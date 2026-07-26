import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FilePickerModal } from "../file-manager/FilePickerModal";
import * as fileManagerApi from "../file-manager/api";
import type { FileItem } from "../file-manager/types";
import { downloadUrl } from "../../shared/download";
import { Stepper } from "../../shared/stepper/Stepper";
import type { StepDefinition } from "../../shared/stepper/Stepper";
import { UploadProgressRing } from "../../shared/upload-progress-ring/UploadProgressRing";
import type { UploadItem } from "../../shared/upload-progress-ring/UploadProgressRing";
import { ImageGrid } from "./ImageGrid";
import { DuplicatesStep } from "./DuplicatesStep";
import { PeopleStep } from "./PeopleStep";
import { CaptionsStep } from "./CaptionsStep";
import { FILE_INPUT_ACCEPT, isPhotoFile } from "./imageFormats";
import * as api from "./api";
import type { DatasetImage, Job, TrainingEntry } from "./types";
import "./LoraTrainerApp.css";

const POLL_INTERVAL_MS = 4000;
// Rough heuristic: enough steps to see a larger dataset several times over,
// without ballooning training time for very large ones. Only applied while
// the user hasn't manually overridden the steps field.
const STEPS_PER_IMAGE = 15;
const MIN_STEPS = 1000;
const MAX_STEPS = 6000;

function suggestSteps(imageCount: number): number {
  return Math.min(MAX_STEPS, Math.max(MIN_STEPS, imageCount * STEPS_PER_IMAGE));
}

function formatDuration(ms: number): string {
  const totalMinutes = Math.max(0, Math.round(ms / 60000));
  if (totalMinutes < 60) return `${totalMinutes}m`;
  return `${Math.floor(totalMinutes / 60)}h ${totalMinutes % 60}m`;
}

const PHASE_LABELS: Record<string, string> = {
  syncing_dataset: "syncing dataset…",
  loading_model: "loading model…",
};

export function LoraTrainerApp() {
  const [activeStep, setActiveStep] = useState(0);
  const [stepError, setStepError] = useState<string | null>(null);
  const [images, setImages] = useState<DatasetImage[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [triggerWord, setTriggerWord] = useState("");
  const [steps, setSteps] = useState(MIN_STEPS);
  const [stepsTouched, setStepsTouched] = useState(false);
  const [rank, setRank] = useState(16);
  // sd-scripts defaults network_alpha to 1 if omitted, scaling the LoRA's
  // effective learning signal down by rank/1 — default this to match rank
  // instead so training runs at full effective strength unless overridden.
  const [alpha, setAlpha] = useState(16);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  // One array of crop boxes per source photo — see `PeopleStep` for why
  // this isn't just a single optional box: a photo can contain more than
  // one instance of the trained person (a collage), producing more than
  // one box, each of which becomes its own training image below.
  const [personCrops, setPersonCrops] = useState<Record<number, (number[] | null)[]>>({});
  const [personCropsConfirmed, setPersonCropsConfirmed] = useState(true);
  const [reviewRequestId, setReviewRequestId] = useState(0);
  // Captions for actual training entries (see `trainingEntries` below),
  // keyed by entry `key` rather than `fileManagerId` — a split photo's
  // entries share a `fileManagerId` but need independent captions.
  const [entryCaptions, setEntryCaptions] = useState<Record<string, string>>({});
  // Whether the currently-active step's own background job (duplicate
  // check, face detection/matching, captioning) is still running — blocks
  // the wizard's Next button until it finishes.
  const [stepBusy, setStepBusy] = useState(false);
  // Job ids with a cancel/retry request in flight — purely for disabling
  // the button immediately, since the actual status change arrives on the
  // next poll a few seconds later.
  const [pendingJobActions, setPendingJobActions] = useState<Set<number>>(new Set());
  // Which job's log is currently expanded, plus its last-fetched content —
  // only one at a time, refetched on every poll tick while expanded so a
  // running job's log viewer stays current without a separate timer.
  const [expandedLogJobId, setExpandedLogJobId] = useState<number | null>(null);
  const [logText, setLogText] = useState<string>("");

  // Expands each source photo into one training entry per active crop (or a
  // single unmodified entry if it has none) — this is where a collage photo
  // actually gets split into separate training images. Purely derived, so
  // navigating back to People and changing the selection can't leave stale
  // split entries lying around.
  const trainingEntries: TrainingEntry[] = useMemo(() => {
    const entries: TrainingEntry[] = [];
    for (const img of images) {
      const active = (personCrops[img.fileManagerId] ?? []).filter(
        (c): c is number[] => c !== null,
      );
      if (active.length <= 1) {
        const key = `${img.fileManagerId}:0`;
        entries.push({
          key,
          fileManagerId: img.fileManagerId,
          name: img.name,
          crop: active[0] ?? null,
          caption: entryCaptions[key] ?? "",
        });
      } else {
        active.forEach((crop, i) => {
          const key = `${img.fileManagerId}:${i}`;
          entries.push({
            key,
            fileManagerId: img.fileManagerId,
            name: `${img.name} (${i + 1}/${active.length})`,
            crop,
            caption: entryCaptions[key] ?? "",
          });
        });
      }
    }
    return entries;
  }, [images, personCrops, entryCaptions]);

  async function refreshJobs() {
    setJobs(await api.listJobs());
  }

  // Optimistically drops the card immediately — the row stays in Postgres
  // either way (see `api.dismissJob`), this just stops it from showing up
  // here. Falls back to a refresh if the request itself failed, so a stale
  // dismiss doesn't silently disagree with the server.
  async function handleDismissJob(jobId: number) {
    setJobs((prev) => prev.filter((j) => j.id !== jobId));
    try {
      await api.dismissJob(jobId);
    } catch {
      await refreshJobs();
    }
  }

  async function handleCancelJob(jobId: number) {
    setPendingJobActions((prev) => new Set(prev).add(jobId));
    try {
      await api.cancelJob(jobId);
      await refreshJobs();
    } finally {
      setPendingJobActions((prev) => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  }

  async function handleRetryJob(jobId: number) {
    setPendingJobActions((prev) => new Set(prev).add(jobId));
    try {
      await api.retryJob(jobId);
      await refreshJobs();
    } finally {
      setPendingJobActions((prev) => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  }

  async function handleToggleLog(jobId: number) {
    if (expandedLogJobId === jobId) {
      setExpandedLogJobId(null);
      setLogText("");
      return;
    }
    setExpandedLogJobId(jobId);
    try {
      setLogText(await api.getJobLog(jobId));
    } catch (err) {
      setLogText(err instanceof Error ? err.message : "no log available yet");
    }
  }

  useEffect(() => {
    void refreshJobs();
  }, []);

  useEffect(() => {
    const hasActiveJob = jobs.some((j) => j.status === "queued" || j.status === "running");
    if (!hasActiveJob && expandedLogJobId === null) return;
    const timer = setInterval(() => {
      void refreshJobs();
      if (expandedLogJobId !== null) {
        api
          .getJobLog(expandedLogJobId)
          .then(setLogText)
          .catch(() => {});
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [jobs, expandedLogJobId]);

  function handleStepChange(index: number) {
    setStepError(null);
    if (index > activeStep) {
      if (activeStep === 0 && images.length === 0) {
        setStepError("Add at least one image first.");
        return;
      }
      if (activeStep === 2 && !personCropsConfirmed) {
        setReviewRequestId((id) => id + 1);
        return;
      }
      if (activeStep === 3 && !triggerWord.trim()) {
        setStepError("A trigger word is required.");
        return;
      }
    }
    setActiveStep(index);
  }

  function addImages(files: FileItem[]) {
    setPickerOpen(false);
    const existingIds = new Set(images.map((img) => img.fileManagerId));
    const toAdd = files.filter((f) => !existingIds.has(f.id));
    // Captions start empty — the Captions step generates and fills these in.
    const placeholders: DatasetImage[] = toAdd.map((f) => ({
      fileManagerId: f.id,
      name: f.name,
      caption: "",
    }));
    setImages((prev) => [...prev, ...placeholders]);
    if (!stepsTouched) setSteps(suggestSteps(images.length + placeholders.length));
  }

  async function handleUpload(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    const trayIds = files.map((file, i) => `${Date.now()}-${i}-${file.name}`);
    setUploads((prev) => [
      ...prev,
      ...files.map((file, i) => ({
        id: trayIds[i],
        name: file.name,
        progress: 0,
        status: "uploading" as const,
      })),
    ]);

    // Fresh uploads bypass file-manager/MinIO entirely — they're staged on
    // local disk server-side (see api.uploadDatasetImage) and cleared out
    // once the dataset's been sent to S3 for training.
    const uploaded: DatasetImage[] = [];
    for (let i = 0; i < files.length; i++) {
      const trayId = trayIds[i];
      try {
        const result = await api.uploadDatasetImage(files[i], (fraction) => {
          setUploads((prev) =>
            prev.map((u) => (u.id === trayId ? { ...u, progress: fraction } : u)),
          );
        });
        uploaded.push({ fileManagerId: result.file_manager_id, name: result.name, caption: "" });
        setUploads((prev) =>
          prev.map((u) => (u.id === trayId ? { ...u, status: "done", progress: 1 } : u)),
        );
      } catch (err) {
        setUploads((prev) =>
          prev.map((u) =>
            u.id === trayId
              ? {
                  ...u,
                  status: "error",
                  error: err instanceof Error ? err.message : "upload failed",
                }
              : u,
          ),
        );
      }
    }
    setImages((prev) => [...prev, ...uploaded]);
    if (!stepsTouched) setSteps(suggestSteps(images.length + uploaded.length));
  }

  function removeImage(fileManagerId: number) {
    setImages((prev) => prev.filter((img) => img.fileManagerId !== fileManagerId));
    if (!stepsTouched) {
      const remaining = images.filter((img) => img.fileManagerId !== fileManagerId).length;
      setSteps(suggestSteps(remaining));
    }
  }

  // A caption is generated once per source photo, then applied to every
  // training entry derived from that photo (all of a split photo's entries
  // start from the same caption, since they're all crops of one BLIP
  // description — each remains individually editable afterward).
  function applyCaptions(captions: Record<number, string>) {
    setEntryCaptions((prev) => {
      const next = { ...prev };
      for (const img of images) {
        if (!(img.fileManagerId in captions)) continue;
        const activeCount = Math.max(
          1,
          (personCrops[img.fileManagerId] ?? []).filter((c) => c !== null).length,
        );
        for (let i = 0; i < activeCount; i++) {
          next[`${img.fileManagerId}:${i}`] = captions[img.fileManagerId];
        }
      }
      return next;
    });
  }

  function updateCaption(entryKey: string, caption: string) {
    setEntryCaptions((prev) => ({ ...prev, [entryKey]: caption }));
  }

  async function handleLaunch() {
    setLaunchError(null);
    if (!triggerWord.trim()) {
      setLaunchError("A trigger word is required.");
      return;
    }
    if (trainingEntries.length === 0) {
      setLaunchError("Add at least one image.");
      return;
    }
    setLaunching(true);
    try {
      await api.createJob({
        trigger_word: triggerWord.trim(),
        steps,
        rank,
        alpha,
        images: trainingEntries.map((entry) => ({
          file_manager_id: entry.fileManagerId,
          caption: entry.caption,
          crop: entry.crop,
        })),
      });
      setImages([]);
      setTriggerWord("");
      setPersonCrops({});
      setPersonCropsConfirmed(true);
      setReviewRequestId(0);
      setEntryCaptions({});
      setActiveStep(0);
      await refreshJobs();
    } catch (err) {
      setLaunchError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLaunching(false);
    }
  }

  const stepDefinitions: StepDefinition[] = [
    {
      label: "Images",
      content: (
        <>
          <div className="lt-dataset-actions">
            <button type="button" onClick={() => setPickerOpen(true)}>
              Add from file storage
            </button>
            <label className="lt-upload">
              Upload images
              <input
                type="file"
                accept={FILE_INPUT_ACCEPT}
                multiple
                onChange={(e) => void handleUpload(e.target.files)}
              />
            </label>
          </div>

          {uploads.length > 0 && (
            <div className="lt-upload-progress">
              <UploadProgressRing uploads={uploads} onExited={() => setUploads([])} />
            </div>
          )}

          {images.length > 0 && uploads.length === 0 && (
            <ImageGrid images={images} onRemove={removeImage} />
          )}
        </>
      ),
    },
    {
      label: "Duplicates",
      content: <DuplicatesStep images={images} onRemove={removeImage} onBusyChange={setStepBusy} />,
    },
    {
      label: "People",
      content: (
        <PeopleStep
          images={images}
          crops={personCrops}
          onCropsResolved={(resolved) =>
            setPersonCrops((prev) => ({ ...prev, ...resolved }))
          }
          reviewRequestId={reviewRequestId}
          onCropsPendingChange={(pending) => setPersonCropsConfirmed(!pending)}
          onReviewFinished={() => {
            setPersonCropsConfirmed(true);
            setActiveStep((s) => s + 1);
          }}
          onBusyChange={setStepBusy}
        />
      ),
    },
    {
      label: "Config",
      content: (
        <div className="lt-config">
          <label className="lt-field">
            <span>Trigger word</span>
            <input
              type="text"
              placeholder="e.g. sks-person"
              value={triggerWord}
              onChange={(e) => setTriggerWord(e.target.value)}
            />
          </label>
          <label className="lt-field">
            <span>Base model</span>
            <input type="text" value="SDXL" disabled />
          </label>
          <label className="lt-field">
            <span>Steps</span>
            <input
              type="number"
              min={100}
              step={50}
              value={steps}
              onChange={(e) => {
                setStepsTouched(true);
                setSteps(Number(e.target.value));
              }}
            />
            {!stepsTouched && (
              <span className="lt-field-hint">
                auto: ~{STEPS_PER_IMAGE}/image for {trainingEntries.length || "N"} images
              </span>
            )}
          </label>
          <label className="lt-field">
            <span>Rank</span>
            <input
              type="number"
              min={1}
              step={1}
              value={rank}
              onChange={(e) => setRank(Number(e.target.value))}
            />
          </label>
          <label className="lt-field">
            <span>Alpha</span>
            <input
              type="number"
              min={1}
              step={1}
              value={alpha}
              onChange={(e) => setAlpha(Number(e.target.value))}
            />
            <span className="lt-field-hint">
              defaults to match rank — lower values weaken the LoRA's
              effective strength
            </span>
          </label>
        </div>
      ),
    },
    {
      label: "Captions",
      content: (
        <CaptionsStep
          entries={trainingEntries}
          triggerWord={triggerWord}
          onCaptionsGenerated={applyCaptions}
          onCaptionEdited={updateCaption}
          onBusyChange={setStepBusy}
        />
      ),
    },
    {
      label: "Train",
      content: (
        <div className="lt-step-launch">
          <button
            type="button"
            className="lt-launch"
            onClick={() => void handleLaunch()}
            disabled={launching}
          >
            {launching ? "Launching..." : "Train LoRA"}
          </button>
          {launchError && <p className="error-message">{launchError}</p>}
        </div>
      ),
    },
  ];

  return (
    <div className="lora-trainer">
      <div className="lt-header">
        <Link className="back-link" to="/services">
          ← Services
        </Link>
        <h2>lora-trainer</h2>
      </div>

      <hr className="lt-divider" />

      <div className="lt-content">
        <Stepper
          steps={stepDefinitions}
          activeIndex={activeStep}
          onStepChange={handleStepChange}
          nextDisabled={stepBusy}
        />
        {stepError && <p className="error-message">{stepError}</p>}
      </div>

      <hr className="lt-divider" />

      <div className="lt-jobs">
        <h2>Jobs</h2>
        {jobs.length === 0 && <p className="lt-empty">No training jobs yet.</p>}
        <ul className="lt-job-list">
          {jobs.map((job) => {
            const busy = pendingJobActions.has(job.id);
            const elapsed =
              job.status === "queued" || job.status === "running"
                ? formatDuration(Date.now() - new Date(job.created_at).getTime())
                : formatDuration(
                    new Date(job.updated_at).getTime() - new Date(job.created_at).getTime(),
                  );
            return (
              <li key={job.id} className="lt-job-row-wrap">
                <div className="lt-job-row">
                  <span className={`lt-job-status status-${job.status}`}>{job.status}</span>
                  <span className="lt-job-trigger">{job.trigger_word}</span>
                  <span className="lt-job-meta">
                    {job.base_model} · {job.steps} steps · rank {job.rank} · alpha{" "}
                    {job.alpha}
                  </span>
                  <span className="lt-job-elapsed">
                    {job.status === "queued" || job.status === "running"
                      ? `${elapsed} elapsed`
                      : `took ${elapsed}`}
                  </span>
                  {job.status === "running" && (
                    <span className="lt-job-progress">
                      <span className="lt-job-progress-bar">
                        <span
                          className="lt-job-progress-fill"
                          style={{ width: `${Math.round((job.progress ?? 0) * 100)}%` }}
                        />
                      </span>
                      <span className="lt-job-progress-pct">
                        {job.progress !== null
                          ? `${Math.round(job.progress * 100)}%`
                          : (PHASE_LABELS[job.phase ?? ""] ?? "starting…")}
                      </span>
                    </span>
                  )}
                  {job.status === "succeeded" && job.output_file_id !== null && (
                    <button
                      type="button"
                      onClick={() =>
                        void downloadUrl(
                          fileManagerApi.fileContentUrl(job.output_file_id!),
                          `${job.trigger_word}-lora.safetensors`,
                        )
                      }
                    >
                      Download
                    </button>
                  )}
                  {job.error_message && (
                    <span className="lt-job-error">{job.error_message.slice(0, 200)}</span>
                  )}
                  <button
                    type="button"
                    className="lt-job-log-toggle"
                    onClick={() => void handleToggleLog(job.id)}
                  >
                    {expandedLogJobId === job.id ? "Hide log" : "View log"}
                  </button>
                  {(job.status === "queued" || job.status === "running") && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleCancelJob(job.id)}
                    >
                      Cancel
                    </button>
                  )}
                  {job.status === "failed" && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleRetryJob(job.id)}
                    >
                      Retry
                    </button>
                  )}
                  {(job.status === "succeeded" ||
                    job.status === "failed" ||
                    job.status === "cancelled") && (
                    <button
                      type="button"
                      className="lt-job-dismiss"
                      aria-label="Dismiss"
                      title="I've seen this — hide it"
                      onClick={() => void handleDismissJob(job.id)}
                    >
                      ✕
                    </button>
                  )}
                </div>
                {expandedLogJobId === job.id && (
                  <pre className="lt-job-log">{logText || "no log available yet"}</pre>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      {pickerOpen && (
        <FilePickerModal
          title="Add images to dataset"
          filter={isPhotoFile}
          onSelectMultiple={addImages}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}
