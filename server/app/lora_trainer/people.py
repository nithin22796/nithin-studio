"""Face detection, identity clustering, and person-box cropping for the
"People" dataset step.

Every photo in the dataset is assumed to already contain the person being
trained — this module exists only to handle group shots, where a second
face in frame would otherwise pollute training. For a photo with more than
one face, we match the detected faces against the identity the user picked
and crop the photo down to a wider box around that person; single-face
photos pass through untouched.

Uses a local `insightface` (buffalo_l) model — detector + ArcFace embeddings
only, no landmark/gender modules. Nothing here downloads anything: the model
files must already exist under `MODELS_DIR/buffalo_l/`, same as every other
model in this app.
"""

import io
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image

from app.shared_models import MODELS_DIR, ModelMissingError

IDENTITY_SIMILARITY_THRESHOLD = 0.5

# There's no body detector here, so the "person box" is a heuristic
# expansion of the face bounding box: wide enough either side to keep the
# shoulders in frame, and tall enough below to keep a full-body shot mostly
# intact (matches the dataset's existing full-body/aspect-bucketing guidance).
PERSON_BOX_WIDTH_SCALE = 2.2
PERSON_BOX_TOP_SCALE = 1.0
PERSON_BOX_BOTTOM_SCALE = 5.5

_REQUIRED_FILES = ("det_10g.onnx", "w600k_r50.onnx")

_face_app = None


def _get_face_app():
    global _face_app
    if _face_app is not None:
        return _face_app

    model_dir = MODELS_DIR / "buffalo_l"
    missing = [f for f in _REQUIRED_FILES if not (model_dir / f).is_file()]
    if missing:
        raise ModelMissingError(
            f"missing insightface model files in {model_dir}: {', '.join(missing)}\n"
            "Download from:\n"
            "  https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip\n"
            f"and unzip into: {model_dir}/"
        )

    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l",
        root=str(MODELS_DIR.parent),
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(640, 640))
    _face_app = app
    return app


@dataclass
class Face:
    bbox: list[float]
    embedding: np.ndarray


def detect_faces(content: bytes) -> list[Face]:
    image = Image.open(io.BytesIO(content)).convert("RGB")
    array = np.array(image)[:, :, ::-1]  # RGB -> BGR
    faces = _get_face_app().get(array)
    return [Face(bbox=f.bbox.tolist(), embedding=f.normed_embedding) for f in faces]


@dataclass
class DetectedFace:
    file_id: int
    bbox: list[float]
    embedding: np.ndarray


class DetectionCancelled(Exception):
    pass


def detect_all_faces(
    items: list[tuple[int, bytes]],
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[DetectedFace]:
    faces = []
    total = len(items)
    for i, (file_id, content) in enumerate(items):
        if should_cancel and should_cancel():
            raise DetectionCancelled
        for face in detect_faces(content):
            faces.append(DetectedFace(file_id=file_id, bbox=face.bbox, embedding=face.embedding))
        if on_progress:
            on_progress(i + 1, total)
    return faces


def _bbox_area(bbox: list[float]) -> float:
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def cluster_identities(faces: list[DetectedFace]) -> list[list[int]]:
    """Greedily group face indices into unique identities by embedding
    similarity — same approach as `duplicates.find_duplicate_groups`, just
    with a running centroid instead of a fixed reference per group."""
    clusters: list[list[int]] = []
    centroids: list[np.ndarray] = []
    for i, face in enumerate(faces):
        best_cluster = None
        best_similarity = IDENTITY_SIMILARITY_THRESHOLD
        for c_idx, centroid in enumerate(centroids):
            similarity = float(np.dot(face.embedding, centroid))
            if similarity >= best_similarity:
                best_similarity = similarity
                best_cluster = c_idx
        if best_cluster is None:
            clusters.append([i])
            centroids.append(face.embedding)
        else:
            clusters[best_cluster].append(i)
            members = clusters[best_cluster]
            mean = np.mean([faces[m].embedding for m in members], axis=0)
            centroids[best_cluster] = mean / np.linalg.norm(mean)
    return clusters


def representative_face(faces: list[DetectedFace], member_indices: list[int]) -> DetectedFace:
    return max((faces[i] for i in member_indices), key=lambda f: _bbox_area(f.bbox))


def crop_face_thumbnail(image: Image.Image, bbox: list[float], padding: float = 0.4) -> bytes:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    pad_x, pad_y = w * padding, h * padding
    box = (
        max(0, int(x1 - pad_x)),
        max(0, int(y1 - pad_y)),
        min(image.width, int(x2 + pad_x)),
        min(image.height, int(y2 + pad_y)),
    )
    thumb = image.crop(box)
    buffer = io.BytesIO()
    thumb.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def best_match(targets: list[np.ndarray], faces: list[Face]) -> Face | None:
    """Matches against several reference embeddings at once — a face counts
    as a match if it's close enough to *any* of them, since the same person
    picked from different angles/poses can land in separate identity
    clusters (see the multi-select picker on the client)."""
    best: Face | None = None
    best_similarity = IDENTITY_SIMILARITY_THRESHOLD
    for face in faces:
        similarity = max(float(np.dot(target, face.embedding)) for target in targets)
        if similarity >= best_similarity:
            best_similarity = similarity
            best = face
    return best


def all_matches(targets: list[np.ndarray], faces: list[Face]) -> list[Face]:
    """Like `best_match`, but returns every matching face instead of only the
    closest one. A single photo can contain more than one instance of the
    trained person — a collage of separate shots, for example — and each
    instance should become its own cropped training image rather than only
    the best match being kept. Sorted left-to-right (by bbox x1) so the
    client's review order matches how a person would naturally scan the
    photo."""
    matches = [
        face
        for face in faces
        if max(float(np.dot(target, face.embedding)) for target in targets)
        >= IDENTITY_SIMILARITY_THRESHOLD
    ]
    matches.sort(key=lambda f: f.bbox[0])
    return matches


def person_box(bbox: list[float], image_size: tuple[int, int]) -> list[int]:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    cx = (x1 + x2) / 2
    box_w = w * PERSON_BOX_WIDTH_SCALE
    top = y1 - h * PERSON_BOX_TOP_SCALE
    bottom = y2 + h * PERSON_BOX_BOTTOM_SCALE
    left = cx - box_w / 2
    right = cx + box_w / 2
    img_w, img_h = image_size
    return [
        int(max(0, left)),
        int(max(0, top)),
        int(min(img_w, right)),
        int(min(img_h, bottom)),
    ]
