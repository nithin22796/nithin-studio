import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import * as fileManagerApi from "../file-manager/api";
import type { FolderItem } from "../file-manager/types";
import { MediaPreviewModal } from "../../shared/media-preview";
import type { MediaItem } from "../../shared/media-preview";
import { PercentRing } from "../../shared/percent-ring/PercentRing";
import {
  cancelImport,
  discardPreviewJob,
  getImportJob,
  getPreviewJob,
  startImport,
  startPreview,
  tempImageSrc,
} from "./api";
import type { ImportJob, PreviewImage, PreviewJob } from "./types";
import "./ImageImporterApp.css";

const POLL_INTERVAL_MS = 1000;

// Imported urls are always images, so the destination picker should never
// offer a folder that lives under "Videos" — walks each folder's parent
// chain rather than just checking its immediate parent, so subfolders
// nested anywhere under Videos are excluded too, not just direct children.
function isUnderVideosFolder(folder: FolderItem, byId: Map<number, FolderItem>): boolean {
  let current: FolderItem | undefined = folder;
  while (current) {
    if (current.name.toLowerCase() === "videos") return true;
    current = current.parent_id === null ? undefined : byId.get(current.parent_id);
  }
  return false;
}

function percentOf(progress: number, total: number): number {
  return total > 0 ? Math.round((progress / total) * 100) : 0;
}

type State =
  | { status: "idle" }
  | { status: "listing"; jobId: string; job: PreviewJob }
  | { status: "picking"; jobId: string; images: PreviewImage[]; selected: Set<string> }
  | { status: "importing"; jobId: string; job: ImportJob }
  | { status: "done"; job: ImportJob }
  | { status: "error"; message: string };

export function ImageImporterApp() {
  const [pageUrl, setPageUrl] = useState("");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [allFolders, setAllFolders] = useState<FolderItem[]>([]);
  const [state, setState] = useState<State>({ status: "idle" });
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const cancelledRef = useRef(false);
  // Tracks a preview job's temp download that hasn't been handed off to an
  // import job yet — cleaned up (server-side temp files deleted) whenever
  // this component unmounts (route change) or a new URL is listed, per the
  // "don't leave temp files lying around" requirement.
  const activePreviewJobIdRef = useRef<string | null>(null);

  useEffect(() => {
    // StrictMode (dev only) double-invokes this effect: mount, cleanup,
    // mount again — the cleanup below sets cancelledRef true, so it must be
    // reset here on setup, or every poll loop for the component's real
    // lifetime silently thinks it's unmounted and never updates state again.
    cancelledRef.current = false;
    fileManagerApi.listAllFolders().then(setAllFolders).catch(() => {});
    return () => {
      cancelledRef.current = true;
      if (activePreviewJobIdRef.current) {
        discardPreviewJob(activePreviewJobIdRef.current);
        activePreviewJobIdRef.current = null;
      }
    };
  }, []);

  async function pollPreviewJob(jobId: string) {
    for (;;) {
      const job = await getPreviewJob(jobId);
      if (cancelledRef.current) return;

      if (job.status === "succeeded") {
        setState({
          status: "picking",
          jobId,
          images: job.images,
          selected: new Set(job.images.map((i) => i.id)),
        });
        return;
      }
      if (job.status === "failed") {
        activePreviewJobIdRef.current = null;
        setState({ status: "error", message: job.error ?? "unknown error" });
        return;
      }
      if (job.status === "cancelled") {
        activePreviewJobIdRef.current = null;
        setState({ status: "idle" });
        return;
      }
      setState({ status: "listing", jobId, job });
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
  }

  async function handleList() {
    if (!pageUrl.trim()) return;
    // A different page means the previous preview's downloaded temp files
    // are no longer wanted — discard them before starting the new one.
    if (activePreviewJobIdRef.current) {
      discardPreviewJob(activePreviewJobIdRef.current);
      activePreviewJobIdRef.current = null;
    }
    try {
      const { job_id } = await startPreview(pageUrl.trim());
      activePreviewJobIdRef.current = job_id;
      setState({
        status: "listing",
        jobId: job_id,
        job: { status: "running", progress: 0, total: 0, images: [], error: null },
      });
      await pollPreviewJob(job_id);
    } catch (err) {
      setState({ status: "error", message: err instanceof Error ? err.message : "unknown error" });
    }
  }

  function toggleSelected(id: string) {
    if (state.status !== "picking") return;
    const selected = new Set(state.selected);
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    setState({ ...state, selected });
  }

  function toggleAll() {
    if (state.status !== "picking") return;
    setState({
      ...state,
      selected:
        state.selected.size === state.images.length
          ? new Set()
          : new Set(state.images.map((i) => i.id)),
    });
  }

  async function pollJob(jobId: string) {
    for (;;) {
      const job = await getImportJob(jobId);
      if (cancelledRef.current) return;

      if (job.status === "succeeded" || job.status === "cancelled") {
        setState({ status: "done", job });
        return;
      }
      if (job.status === "failed") {
        setState({ status: "error", message: job.error ?? "unknown error" });
        return;
      }
      setState({ status: "importing", jobId, job });
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
  }

  async function handleSaveSelected() {
    if (state.status !== "picking" || state.selected.size === 0) return;
    const previewJobId = state.jobId;
    try {
      const { job_id } = await startImport(previewJobId, Array.from(state.selected), folderId);
      // The import job now owns cleaning up this preview job's temp files
      // (on success, failure, or cancellation) — stop tracking it here so
      // an unmount doesn't race it with a second discard.
      activePreviewJobIdRef.current = null;
      setState({
        status: "importing",
        jobId: job_id,
        job: { status: "running", progress: 0, total: 0, saved_file_ids: [], skipped: 0, error: null },
      });
      await pollJob(job_id);
    } catch (err) {
      setState({ status: "error", message: err instanceof Error ? err.message : "unknown error" });
    }
  }

  function handleCancelImport() {
    if (state.status !== "importing") return;
    void cancelImport(state.jobId);
  }

  function handleReset() {
    setState({ status: "idle" });
  }

  const listing = state.status === "listing";
  const importing = state.status === "importing";
  const foldersById = new Map(allFolders.map((f) => [f.id, f]));
  const destinationFolders = allFolders.filter((f) => !isUnderVideosFolder(f, foldersById));
  const previewItems: MediaItem[] =
    state.status === "picking"
      ? state.images.map((image) => ({
          id: image.id,
          src: tempImageSrc(image.temp_url),
          alt: "",
        }))
      : [];

  return (
    <div className="image-importer">
      <Link to="/services" className="back-link">
        &larr; Back to services
      </Link>
      <h2>image-importer</h2>
      <p className="image-importer-hint">
        Paste a page URL to list the images found on it, pick which ones you want, then save
        them into file-manager.
      </p>

      <div className="image-importer-form">
        <input
          type="url"
          placeholder="https://example.com/gallery"
          value={pageUrl}
          onChange={(e) => setPageUrl(e.target.value)}
          disabled={listing || importing}
        />
        <button onClick={() => void handleList()} disabled={listing || importing || !pageUrl.trim()}>
          List images
        </button>
      </div>

      {listing && (
        <div className="image-importer-status">
          <PercentRing pct={percentOf(state.job.progress, state.job.total)} size={3} />
          <p>Downloading previews… {state.job.progress} / {state.job.total || "?"}</p>
        </div>
      )}

      {state.status === "picking" && (
        <>
          <div className="image-importer-picker-bar">
            <label>
              <input
                type="checkbox"
                checked={state.selected.size === state.images.length && state.images.length > 0}
                onChange={toggleAll}
              />
              {state.selected.size} / {state.images.length} selected
            </label>
            <select
              value={folderId ?? ""}
              onChange={(e) => setFolderId(e.target.value === "" ? null : Number(e.target.value))}
            >
              <option value="">Home</option>
              {destinationFolders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
            <button onClick={() => void handleSaveSelected()} disabled={state.selected.size === 0}>
              Save selected to file-manager
            </button>
          </div>

          {state.images.length === 0 ? (
            <p className="image-importer-hint">No images found on that page.</p>
          ) : (
            <div className="image-importer-grid">
              {state.images.map((image, index) => (
                <div key={image.id} className="image-importer-tile">
                  <input
                    type="checkbox"
                    className="image-importer-tile-check"
                    checked={state.selected.has(image.id)}
                    onChange={() => toggleSelected(image.id)}
                  />
                  <button
                    type="button"
                    className="image-importer-tile-body"
                    onClick={() => setPreviewIndex(index)}
                  >
                    <img src={tempImageSrc(image.temp_url)} loading="lazy" alt="" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {importing && (
        <div className="image-importer-status">
          <PercentRing pct={percentOf(state.job.progress, state.job.total)} size={3} />
          <p>
            {state.job.progress} / {state.job.total} images saved ({state.job.saved_file_ids.length}{" "}
            succeeded)
          </p>
          <button onClick={handleCancelImport}>Cancel</button>
        </div>
      )}

      {state.status === "done" && (
        <div className="image-importer-status">
          <p>
            {state.job.status === "cancelled" ? "Cancelled. " : "Done. "}
            Saved {state.job.saved_file_ids.length} image(s), skipped {state.job.skipped}.
          </p>
          <button onClick={handleReset}>Import from another page</button>
        </div>
      )}

      {state.status === "error" && <p className="error-message">{state.message}</p>}

      {previewIndex !== null && (
        <MediaPreviewModal
          items={previewItems}
          index={previewIndex}
          onIndexChange={setPreviewIndex}
          onClose={() => setPreviewIndex(null)}
        />
      )}
    </div>
  );
}
