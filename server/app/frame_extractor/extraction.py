from pathlib import Path

import cv2


def extract_frames(video_path: Path, output_dir: Path, interval_seconds: float) -> list[str]:
    """Extract one frame every `interval_seconds` of video, saving JPEGs to output_dir.

    Returns the list of written filenames, in order.
    """
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("could not open video file")

    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        if fps <= 0:
            raise ValueError("could not determine video frame rate")

        frame_step = max(1, round(fps * interval_seconds))
        output_dir.mkdir(parents=True, exist_ok=True)

        filenames: list[str] = []
        frame_index = 0
        saved_index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % frame_step == 0:
                filename = f"frame_{saved_index:04d}.jpg"
                cv2.imwrite(str(output_dir / filename), frame)
                filenames.append(filename)
                saved_index += 1
            frame_index += 1

        return filenames
    finally:
        capture.release()
