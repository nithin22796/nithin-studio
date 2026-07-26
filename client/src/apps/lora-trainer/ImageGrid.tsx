import { useState } from "react";
import * as api from "./api";
import type { DatasetImage } from "./types";
import "./ImageGrid.css";

const GRID_COLUMNS = 6;
const VISIBLE_ROWS = 2;
const VISIBLE_LIMIT = GRID_COLUMNS * VISIBLE_ROWS;

export interface ImageGridProps {
  images: DatasetImage[];
  onRemove: (fileManagerId: number) => void;
  secondaryAction?: { label: string; onClick: () => void };
}

function GridCells({
  images,
  onRemove,
}: {
  images: DatasetImage[];
  onRemove: (fileManagerId: number) => void;
}) {
  return (
    <>
      {images.map((img) => (
        <div key={img.fileManagerId} className="lt-grid-cell">
          <img src={api.datasetImageUrl(img.fileManagerId, "inline")} alt={img.name} />
          <button
            type="button"
            className="lt-grid-remove"
            onClick={() => onRemove(img.fileManagerId)}
            aria-label={`Remove ${img.name}`}
          >
            ✕
          </button>
        </div>
      ))}
    </>
  );
}

export function ImageGrid({ images, onRemove, secondaryAction }: ImageGridProps) {
  const [showAll, setShowAll] = useState(false);
  const visible = images.slice(0, VISIBLE_LIMIT);
  const remaining = images.length - visible.length;

  return (
    <>
      <div className="lt-grid">
        <GridCells images={visible} onRemove={onRemove} />
      </div>

      {(remaining > 0 || secondaryAction) && (
        <div className="lt-grid-actions">
          {remaining > 0 && (
            <button type="button" className="lt-show-more" onClick={() => setShowAll(true)}>
              Show more ({remaining} more)
            </button>
          )}
          {secondaryAction && (
            <button type="button" className="lt-show-more" onClick={secondaryAction.onClick}>
              {secondaryAction.label}
            </button>
          )}
        </div>
      )}

      {showAll && (
        <div
          className="lt-grid-modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) setShowAll(false);
          }}
        >
          <div className="lt-grid-modal">
            <div className="lt-grid-modal-header">
              <h3>All images ({images.length})</h3>
              <button type="button" onClick={() => setShowAll(false)} aria-label="Close">
                ✕
              </button>
            </div>
            <div className="lt-grid lt-grid-modal-content">
              <GridCells images={images} onRemove={onRemove} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
