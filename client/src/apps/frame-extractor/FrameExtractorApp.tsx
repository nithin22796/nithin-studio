import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FilePickerModal } from "../file-manager/FilePickerModal";
import { fileContentUrl } from "../file-manager/api";
import type { FileItem } from "../file-manager/types";
import { API_URL, useExtractor } from "./useExtractor";
import type { ExtractSource } from "./useExtractor";
import { FrameGallery } from "./FrameGallery";
import "./FrameExtractorApp.css";

type Selection =
  | { kind: "upload"; file: File }
  | { kind: "file-manager"; fileId: number; name: string };

const INTERVAL_PRESETS = [0.5, 1, 2, 5];

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
}

export function FrameExtractorApp() {
  const [sourceTab, setSourceTab] = useState<"upload" | "storage">("upload");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [intervalSeconds, setIntervalSeconds] = useState(1);
  const [durationSeconds, setDurationSeconds] = useState<number | null>(null);
  const { state, extract } = useExtractor();

  const videoUrl = useMemo(() => {
    if (!selection) return null;
    if (selection.kind === "upload") return URL.createObjectURL(selection.file);
    return fileContentUrl(selection.fileId, "inline");
  }, [selection]);

  useEffect(() => {
    return () => {
      if (selection?.kind === "upload" && videoUrl)
        URL.revokeObjectURL(videoUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoUrl]);

  const estimatedFrames =
    durationSeconds && intervalSeconds > 0
      ? Math.max(1, Math.ceil(durationSeconds / intervalSeconds))
      : null;

  function chooseUpload(file: File | null) {
    setDurationSeconds(null);
    setSelection(file ? { kind: "upload", file } : null);
  }

  function handleDrop(event: React.DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragActive(false);
    const file = event.dataTransfer.files?.[0];
    if (file && file.type.startsWith("video/")) chooseUpload(file);
  }

  function handlePickFromStorage(file: FileItem) {
    setDurationSeconds(null);
    setSelection({ kind: "file-manager", fileId: file.id, name: file.name });
    setPickerOpen(false);
  }

  function clearSelection() {
    setDurationSeconds(null);
    setSelection(null);
  }

  function handleProcess() {
    if (!selection) return;
    const source: ExtractSource =
      selection.kind === "upload"
        ? { kind: "upload", file: selection.file }
        : { kind: "file-manager", fileId: selection.fileId };
    void extract(source, intervalSeconds);
  }

  return (
    <div className="frame-extractor">
      <Link className="back-link" to="/services">
        ← Services
      </Link>
      <h2>frame-extractor</h2>

      <div className="fe-setup">
        <div className="fe-step">
          <span className="fe-step-label">Step 1 — Source</span>

          {!selection ? (
            <>
              <div className="fe-source-tabs">
                <button
                  type="button"
                  className={sourceTab === "upload" ? "active" : ""}
                  onClick={() => setSourceTab("upload")}
                >
                  Upload a video
                </button>
                <button
                  type="button"
                  className={sourceTab === "storage" ? "active" : ""}
                  onClick={() => setSourceTab("storage")}
                >
                  From file storage
                </button>
              </div>

              {sourceTab === "upload" ? (
                <label
                  className={
                    dragActive ? "fe-dropzone drag-active" : "fe-dropzone"
                  }
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragActive(true);
                  }}
                  onDragLeave={() => setDragActive(false)}
                  onDrop={handleDrop}
                >
                  <input
                    type="file"
                    accept="video/*"
                    onChange={(e) => chooseUpload(e.target.files?.[0] ?? null)}
                  />
                  <span className="fe-dropzone-title">Drop a video here</span>
                  <span className="fe-dropzone-hint">or click to browse</span>
                </label>
              ) : (
                <button
                  type="button"
                  className="fe-storage-trigger"
                  onClick={() => setPickerOpen(true)}
                >
                  Browse file storage…
                </button>
              )}
            </>
          ) : (
            <div className="fe-selected">
              {videoUrl && (
                <video
                  className="fe-selected-video"
                  src={videoUrl}
                  controls
                  onLoadedMetadata={(e) =>
                    setDurationSeconds(e.currentTarget.duration || null)
                  }
                />
              )}
              <div className="fe-selected-details">
                <div className="fe-selected-info">
                  <span className="fe-selected-name">
                    🎬{" "}
                    {selection.kind === "upload"
                      ? selection.file.name
                      : selection.name}
                  </span>
                  <span className="fe-selected-meta">
                    {selection.kind === "upload" &&
                      formatSize(selection.file.size)}
                    {durationSeconds !== null &&
                      ` · ${formatDuration(durationSeconds)}`}
                  </span>
                </div>
                <button
                  type="button"
                  className="fe-change"
                  onClick={clearSelection}
                >
                  Change
                </button>
              </div>
            </div>
          )}
        </div>

        {selection && (
          <div className="fe-step">
            <span className="fe-step-label">Step 2 — Interval</span>
            <div className="fe-interval">
              <div className="fe-presets">
                {INTERVAL_PRESETS.map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    className={intervalSeconds === preset ? "active" : ""}
                    onClick={() => setIntervalSeconds(preset)}
                  >
                    {preset}s
                  </button>
                ))}
              </div>
              <label className="fe-interval-custom">
                <input
                  type="number"
                  min={0.1}
                  step={0.1}
                  value={intervalSeconds}
                  onChange={(e) => setIntervalSeconds(Number(e.target.value))}
                />
                <span>seconds between frames</span>
              </label>
            </div>
            {estimatedFrames !== null && (
              <p className="fe-estimate">
                ≈ {estimatedFrames} frames will be extracted
              </p>
            )}
          </div>
        )}

        {selection && (
          <div className="fe-step fe-step-process">
            <span className="fe-step-label">Step 3 — Process</span>
            <button
              type="button"
              className="fe-process"
              onClick={handleProcess}
              disabled={state.status === "loading"}
            >
              {state.status === "loading" ? "Processing..." : "Process"}
            </button>
          </div>
        )}
      </div>

      {state.status === "error" && (
        <p className="error-message">{state.message}</p>
      )}

      {state.status === "success" && (
        <FrameGallery
          apiUrl={API_URL}
          jobId={state.result.job_id}
          frames={state.result.frames}
        />
      )}

      {pickerOpen && (
        <FilePickerModal
          title="Pick a video"
          filter={(file) => file.content_type.startsWith("video/")}
          onSelect={handlePickFromStorage}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
