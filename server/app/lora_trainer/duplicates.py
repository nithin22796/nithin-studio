import io

import imagehash
from PIL import Image

# Hamming distance threshold on a 64-bit perceptual hash below which two
# images are considered near-duplicates (0 = identical hash; ~8 tolerates
# minor recompression/resizing while still catching burst-mode near-repeats).
SIMILARITY_THRESHOLD = 8


def phash(content: bytes) -> imagehash.ImageHash:
    image = Image.open(io.BytesIO(content)).convert("RGB")
    return imagehash.phash(image)


def group_hashes(hashes: list[tuple[int, imagehash.ImageHash]]) -> list[list[int]]:
    """Group file ids whose hashes are near-duplicates of each other.

    Returns groups of 2+ ids; ids with no similar match anywhere aren't
    included in any group.
    """
    groups: list[list[int]] = []
    used: set[int] = set()
    for i, (id_a, hash_a) in enumerate(hashes):
        if id_a in used:
            continue
        group = [id_a]
        for id_b, hash_b in hashes[i + 1 :]:
            if id_b in used:
                continue
            if hash_a - hash_b <= SIMILARITY_THRESHOLD:
                group.append(id_b)
                used.add(id_b)
        if len(group) > 1:
            used.add(id_a)
            groups.append(group)
    return groups


def find_duplicate_groups(items: list[tuple[int, bytes]]) -> list[list[int]]:
    """Group file ids whose images are near-duplicates of each other.

    `items` is a list of (file_manager_id, image_bytes). See `group_hashes`
    for the grouping semantics.
    """
    hashes = [(file_id, phash(content)) for file_id, content in items]
    return group_hashes(hashes)
