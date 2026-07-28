import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { MediaPreviewModal } from "../../shared/media-preview";
import type { MediaItem } from "../../shared/media-preview";
import { downloadUrl } from "../../shared/download";
import { UploadProgressRing } from "../../shared/upload-progress-ring/UploadProgressRing";
import type { UploadItem } from "../../shared/upload-progress-ring/UploadProgressRing";
import * as api from "./api";
import type { ContentsResponse, FileItem, FolderItem } from "./types";
import "./FileManagerApp.css";

interface Crumb {
  id: number | null;
  name: string;
}

type Row =
  { kind: "folder"; folder: FolderItem } | { kind: "file"; file: FileItem };

// Firing one request per selected file via Promise.all works for a handful
// of files, but a "select all" over thousands (a real case here — 5000+
// item folders exist) opens that many connections at once and the browser
// just gives up with a generic "Failed to fetch". Bounded concurrency
// keeps a fixed number of requests in flight, and continues past individual
// failures (e.g. a file already deleted) instead of aborting the whole
// batch like Promise.all would.
const BULK_CONCURRENCY = 8;

async function runBulk(
  ids: number[],
  action: (id: number) => Promise<void>,
  onProgress: (done: number) => void,
): Promise<number> {
  let index = 0;
  let done = 0;
  let succeeded = 0;

  async function worker() {
    for (;;) {
      const i = index++;
      if (i >= ids.length) return;
      try {
        await action(ids[i]);
        succeeded++;
      } catch {
        // Keep going — a bulk action over thousands of items shouldn't
        // abort entirely because one file failed (e.g. already deleted).
      }
      done++;
      onProgress(done);
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(BULK_CONCURRENCY, ids.length) }, () =>
      worker(),
    ),
  );
  return succeeded;
}

export function FileManagerApp() {
  const [path, setPath] = useState<Crumb[]>([{ id: null, name: "Home" }]);
  const currentFolderId = path[path.length - 1].id;

  const [contents, setContents] = useState<ContentsResponse>({
    folders: [],
    files: [],
  });
  const [allFolders, setAllFolders] = useState<FolderItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<ContentsResponse | null>(
    null,
  );
  const [newFolderName, setNewFolderName] = useState("");
  const [newFolderModalOpen, setNewFolderModalOpen] = useState(false);
  const uploadFilesInputRef = useRef<HTMLInputElement>(null);
  const uploadFolderInputRef = useRef<HTMLInputElement>(null);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  // Counts nested dragenter/dragleave pairs (every child element the
  // pointer passes over fires its own enter/leave) so the drop highlight
  // only turns off once the pointer has actually left the whole drop zone,
  // not just moved from a card onto the gap between cards.
  const dragCounter = useRef(0);
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [lastFolderId, setLastFolderId] = useState(currentFolderId);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkProgress, setBulkProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsRef = useRef<HTMLDivElement>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Which card's "⋮" actions menu is open, plus a viewport-anchored position
  // for it — rendered via a portal (not inline in the card) so it isn't
  // clipped by the grid's own `overflow: auto` scroll container.
  const [openRowMenu, setOpenRowMenu] = useState<{
    key: string;
    top: number;
    right: number;
  } | null>(null);
  // How far the grid's top edge sits from the top of the document — used
  // only so the drop zone's min-height can fill the rest of the viewport
  // (see the inline style below), not for any virtualization math (there
  // is none: every item just renders as a plain CSS grid cell, see
  // <FileGrid> — @tanstack/react-virtual's `lanes` mode had a real bug
  // that silently broke positioning for large folders, and a table no
  // longer forces a bounded-height scrollbox that virtualization would've
  // been needed for anyway). Recomputed after every render (cheap: just a
  // rect read) rather than only once, since content above the grid
  // (breadcrumbs wrapping, the bulk-actions bar, an error message) can
  // change height and shift the grid down.
  const [containerTop, setContainerTop] = useState(0);
  // Deliberately no dependency array — this must re-measure after every
  // render, since content above the grid can change height on its own
  // schedule (not tied to any single prop/state this component could list).
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally runs every render, not a one-time or dependency-driven sync
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    setContainerTop(el.getBoundingClientRect().top + window.scrollY);
  });

  if (currentFolderId !== lastFolderId) {
    setLastFolderId(currentFolderId);
    setSearchResults(null);
    setSearchQuery("");
    setSelected(new Set());
  }

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [items, folders] = await Promise.all([
        api.fetchContents(currentFolderId),
        api.listAllFolders(),
      ]);
      setContents(items);
      setAllFolders(folders);
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time fetch of a folder's contents when navigating into it, not a render-driven state sync
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFolderId]);

  useEffect(() => {
    if (!actionsOpen) return;
    function handleClickOutside(event: MouseEvent) {
      if (
        actionsRef.current &&
        !actionsRef.current.contains(event.target as Node)
      ) {
        setActionsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [actionsOpen]);

  useEffect(() => {
    if (!openRowMenu) return;
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Element;
      if (!target.closest(".fm-row-menu, .fm-card-menu-toggle")) {
        setOpenRowMenu(null);
      }
    }
    // Closes on scroll too, since the menu's position is a one-time snapshot
    // of the toggle button's location — it wouldn't track the card as the
    // page scrolled underneath it. The grid now scrolls with the window
    // (not an inner scrollbox), so this listens on window instead.
    function handleScroll() {
      setOpenRowMenu(null);
    }
    document.addEventListener("mousedown", handleClickOutside);
    window.addEventListener("scroll", handleScroll);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      window.removeEventListener("scroll", handleScroll);
    };
  }, [openRowMenu]);

  function toggleRowMenu(
    key: string,
    event: React.MouseEvent<HTMLButtonElement>,
  ) {
    if (openRowMenu?.key === key) {
      setOpenRowMenu(null);
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    setOpenRowMenu({
      key,
      top: rect.bottom + 4,
      right: window.innerWidth - rect.right,
    });
  }

  const displayed = searchResults ?? contents;
  const allFileIds = displayed.files.map((f) => f.id);
  const selectedCount = [...selected].filter((id) =>
    allFileIds.includes(id),
  ).length;
  const allSelected =
    allFileIds.length > 0 && selectedCount === allFileIds.length;

  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = selectedCount > 0 && !allSelected;
    }
  }, [selectedCount, allSelected]);

  const previewItems: MediaItem[] = displayed.files
    .filter(
      (f) =>
        f.content_type.startsWith("image/") ||
        f.content_type.startsWith("video/"),
    )
    .map((f) => ({
      id: String(f.id),
      src: api.fileContentUrl(f.id, "inline"),
      alt: f.name,
      downloadName: f.name,
      kind: f.content_type.startsWith("video/") ? "video" : "image",
    }));

  const rows: Row[] = useMemo(
    () => [
      ...displayed.folders.map((folder): Row => ({ kind: "folder", folder })),
      ...displayed.files.map((file): Row => ({ kind: "file", file })),
    ],
    [displayed],
  );

  function openFolder(folder: FolderItem) {
    setPath((p) => [...p, { id: folder.id, name: folder.name }]);
  }

  function goToBreadcrumb(index: number) {
    setPath((p) => p.slice(0, index + 1));
  }

  async function withErrorHandling(action: () => Promise<void>) {
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    }
  }

  function openNewFolderModal() {
    setActionsOpen(false);
    setNewFolderName("");
    setNewFolderModalOpen(true);
  }

  function closeNewFolderModal() {
    setNewFolderModalOpen(false);
  }

  function handleCreateFolder() {
    if (!newFolderName.trim()) return;
    void withErrorHandling(async () => {
      await api.createFolder(newFolderName.trim(), currentFolderId);
      setNewFolderModalOpen(false);
      await refresh();
    });
  }

  // Tracks a single file's upload in `uploads` (rendered as the
  // UploadProgressRing overlay) while it's in flight, so drag-and-drop and
  // the file pickers all show the same spinner regardless of entry point.
  async function uploadFileTracked(
    file: File,
    folderId: number | null,
  ): Promise<FileItem> {
    const trayId = `${Date.now()}-${Math.random()}-${file.name}`;
    setUploads((prev) => [
      ...prev,
      { id: trayId, name: file.name, progress: 0, status: "uploading" as const },
    ]);
    try {
      const result = await api.uploadFile(file, folderId, (fraction) => {
        setUploads((prev) =>
          prev.map((u) => (u.id === trayId ? { ...u, progress: fraction } : u)),
        );
      });
      setUploads((prev) =>
        prev.map((u) => (u.id === trayId ? { ...u, status: "done", progress: 1 } : u)),
      );
      return result;
    } catch (err) {
      setUploads((prev) =>
        prev.map((u) =>
          u.id === trayId
            ? { ...u, status: "error", error: err instanceof Error ? err.message : "upload failed" }
            : u,
        ),
      );
      throw err;
    }
  }

  function handleUpload(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setActionsOpen(false);
    void withErrorHandling(async () => {
      for (const file of Array.from(fileList)) {
        await uploadFileTracked(file, currentFolderId);
      }
      await refresh();
    });
  }

  // Resolves (creating as needed) the folder chain named by `segments`
  // under `baseFolderId`, caching each resolved id by its path-so-far so
  // uploading many files from the same subfolder doesn't call
  // getOrCreateFolder once per file for folders it already just created.
  async function resolveFolderPath(
    segments: string[],
    baseFolderId: number | null,
    cache: Map<string, number>,
  ): Promise<number | null> {
    let parentId = baseFolderId;
    let pathSoFar = "";
    for (const segment of segments) {
      pathSoFar = pathSoFar ? `${pathSoFar}/${segment}` : segment;
      const cached = cache.get(pathSoFar);
      if (cached !== undefined) {
        parentId = cached;
        continue;
      }
      const folder = await api.getOrCreateFolder(segment, parentId);
      cache.set(pathSoFar, folder.id);
      parentId = folder.id;
    }
    return parentId;
  }

  function handleUploadFolder(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setActionsOpen(false);
    void withErrorHandling(async () => {
      const cache = new Map<string, number>();
      for (const file of Array.from(fileList)) {
        const relPath =
          (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
          file.name;
        const segments = relPath.split("/");
        segments.pop(); // drop the filename itself, keep only directory segments
        const targetFolderId =
          segments.length > 0
            ? await resolveFolderPath(segments, currentFolderId, cache)
            : currentFolderId;
        await uploadFileTracked(file, targetFolderId);
      }
      await refresh();
    });
  }

  // Walks a dropped FileSystemEntry (file or directory), recursing into
  // subdirectories, and collects every file found along with the directory
  // path it lived at relative to whatever was dropped — mirrors what the
  // "Upload folder(s)" input gets for free via `webkitRelativePath`, since
  // drag-and-drop's File objects don't carry that property themselves.
  async function collectEntryFiles(
    entry: FileSystemEntry,
    dirPath: string,
    out: { file: File; dirPath: string }[],
  ): Promise<void> {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      );
      out.push({ file, dirPath });
      return;
    }
    if (!entry.isDirectory) return;
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const children: FileSystemEntry[] = [];
    // readEntries only returns entries in batches (browser-defined size) —
    // it must be called repeatedly until it returns an empty batch, a
    // single call is not guaranteed to return everything.
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
        reader.readEntries(resolve, reject),
      );
      if (batch.length === 0) break;
      children.push(...batch);
    }
    const childDirPath = dirPath ? `${dirPath}/${entry.name}` : entry.name;
    for (const child of children) {
      await collectEntryFiles(child, childDirPath, out);
    }
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDraggingOver(false);
    dragCounter.current = 0;

    const items = event.dataTransfer.items;
    const entries = items
      ? Array.from(items)
          .map((item) => item.webkitGetAsEntry?.())
          .filter((entry): entry is FileSystemEntry => entry !== null)
      : [];

    if (entries.length === 0) {
      // No FileSystemEntry support (or nothing draggable found) — fall back
      // to a flat upload of whatever files are directly on the drop event.
      handleUpload(event.dataTransfer.files);
      return;
    }

    void withErrorHandling(async () => {
      const collected: { file: File; dirPath: string }[] = [];
      for (const entry of entries) {
        await collectEntryFiles(entry, "", collected);
      }
      const cache = new Map<string, number>();
      for (const { file, dirPath } of collected) {
        const targetFolderId = dirPath
          ? await resolveFolderPath(dirPath.split("/"), currentFolderId, cache)
          : currentFolderId;
        await uploadFileTracked(file, targetFolderId);
      }
      await refresh();
    });
  }

  function handleDragOver(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  function handleDragEnter(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragCounter.current += 1;
    setIsDraggingOver(true);
  }

  function handleDragLeave(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragCounter.current -= 1;
    if (dragCounter.current <= 0) {
      dragCounter.current = 0;
      setIsDraggingOver(false);
    }
  }

  function handleRenameFolder(folder: FolderItem) {
    const name = window.prompt("Rename folder", folder.name);
    if (!name || name === folder.name) return;
    void withErrorHandling(async () => {
      await api.renameFolder(folder.id, name);
      await refresh();
    });
  }

  function handleDeleteFolder(folder: FolderItem) {
    if (!window.confirm(`Delete folder "${folder.name}"?`)) return;
    void withErrorHandling(async () => {
      await api.deleteFolder(folder.id);
      await refresh();
    });
  }

  function handleRenameFile(file: FileItem) {
    const name = window.prompt("Rename file", file.name);
    if (!name || name === file.name) return;
    void withErrorHandling(async () => {
      await api.renameFile(file.id, name);
      await refresh();
    });
  }

  function handleMoveFile(file: FileItem, folderId: number | null) {
    void withErrorHandling(async () => {
      await api.moveFile(file.id, folderId);
      await refresh();
    });
  }

  function handleDeleteFile(file: FileItem) {
    if (!window.confirm(`Delete "${file.name}"?`)) return;
    void withErrorHandling(async () => {
      await api.deleteFile(file.id);
      await refresh();
    });
  }

  function toggleSelected(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelected((prev) => {
      if (allSelected) {
        const next = new Set(prev);
        for (const id of allFileIds) next.delete(id);
        return next;
      }
      return new Set([...prev, ...allFileIds]);
    });
  }

  function handleBulkDelete() {
    if (!window.confirm(`Delete ${selectedCount} selected file(s)?`)) return;
    const ids = [...selected];
    void withErrorHandling(async () => {
      setBulkProgress({ done: 0, total: ids.length });
      const succeeded = await runBulk(
        ids,
        (id) => api.deleteFile(id),
        (done) => setBulkProgress({ done, total: ids.length }),
      );
      setBulkProgress(null);
      setSelected(new Set());
      await refresh();
      if (succeeded < ids.length) {
        setError(`Deleted ${succeeded} of ${ids.length} — the rest failed.`);
      }
    });
  }

  function handleBulkMove(folderId: number | null) {
    const ids = [...selected];
    void withErrorHandling(async () => {
      setBulkProgress({ done: 0, total: ids.length });
      const succeeded = await runBulk(
        ids,
        (id) => api.moveFile(id, folderId).then(() => undefined),
        (done) => setBulkProgress({ done, total: ids.length }),
      );
      setBulkProgress(null);
      setSelected(new Set());
      await refresh();
      if (succeeded < ids.length) {
        setError(`Moved ${succeeded} of ${ids.length} — the rest failed.`);
      }
    });
  }

  // Live search-as-you-type (Google Drive-style — no separate Search
  // button to click). Debounced so fast typing doesn't fire a request per
  // keystroke; clearing the box drops back to the current folder's normal
  // contents immediately, no debounce needed for that direction.
  useEffect(() => {
    const query = searchQuery.trim();
    if (!query) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- dropping back to normal folder contents when the box is cleared, not a debounced request; no reason to delay it
      setSearchResults(null);
      return;
    }
    const timer = setTimeout(() => {
      void withErrorHandling(async () => {
        setSearchResults(await api.searchItems(query));
        setSelected(new Set());
      });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  return (
    <div className="file-manager">
      <Link className="back-link" to="/services">
        ← Services
      </Link>
      <h2>file-manager</h2>

      <div className="fm-header-row">
        <div className="fm-header-left">
          {searchResults ? (
            <span className="fm-search-label">
              Search results
              <button
                type="button"
                className="fm-clear-search"
                onClick={() => {
                  setSearchResults(null);
                  setSearchQuery("");
                }}
              >
                Clear
              </button>
            </span>
          ) : (
            <nav className="fm-breadcrumbs">
              {path.map((crumb, i) => (
                <span key={crumb.id ?? "root"}>
                  {i > 0 && <span className="fm-breadcrumb-sep">/</span>}
                  <button
                    type="button"
                    className="fm-breadcrumb"
                    disabled={i === path.length - 1}
                    onClick={() => goToBreadcrumb(i)}
                  >
                    {crumb.name}
                  </button>
                </span>
              ))}
            </nav>
          )}
          <span className="fm-item-count">
            {displayed.folders.length + displayed.files.length}{" "}
            {searchResults ? "result" : "item"}
            {displayed.folders.length + displayed.files.length === 1 ? "" : "s"}
          </span>
          {allFileIds.length > 0 && (
            <label className="fm-select-all">
              <input
                type="checkbox"
                ref={selectAllRef}
                checked={allSelected}
                onChange={toggleSelectAll}
              />
              Select all
            </label>
          )}
        </div>
      </div>

      <div className="fm-toolbar-row">
        <div className="fm-search">
          <input
            type="search"
            placeholder="Search files and folders..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {!searchResults && (
          <div className="fm-actions-bar" ref={actionsRef}>
            <button
              type="button"
              className="fm-actions-toggle"
              aria-label="New"
              onClick={() => setActionsOpen((open) => !open)}
            >
              + New
            </button>
            {actionsOpen && (
              <div className="fm-actions-menu">
                <button type="button" onClick={openNewFolderModal}>
                  New folder
                </button>
                <button
                  type="button"
                  onClick={() => uploadFilesInputRef.current?.click()}
                >
                  Upload file(s)
                </button>
                <button
                  type="button"
                  onClick={() => uploadFolderInputRef.current?.click()}
                >
                  Upload folder(s)
                </button>
              </div>
            )}

            <input
              ref={uploadFilesInputRef}
              type="file"
              multiple
              className="fm-hidden-input"
              onChange={(e) => {
                handleUpload(e.target.files);
                e.target.value = "";
              }}
            />
            <input
              ref={(el) => {
                if (el) {
                  (
                    el as HTMLInputElement & { webkitdirectory: boolean }
                  ).webkitdirectory = true;
                }
                uploadFolderInputRef.current = el;
              }}
              type="file"
              multiple
              className="fm-hidden-input"
              onChange={(e) => {
                handleUploadFolder(e.target.files);
                e.target.value = "";
              }}
            />
          </div>
        )}
      </div>

      <hr className="fm-divider" />

      {newFolderModalOpen && (
        <div
          className="fm-modal-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeNewFolderModal();
          }}
        >
          <div className="fm-modal">
            <input
              type="text"
              autoFocus
              placeholder="New folder name"
              value={newFolderName}
              onChange={(e) => setNewFolderName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreateFolder();
                if (e.key === "Escape") closeNewFolderModal();
              }}
            />
            <div className="fm-modal-actions">
              <button
                type="button"
                className="fm-modal-cancel"
                onClick={closeNewFolderModal}
              >
                Cancel
              </button>
              <button
                type="button"
                className="fm-modal-create"
                disabled={!newFolderName.trim()}
                onClick={handleCreateFolder}
              >
                Create folder
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedCount > 0 && (
        <div className="fm-bulk-bar">
          {bulkProgress ? (
            <span>
              Working: {bulkProgress.done}/{bulkProgress.total}…
            </span>
          ) : (
            <>
              <span>{selectedCount} selected</span>
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value === "") return;
                  handleBulkMove(
                    e.target.value === "home" ? null : Number(e.target.value),
                  );
                }}
              >
                <option value="" disabled>
                  Move to...
                </option>
                <option value="home">Home</option>
                {allFolders.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
              <button type="button" onClick={handleBulkDelete}>
                Delete selected
              </button>
              <button
                type="button"
                className="fm-bulk-clear"
                onClick={() => setSelected(new Set())}
              >
                Clear selection
              </button>
            </>
          )}
        </div>
      )}

      {error && <p className="error-message">{error}</p>}
      {loading && <p className="fm-loading">Loading...</p>}

      <FileGrid
        rows={rows}
        containerTop={containerTop}
        containerRef={scrollRef}
        isDraggingOver={isDraggingOver}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        loading={loading}
        openRowMenu={openRowMenu}
        toggleRowMenu={toggleRowMenu}
        setOpenRowMenu={setOpenRowMenu}
        selected={selected}
        toggleSelected={toggleSelected}
        openFolder={openFolder}
        handleRenameFolder={handleRenameFolder}
        handleDeleteFolder={handleDeleteFolder}
        handleRenameFile={handleRenameFile}
        handleMoveFile={handleMoveFile}
        handleDeleteFile={handleDeleteFile}
        allFolders={allFolders}
        setPreviewIndex={setPreviewIndex}
        previewItems={previewItems}
      />

      {previewIndex !== null && (
        <MediaPreviewModal
          items={previewItems}
          index={previewIndex}
          onIndexChange={setPreviewIndex}
          onClose={() => setPreviewIndex(null)}
          onDownload={(item) =>
            void downloadUrl(item.src, item.downloadName ?? item.id)
          }
        />
      )}

      {uploads.length > 0 && (
        <div className="fm-upload-backdrop">
          <UploadProgressRing uploads={uploads} onExited={() => setUploads([])} />
        </div>
      )}
    </div>
  );
}

interface FileGridProps {
  rows: Row[];
  containerTop: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  isDraggingOver: boolean;
  onDragEnter: (event: React.DragEvent<HTMLDivElement>) => void;
  onDragOver: (event: React.DragEvent<HTMLDivElement>) => void;
  onDragLeave: (event: React.DragEvent<HTMLDivElement>) => void;
  onDrop: (event: React.DragEvent<HTMLDivElement>) => void;
  loading: boolean;
  openRowMenu: { key: string; top: number; right: number } | null;
  toggleRowMenu: (
    key: string,
    event: React.MouseEvent<HTMLButtonElement>,
  ) => void;
  setOpenRowMenu: (
    value: { key: string; top: number; right: number } | null,
  ) => void;
  selected: Set<number>;
  toggleSelected: (id: number) => void;
  openFolder: (folder: FolderItem) => void;
  handleRenameFolder: (folder: FolderItem) => void;
  handleDeleteFolder: (folder: FolderItem) => void;
  handleRenameFile: (file: FileItem) => void;
  handleMoveFile: (file: FileItem, folderId: number | null) => void;
  handleDeleteFile: (file: FileItem) => void;
  allFolders: FolderItem[];
  setPreviewIndex: (index: number) => void;
  previewItems: MediaItem[];
}

function FileGrid({
  rows,
  containerTop,
  containerRef,
  isDraggingOver,
  onDragEnter,
  onDragOver,
  onDragLeave,
  onDrop,
  loading,
  openRowMenu,
  toggleRowMenu,
  setOpenRowMenu,
  selected,
  toggleSelected,
  openFolder,
  handleRenameFolder,
  handleDeleteFolder,
  handleRenameFile,
  handleMoveFile,
  handleDeleteFile,
  allFolders,
  setPreviewIndex,
  previewItems,
}: FileGridProps) {
  return (
    <div
      className={`fm-grid-scroll${isDraggingOver ? " fm-drag-over" : ""}`}
      ref={containerRef}
      // Fills the rest of the viewport below the divider, not just however
      // tall the current folder's contents happen to be — so dropping
      // anywhere in that empty space still hits the drop zone, same as
      // Google Drive. `containerTop` is this container's distance from the
      // top of the page, so 100vh minus that is what's left below it.
      style={{ minHeight: `calc(100vh - ${containerTop}px)` }}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {isDraggingOver && (
        <div className="fm-drop-overlay">
          <p>Drop to upload</p>
        </div>
      )}

      {rows.length === 0 && !loading && (
        <p className="fm-empty">Nothing here yet.</p>
      )}

      {/* Plain CSS grid — no virtualization. Every item is a real DOM node;
          @tanstack/react-virtual's `lanes` mode had a real bug that
          silently broke positioning for large folders (see the removal
          note in FileManagerApp), and a plain grid sidesteps that whole
          class of bug entirely. Thumbnails use `loading="lazy"` so the
          browser doesn't actually fetch/decode off-screen images. */}
      <div className="fm-grid-list">
        {rows.map((row) => {
          if (row.kind === "folder") {
            const folder = row.folder;
            const menuKey = `grid-folder-${folder.id}`;
            return (
              <div key={menuKey} className="fm-card-wrap">
                <div className="fm-card">
                  <button
                    type="button"
                    className="fm-card-menu-toggle"
                    aria-label="Folder actions"
                    onClick={(e) => toggleRowMenu(menuKey, e)}
                  >
                    ⋮
                  </button>
                  {openRowMenu?.key === menuKey &&
                    createPortal(
                      <div
                        className="fm-row-menu"
                        style={{
                          top: openRowMenu.top,
                          right: openRowMenu.right,
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => {
                            setOpenRowMenu(null);
                            handleRenameFolder(folder);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setOpenRowMenu(null);
                            handleDeleteFolder(folder);
                          }}
                        >
                          Delete
                        </button>
                      </div>,
                      document.body,
                    )}
                  <button
                    type="button"
                    className="fm-card-body"
                    onClick={() => openFolder(folder)}
                  >
                    <span className="fm-card-thumb fm-card-thumb-icon">📁</span>
                    <span className="fm-card-name">{folder.name}</span>
                  </button>
                </div>
              </div>
            );
          }

          const file = row.file;
          const isImage = file.content_type.startsWith("image/");
          const isVideo = file.content_type.startsWith("video/");
          const previewIdx = previewItems.findIndex(
            (it) => it.id === String(file.id),
          );
          const menuKey = `grid-file-${file.id}`;
          return (
            <div key={menuKey} className="fm-card-wrap">
              <div className="fm-card">
                <input
                  type="checkbox"
                  className="fm-card-check"
                  checked={selected.has(file.id)}
                  onChange={() => toggleSelected(file.id)}
                  aria-label={`Select ${file.name}`}
                />
                <button
                  type="button"
                  className="fm-card-menu-toggle"
                  aria-label="File actions"
                  onClick={(e) => toggleRowMenu(menuKey, e)}
                >
                  ⋮
                </button>
                {openRowMenu?.key === menuKey &&
                  createPortal(
                    <div
                      className="fm-row-menu"
                      style={{
                        top: openRowMenu.top,
                        right: openRowMenu.right,
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => {
                          setOpenRowMenu(null);
                          void downloadUrl(
                            api.fileContentUrl(file.id),
                            file.name,
                          );
                        }}
                      >
                        Download
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setOpenRowMenu(null);
                          handleRenameFile(file);
                        }}
                      >
                        Rename
                      </button>
                      <label className="fm-row-menu-move">
                        Move to
                        <select
                          value={file.folder_id ?? ""}
                          onChange={(e) => {
                            setOpenRowMenu(null);
                            handleMoveFile(
                              file,
                              e.target.value === ""
                                ? null
                                : Number(e.target.value),
                            );
                          }}
                        >
                          <option value="">Home</option>
                          {allFolders.map((f) => (
                            <option key={f.id} value={f.id}>
                              {f.name}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        onClick={() => {
                          setOpenRowMenu(null);
                          handleDeleteFile(file);
                        }}
                      >
                        Delete
                      </button>
                    </div>,
                    document.body,
                  )}
                <button
                  type="button"
                  className="fm-card-body"
                  onClick={() =>
                    isImage || isVideo ? setPreviewIndex(previewIdx) : undefined
                  }
                >
                  {isImage ? (
                    <img
                      className="fm-card-thumb fm-card-thumb-img"
                      src={api.fileContentUrl(file.id, "inline")}
                      alt={file.name}
                      loading="lazy"
                    />
                  ) : (
                    <span className="fm-card-thumb fm-card-thumb-icon">
                      {isVideo ? "🎬" : "📄"}
                    </span>
                  )}
                  <span className="fm-card-name" title={file.name}>
                    {file.name}
                  </span>
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
