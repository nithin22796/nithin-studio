import { useEffect, useState } from "react";
import * as api from "./api";
import type { ContentsResponse, FileItem, FolderItem } from "./types";
import "./FilePickerModal.css";

interface Crumb {
  id: number | null;
  name: string;
}

export interface FilePickerModalProps {
  onClose: () => void;
  filter?: (file: FileItem) => boolean;
  title?: string;
  /** Single-select: called immediately when a file is clicked. */
  onSelect?: (file: FileItem) => void;
  /** Multi-select: renders checkboxes + a confirm button instead. */
  onSelectMultiple?: (files: FileItem[]) => void;
}

export function FilePickerModal({
  onClose,
  filter,
  title,
  onSelect,
  onSelectMultiple,
}: FilePickerModalProps) {
  const [path, setPath] = useState<Crumb[]>([{ id: null, name: "Home" }]);
  const currentFolderId = path[path.length - 1].id;
  const [contents, setContents] = useState<ContentsResponse>({ folders: [], files: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Map<number, FileItem>>(new Map());

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- immediate loading feedback on folder change
    setLoading(true);
    setError(null);
    setSelected(new Map());
    api
      .fetchContents(currentFolderId)
      .then(setContents)
      .catch((err) => setError(err instanceof Error ? err.message : "unknown error"))
      .finally(() => setLoading(false));
  }, [currentFolderId]);

  function openFolder(folder: FolderItem) {
    setPath((p) => [...p, { id: folder.id, name: folder.name }]);
  }

  function goToBreadcrumb(index: number) {
    setPath((p) => p.slice(0, index + 1));
  }

  function toggleSelected(file: FileItem) {
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(file.id)) next.delete(file.id);
      else next.set(file.id, file);
      return next;
    });
  }

  const selectableFiles = contents.files.filter((f) => !filter || filter(f));
  const allSelected = selectableFiles.length > 0 && selected.size === selectableFiles.length;

  function toggleSelectAll() {
    setSelected((prev) => {
      if (prev.size === selectableFiles.length) return new Map();
      return new Map(selectableFiles.map((f) => [f.id, f]));
    });
  }

  return (
    <div
      className="file-picker-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="file-picker-dialog">
        <div className="file-picker-header">
          <h3>{title ?? "Choose a file"}</h3>
          <button type="button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="file-picker-toolbar">
          <nav className="file-picker-breadcrumbs">
            {path.map((crumb, i) => (
              <span key={crumb.id ?? "root"}>
                {i > 0 && <span className="file-picker-sep">/</span>}
                <button
                  type="button"
                  disabled={i === path.length - 1}
                  onClick={() => goToBreadcrumb(i)}
                >
                  {crumb.name}
                </button>
              </span>
            ))}
          </nav>

          {onSelectMultiple && selectableFiles.length > 0 && (
            <label className="file-picker-select-all">
              <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
              Select all
            </label>
          )}
        </div>

        {error && <p className="error-message">{error}</p>}
        {loading && <p className="file-picker-loading">Loading...</p>}

        <div className="file-picker-grid">
          {contents.folders.map((folder) => (
            <button
              key={`folder-${folder.id}`}
              type="button"
              className="file-picker-tile"
              onClick={() => openFolder(folder)}
            >
              <div className="file-picker-thumb file-picker-thumb-folder">📁</div>
              <span className="file-picker-tile-name" title={folder.name}>
                {folder.name}
              </span>
            </button>
          ))}
          {contents.files.map((file) => {
            const selectable = !filter || filter(file);
            const checked = selected.has(file.id);
            const isImage = file.content_type.startsWith("image/");
            return (
              <button
                key={`file-${file.id}`}
                type="button"
                className={`file-picker-tile${checked ? " selected" : ""}`}
                disabled={!selectable}
                onClick={() =>
                  onSelectMultiple ? toggleSelected(file) : onSelect?.(file)
                }
              >
                <div className="file-picker-thumb">
                  {isImage ? (
                    <img src={api.fileContentUrl(file.id, "inline")} alt="" loading="lazy" />
                  ) : (
                    <span className="file-picker-thumb-icon">📄</span>
                  )}
                  {onSelectMultiple && (
                    <input
                      type="checkbox"
                      className="file-picker-tile-checkbox"
                      checked={checked}
                      disabled={!selectable}
                      onChange={() => toggleSelected(file)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  )}
                </div>
                <span className="file-picker-tile-name" title={file.name}>
                  {file.name}
                </span>
              </button>
            );
          })}
          {contents.folders.length === 0 && contents.files.length === 0 && !loading && (
            <p className="file-picker-empty">Nothing here yet.</p>
          )}
        </div>

        {onSelectMultiple && (
          <div className="file-picker-footer">
            <button
              type="button"
              disabled={selected.size === 0}
              onClick={() => onSelectMultiple([...selected.values()])}
            >
              Add {selected.size} file{selected.size === 1 ? "" : "s"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
