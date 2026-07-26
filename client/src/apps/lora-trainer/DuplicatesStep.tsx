import { useEffect, useRef, useState } from "react";
import { PercentRing } from "../../shared/percent-ring/PercentRing";
import { ImageGrid } from "./ImageGrid";
import * as api from "./api";
import type { DatasetImage } from "./types";
import "./DuplicatesStep.css";

const POLL_INTERVAL_MS = 1000;

export interface DuplicatesStepProps {
  images: DatasetImage[];
  onRemove: (fileManagerId: number) => void;
  /** Reported whenever the busy/idle state changes, so the wizard can
   * disable its own Next button while the background check is running. */
  onBusyChange?: (busy: boolean) => void;
}

type State =
  | { status: "checking"; progress: number; jobId: string }
  | { status: "done"; groups: number[][] }
  | { status: "cancelled" }
  | { status: "error"; message: string };

interface Candidate {
  id: number;
  referenceId: number;
}

export function DuplicatesStep({ images, onRemove, onBusyChange }: DuplicatesStepProps) {
  const [state, setState] = useState<State>(
    images.length === 0
      ? { status: "done", groups: [] }
      : { status: "checking", progress: 0, jobId: "" },
  );
  const [review, setReview] = useState<{ candidates: Candidate[]; index: number } | null>(null);
  const [reviewed, setReviewed] = useState(false);
  // Holds the currently-live check function so the Retry button can restart
  // it outside of any effect (see the effect below for why).
  const retryRef = useRef(() => {});
  // Guards against React StrictMode's dev-only mount -> cleanup -> remount:
  // calling an async function already runs its body — and dispatches its
  // fetch — synchronously up to the first `await`, before any cleanup gets
  // a chance to run. An `abandoned`-flag-in-cleanup pattern only stops the
  // *client* from double-tracking; it can't stop the second invocation's
  // POST from firing at all, since that happens before the flag is ever
  // checked. Checking this ref synchronously, before ever calling `check()`,
  // is what actually prevents two duplicate-check jobs from being created.
  const hasStartedRef = useRef(false);
  // Purely for immediate button feedback — the actual cancellation only
  // takes effect server-side on the next between-images check, which can
  // lag a poll interval behind the click.
  const [cancelling, setCancelling] = useState(false);

  // Runs once when this step is entered. The stepper remounts this component
  // fresh every time the user navigates to it (its wrapper is keyed by step
  // index), so this naturally re-checks on every entry without extra wiring.
  useEffect(() => {
    if (images.length === 0) return;
    if (hasStartedRef.current) return;
    hasStartedRef.current = true;

    async function check() {
      try {
        const { job_id } = await api.startDuplicateCheck(images.map((img) => img.fileManagerId));
        setCancelling(false);
        setState({ status: "checking", progress: 0, jobId: job_id });
        for (;;) {
          const job = await api.getDuplicateCheck(job_id);
          if (job.status === "succeeded") {
            setState({ status: "done", groups: job.groups });
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
          setState({ status: "checking", progress: job.progress, jobId: job_id });
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        }
      } catch (err) {
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "unknown error",
        });
      }
    }

    retryRef.current = () => void check();
    void check();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per mount; component remounts on each step entry
  }, []);

  // Only the "checking" spinner should block the wizard's Next button —
  // once we land on "done" (or the check failed/was cancelled), the user
  // is free to move on.
  useEffect(() => {
    onBusyChange?.(state.status === "checking");
    return () => onBusyChange?.(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires only when status changes, not on every parent re-render
  }, [state.status]);

  // Drop ids that were already removed, and any group that's down to 0-1
  // images as a result — recomputed on every render so removing an image
  // updates the groups immediately without a fresh check.
  const imageIds = new Set(images.map((img) => img.fileManagerId));
  const visibleGroups =
    state.status === "done"
      ? state.groups
          .map((group) => group.filter((id) => imageIds.has(id)))
          .filter((group) => group.length > 1)
      : [];

  function startReview() {
    // Treat the first photo in each group as the keeper and walk the rest
    // one at a time, comparing each against that keeper.
    const candidates = visibleGroups.flatMap((group) =>
      group.slice(1).map((id) => ({ id, referenceId: group[0] })),
    );
    setReview({ candidates, index: 0 });
  }

  function resolveCandidate(remove: boolean) {
    if (!review) return;
    const candidate = review.candidates[review.index];
    if (remove) onRemove(candidate.id);
    setReview({ ...review, index: review.index + 1 });
  }

  return (
    <div className="lt-duplicates">
      {state.status === "checking" && (
        <div className="lt-dupe-checking">
          <PercentRing pct={Math.round(state.progress * 100)} />
          <p>Checking duplicates…</p>
          <button
            type="button"
            className="lt-dupe-cancel"
            disabled={cancelling}
            onClick={() => {
              setCancelling(true);
              void api.cancelDuplicateCheck(state.jobId);
            }}
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      )}

      {state.status === "cancelled" && (
        <div className="lt-dupe-checking">
          <p>Duplicate check cancelled.</p>
          <button type="button" className="lt-dupe-retry" onClick={() => retryRef.current()}>
            Retry
          </button>
        </div>
      )}

      {state.status === "error" && <p className="error-message">{state.message}</p>}

      {state.status === "done" && visibleGroups.length === 0 && (
        <p className="lt-dupe-empty">No duplicates found.</p>
      )}

      {state.status === "done" && images.length > 0 && (
        <ImageGrid
          images={images}
          onRemove={onRemove}
          secondaryAction={
            visibleGroups.length > 0 && !reviewed
              ? { label: `Show duplicates (${visibleGroups.length})`, onClick: startReview }
              : undefined
          }
        />
      )}

      {review && (
        <div className="lt-dupe-review-overlay">
          <div className="lt-dupe-review">
            {review.index < review.candidates.length ? (
              <>
                <p className="lt-dupe-review-progress">
                  Duplicate {review.index + 1} of {review.candidates.length}
                </p>
                <div className="lt-dupe-review-pair">
                  <div className="lt-dupe-review-cell">
                    <img
                      src={api.datasetImageUrl(
                        review.candidates[review.index].referenceId,
                        "inline",
                      )}
                      alt=""
                    />
                    <span>Keeping</span>
                  </div>
                  <div className="lt-dupe-review-cell">
                    <img
                      src={api.datasetImageUrl(review.candidates[review.index].id, "inline")}
                      alt=""
                    />
                    <span>Possible duplicate</span>
                  </div>
                </div>
                <div className="lt-dupe-review-actions">
                  <button type="button" onClick={() => resolveCandidate(false)}>
                    Keep both
                  </button>
                  <button
                    type="button"
                    className="lt-dupe-review-remove"
                    onClick={() => resolveCandidate(true)}
                  >
                    Remove duplicate
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="lt-dupe-review-progress">All duplicates reviewed.</p>
                <div className="lt-dupe-review-actions">
                  <button
                    type="button"
                    className="lt-dupe-review-remove"
                    onClick={() => {
                      setReview(null);
                      setReviewed(true);
                    }}
                  >
                    Finish
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
