import { useEffect, useState } from "react";
import type { MediaItem } from "./types";
import "./MediaPreviewModal.css";

const MIN_ZOOM = 1;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;

export interface MediaPreviewModalProps {
  items: MediaItem[];
  index: number;
  onIndexChange: (index: number) => void;
  onClose: () => void;
  onDownload?: (item: MediaItem) => void;
}

export function MediaPreviewModal({
  items,
  index,
  onIndexChange,
  onClose,
  onDownload,
}: MediaPreviewModalProps) {
  const [zoom, setZoom] = useState(1);
  const [zoomResetIndex, setZoomResetIndex] = useState(index);
  const item = items[index];
  const hasPrev = index > 0;
  const hasNext = index < items.length - 1;

  if (index !== zoomResetIndex) {
    setZoomResetIndex(index);
    setZoom(1);
  }

  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      else if (event.key === "ArrowLeft" && hasPrev) onIndexChange(index - 1);
      else if (event.key === "ArrowRight" && hasNext) onIndexChange(index + 1);
      else if (event.key === "+" || event.key === "=") zoomIn();
      else if (event.key === "-" || event.key === "_") zoomOut();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, hasPrev, hasNext]);

  if (!item) return null;

  const isVideo = item.kind === "video";

  function zoomIn() {
    setZoom((z) => Math.min(MAX_ZOOM, +(z + ZOOM_STEP).toFixed(2)));
  }

  function zoomOut() {
    setZoom((z) => Math.max(MIN_ZOOM, +(z - ZOOM_STEP).toFixed(2)));
  }

  return (
    <div
      className="media-preview-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="media-preview-toolbar">
        <span className="media-preview-counter">
          {index + 1} / {items.length}
        </span>
        <div className="media-preview-actions">
          {!isVideo && (
            <>
              <button
                type="button"
                onClick={zoomOut}
                disabled={zoom <= MIN_ZOOM}
                aria-label="Zoom out"
              >
                −
              </button>
              <span className="media-preview-zoom-level">
                {Math.round(zoom * 100)}%
              </span>
              <button
                type="button"
                onClick={zoomIn}
                disabled={zoom >= MAX_ZOOM}
                aria-label="Zoom in"
              >
                +
              </button>
            </>
          )}
          {onDownload && (
            <button
              type="button"
              onClick={() => onDownload(item)}
              aria-label="Download"
            >
              Download
            </button>
          )}
          <button
            type="button"
            className="media-preview-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
      </div>

      <button
        type="button"
        className="media-preview-nav media-preview-prev"
        onClick={() => hasPrev && onIndexChange(index - 1)}
        disabled={!hasPrev}
        aria-label="Previous"
      >
        ‹
      </button>

      <div className="media-preview-stage">
        {isVideo ? (
          <video src={item.src} controls autoPlay />
        ) : (
          <img
            src={item.src}
            alt={item.alt ?? ""}
            style={{ transform: `scale(${zoom})` }}
            draggable={false}
          />
        )}
      </div>

      <button
        type="button"
        className="media-preview-nav media-preview-next"
        onClick={() => hasNext && onIndexChange(index + 1)}
        disabled={!hasNext}
        aria-label="Next"
      >
        ›
      </button>
    </div>
  );
}
