import { useState } from "react";
import { MediaPreviewModal } from "../../shared/media-preview";
import type { MediaItem } from "../../shared/media-preview";
import { downloadUrl } from "../../shared/download";
import { downloadSelectedFrames } from "./downloadSelected";
import "./FrameGallery.css";

const PAGE_SIZE = 20;

function toFilename(path: string): string {
  return path.split("/").pop() ?? path;
}

export interface FrameGalleryProps {
  apiUrl: string;
  jobId: string;
  frames: string[];
}

export function FrameGallery({ apiUrl, jobId, frames }: FrameGalleryProps) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [downloading, setDownloading] = useState(false);

  const items: MediaItem[] = frames.map((path) => ({
    id: path,
    src: `${apiUrl}${path}`,
    alt: toFilename(path),
    downloadName: toFilename(path),
  }));

  function toggleSelected(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function handleDownloadAll() {
    setDownloading(true);
    try {
      await downloadUrl(
        `${apiUrl}/frame-extractor/jobs/${jobId}/download`,
        `${jobId}-frames.zip`,
      );
    } finally {
      setDownloading(false);
    }
  }

  async function handleDownloadSelected() {
    setDownloading(true);
    try {
      await downloadSelectedFrames(
        apiUrl,
        jobId,
        [...selected].map(toFilename),
      );
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="results">
      <div className="results-header">
        <h2>{frames.length} frames extracted</h2>
        <div className="results-actions">
          <button
            type="button"
            onClick={handleDownloadSelected}
            disabled={selected.size === 0 || downloading}
          >
            Download selected ({selected.size})
          </button>
          <button type="button" onClick={handleDownloadAll} disabled={downloading}>
            Download all
          </button>
        </div>
      </div>

      <ul className="frame-grid">
        {frames.slice(0, visibleCount).map((path, i) => {
          const filename = toFilename(path);
          const isSelected = selected.has(path);
          return (
            <li key={path} className={isSelected ? "frame-card selected" : "frame-card"}>
              <label className="frame-select" onClick={(e) => e.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleSelected(path)}
                />
              </label>
              <button
                type="button"
                className="frame-download"
                aria-label={`Download ${filename}`}
                onClick={(e) => {
                  e.stopPropagation();
                  void downloadUrl(`${apiUrl}${path}`, filename);
                }}
              >
                ⬇
              </button>
              <img
                src={`${apiUrl}${path}`}
                alt={filename}
                onClick={() => setPreviewIndex(i)}
              />
            </li>
          );
        })}
      </ul>

      {visibleCount < frames.length && (
        <button
          type="button"
          className="show-more"
          onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
        >
          Show more ({frames.length - visibleCount} remaining)
        </button>
      )}

      {previewIndex !== null && (
        <MediaPreviewModal
          items={items}
          index={previewIndex}
          onIndexChange={setPreviewIndex}
          onClose={() => setPreviewIndex(null)}
          onDownload={(item) => void downloadUrl(item.src, item.downloadName ?? item.id)}
        />
      )}
    </section>
  );
}
