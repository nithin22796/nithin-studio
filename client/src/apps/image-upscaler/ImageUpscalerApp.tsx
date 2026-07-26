import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { fileContentUrl } from "../file-manager/api";
import { downloadUrl } from "../../shared/download";
import { cancelUpscale, getUpscaleJob, startUpscale } from "./api";
import type { Scale, UpscaleJob } from "./types";
import "./ImageUpscalerApp.css";

const POLL_INTERVAL_MS = 1000;
const ACTIVE_JOB_KEY = "image-upscaler:active-job";

type State =
  | { status: "idle" }
  | { status: "loading"; jobId: string; progress: number; total: number }
  | { status: "success"; result: UpscaleJob }
  | { status: "error"; message: string };

function persistJob(jobId: string) {
  localStorage.setItem(ACTIVE_JOB_KEY, jobId);
}

function clearPersistedJob() {
  localStorage.removeItem(ACTIVE_JOB_KEY);
}

export function ImageUpscalerApp() {
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [scale, setScale] = useState<Scale>(2);
  const [state, setState] = useState<State>({ status: "idle" });
  const cancelledRef = useRef(false);

  const previewUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  useEffect(() => {
    cancelledRef.current = false;
    // Resume watching a job that was still running when this page was last left —
    // it kept running on the server the whole time, we just lost track of it locally.
    const activeJobId = localStorage.getItem(ACTIVE_JOB_KEY);
    if (activeJobId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- resuming a job found in storage on mount
      setState({ status: "loading", jobId: activeJobId, progress: 0, total: 0 });
      void pollJob(activeJobId);
    }
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  function chooseFile(selected: File | null) {
    setFile(selected);
    setState({ status: "idle" });
  }

  function handleDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragActive(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped && dropped.type.startsWith("image/")) chooseFile(dropped);
  }

  async function pollJob(jobId: string) {
    for (;;) {
      const job = await getUpscaleJob(jobId);
      if (cancelledRef.current) return;

      if (job.status === "succeeded") {
        clearPersistedJob();
        setState({ status: "success", result: job });
        return;
      }
      if (job.status === "failed") {
        clearPersistedJob();
        setState({ status: "error", message: job.error ?? "unknown error" });
        return;
      }
      if (job.status === "cancelled") {
        clearPersistedJob();
        setState({ status: "idle" });
        return;
      }
      setState({ status: "loading", jobId, progress: job.progress, total: job.total });
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    }
  }

  async function handleUpscale() {
    if (!file) return;
    try {
      const { job_id } = await startUpscale(file, scale);
      persistJob(job_id);
      setState({ status: "loading", jobId: job_id, progress: 0, total: 0 });
      await pollJob(job_id);
    } catch (err) {
      setState({
        status: "error",
        message: err instanceof Error ? err.message : "unknown error",
      });
    }
  }

  function handleCancel() {
    if (state.status !== "loading") return;
    void cancelUpscale(state.jobId);
  }

  const percent =
    state.status === "loading" && state.total > 0
      ? Math.round((state.progress / state.total) * 100)
      : null;
  const isLoading = state.status === "loading";

  return (
    <div className="image-upscaler">
      <Link className="back-link" to="/services">
        ← Services
      </Link>
      <h2>image-upscaler</h2>

      <div className="iu-setup">
        <div className="iu-step">
          <span className="iu-step-label">Step 1 — Image</span>

          {!file ? (
            <label
              className={dragActive ? "iu-dropzone drag-active" : "iu-dropzone"}
              onDragOver={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
            >
              <input
                type="file"
                accept="image/*"
                onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
              />
              <span className="iu-dropzone-title">Drop a photo here</span>
              <span className="iu-dropzone-hint">or click to browse</span>
            </label>
          ) : (
            <div className="iu-selected">
              <span className="iu-selected-name">🖼️ {file.name}</span>
              <button type="button" className="iu-change" onClick={() => chooseFile(null)}>
                Change
              </button>
            </div>
          )}
        </div>

        {file && (
          <div className="iu-step">
            <span className="iu-step-label">Step 2 — Enhancement</span>
            <div className="iu-target-tabs">
              <button
                type="button"
                className={scale === 2 ? "active" : ""}
                onClick={() => setScale(2)}
              >
                2× detail
              </button>
              <button
                type="button"
                className={scale === 4 ? "active" : ""}
                onClick={() => setScale(4)}
              >
                4× detail
              </button>
            </div>
            <p className="iu-hint">
              Output is always at least this many times your original photo's resolution —
              never smaller than what you started with.
            </p>
          </div>
        )}

        {(file || isLoading) && (
          <div className="iu-step">
            <span className="iu-step-label">Step 3 — Upscale</span>
            <div className="iu-process-row">
              {file && (
                <button
                  type="button"
                  className="iu-process"
                  onClick={() => void handleUpscale()}
                  disabled={isLoading}
                >
                  {isLoading
                    ? percent !== null
                      ? `Restoring… ${percent}%`
                      : "Restoring…"
                    : "Upscale"}
                </button>
              )}
              {!file && isLoading && (
                <span className="iu-selected-name">
                  Resuming job in progress{percent !== null ? ` — ${percent}%` : "…"}
                </span>
              )}
              {isLoading && (
                <button type="button" className="iu-cancel" onClick={handleCancel}>
                  Cancel
                </button>
              )}
            </div>
            {isLoading && (
              <div className="iu-progress-track">
                <div
                  className="iu-progress-fill"
                  style={{ width: percent !== null ? `${percent}%` : "8%" }}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {state.status === "error" && <p className="error-message">{state.message}</p>}
      {state.status === "success" && state.result.diagnostics && (
        <p className="iu-diagnostics">⚠ {state.result.diagnostics}</p>
      )}

      {(previewUrl || state.status === "success") && (
        <div className="iu-compare">
          {previewUrl && (
            <figure className="iu-pane">
              <figcaption>Original</figcaption>
              <img src={previewUrl} alt="Original" />
            </figure>
          )}
          {state.status === "success" && state.result.file_id !== null && (
            <figure className="iu-pane">
              <figcaption>Restored</figcaption>
              <img src={fileContentUrl(state.result.file_id, "inline")} alt="Restored" />
              <button
                type="button"
                onClick={() =>
                  void downloadUrl(
                    fileContentUrl(state.result.file_id!),
                    state.result.name ?? "restored.png",
                  )
                }
              >
                Download
              </button>
            </figure>
          )}
        </div>
      )}
    </div>
  );
}
