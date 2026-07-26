from pathlib import Path

import cv2
import numpy as np
import pytest

from app.frame_extractor.extraction import extract_frames


def make_video(path: Path, fps: int, num_frames: int) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (16, 16))
    for i in range(num_frames):
        frame = np.full((16, 16, 3), i % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_extract_frames_at_interval(tmp_path: Path):
    video_path = tmp_path / "video.mp4"
    make_video(video_path, fps=10, num_frames=30)

    output_dir = tmp_path / "frames"
    filenames = extract_frames(video_path, output_dir, interval_seconds=1.0)

    # ~30 frames at 10fps over 1s interval -> one frame per 10 frames -> 3 frames
    assert filenames == ["frame_0000.jpg", "frame_0001.jpg", "frame_0002.jpg"]
    for name in filenames:
        assert (output_dir / name).is_file()


def test_extract_frames_invalid_path(tmp_path: Path):
    with pytest.raises(ValueError):
        extract_frames(tmp_path / "missing.mp4", tmp_path / "out", interval_seconds=1.0)
