import { useEffect, useRef, useState } from "react";
import { PercentRing } from "../../shared/percent-ring/PercentRing";
import * as api from "./api";
import type { TrainingEntry } from "./types";
import "./CaptionsStep.css";

const POLL_INTERVAL_MS = 1000;

export interface CaptionsStepProps {
  entries: TrainingEntry[];
  triggerWord: string;
  onCaptionsGenerated: (captions: Record<number, string>) => void;
  onCaptionEdited: (key: string, caption: string) => void;
  /** Reported whenever the busy/idle state changes, so the wizard can
   * disable its own Next button while the background job is running. */
  onBusyChange?: (busy: boolean) => void;
}

type State =
  | { status: "captioning"; progress: number; jobId: string }
  | { status: "done" }
  | { status: "cancelled" }
  | { status: "error"; message: string };

function initialState(entries: TrainingEntry[]): State {
  if (entries.length === 0) return { status: "done" };
  const alreadyCaptioned = entries.every((entry) => entry.caption.trim() !== "");
  return alreadyCaptioned ? { status: "done" } : { status: "captioning", progress: 0, jobId: "" };
}

export function CaptionsStep({
  entries,
  triggerWord,
  onCaptionsGenerated,
  onCaptionEdited,
  onBusyChange,
}: CaptionsStepProps) {
  const [state, setState] = useState<State>(() => initialState(entries));
  // Holds the currently-live run function so the Retry button can restart
  // the same logic outside of any effect (see the effect below for why).
  const retryRef = useRef(() => {});
  // Guards against React StrictMode's dev-only mount -> cleanup -> remount:
  // calling an async function already runs its body — and dispatches its
  // fetch — synchronously up to the first `await`, before any cleanup gets
  // a chance to run. An `abandoned`-flag-in-cleanup pattern only stops the
  // *client* from double-tracking; it can't stop the second invocation's
  // POST from firing at all, since that happens before the flag is ever
  // checked. Checking this ref synchronously, before ever calling `run()`,
  // is what actually prevents two captioning jobs (each loading the BLIP
  // model) from being created.
  const hasStartedRef = useRef(false);
  // Purely for immediate button feedback — the actual cancellation only
  // takes effect server-side on the next between-images check, which can
  // lag a poll interval behind the click.
  const [cancelling, setCancelling] = useState(false);

  // Runs once when this step is entered, unless every entry already has a
  // caption — the stepper remounts this component fresh on each entry, so a
  // fresh captioning run naturally happens if new images were added since.
  useEffect(() => {
    if (entries.length === 0 || entries.every((entry) => entry.caption.trim() !== "")) return;
    if (hasStartedRef.current) return;
    hasStartedRef.current = true;

    async function run() {
      try {
        // Caption once per underlying photo, not once per training entry —
        // a photo split into several entries (a collage) would otherwise be
        // captioned redundantly for each of its own crops.
        const uniqueFileManagerIds = [...new Set(entries.map((entry) => entry.fileManagerId))];
        const { job_id } = await api.startCaptionBatch(uniqueFileManagerIds);
        setCancelling(false);
        setState({ status: "captioning", progress: 0, jobId: job_id });
        for (;;) {
          const job = await api.getCaptionBatch(job_id);
          if (job.status === "succeeded") {
            const prefix = triggerWord.trim();
            const captions: Record<number, string> = {};
            for (const result of job.captions) {
              captions[result.file_manager_id] = prefix
                ? `${prefix}, ${result.caption}`
                : result.caption;
            }
            onCaptionsGenerated(captions);
            setState({ status: "done" });
            return;
          }
          if (job.status === "failed") {
            setState({ status: "error", message: job.error ?? "unknown error" });
            return;
          }
          if (job.status === "cancelled") {
            setState({ status: "cancelled" });
            return;
          }
          setState({ status: "captioning", progress: job.progress, jobId: job_id });
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        }
      } catch (err) {
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "unknown error",
        });
      }
    }

    retryRef.current = () => void run();
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per mount; component remounts on each step entry
  }, []);

  // Only the "captioning" spinner should block the wizard's Next button —
  // once we land on "done" (or the job failed/was cancelled), the user is
  // free to move on.
  useEffect(() => {
    onBusyChange?.(state.status === "captioning");
    return () => onBusyChange?.(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires only when status changes, not on every parent re-render
  }, [state.status]);

  return (
    <div className="lt-captions">
      {state.status === "captioning" && (
        <div className="lt-captions-status">
          <PercentRing pct={Math.round(state.progress * 100)} />
          <p>Generating captions…</p>
          <button
            type="button"
            className="lt-captions-cancel"
            disabled={cancelling}
            onClick={() => {
              setCancelling(true);
              void api.cancelCaptionBatch(state.jobId);
            }}
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      )}

      {state.status === "cancelled" && (
        <div className="lt-captions-status">
          <p>Captioning cancelled.</p>
          <button type="button" className="lt-captions-retry" onClick={() => retryRef.current()}>
            Retry
          </button>
        </div>
      )}

      {state.status === "error" && <p className="error-message">{state.message}</p>}

      {state.status === "done" && entries.length > 0 && (
        <div className="lt-captions-list">
          {entries.map((entry) => (
            <div key={entry.key} className="lt-caption-row">
              <img
                src={
                  entry.crop
                    ? api.cropPreviewUrl(entry.fileManagerId, entry.crop)
                    : api.datasetImageUrl(entry.fileManagerId, "inline")
                }
                alt={entry.name}
              />
              <input
                type="text"
                value={entry.caption}
                onChange={(e) => onCaptionEdited(entry.key, e.target.value)}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
