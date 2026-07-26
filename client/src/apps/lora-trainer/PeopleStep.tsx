import { useEffect, useRef, useState } from "react";
import { PercentRing } from "../../shared/percent-ring/PercentRing";
import * as api from "./api";
import type { DatasetImage } from "./types";
import "./PeopleStep.css";

const POLL_INTERVAL_MS = 1000;

export interface PeopleStepProps {
  images: DatasetImage[];
  /** One array of crop boxes per photo — a photo with more than one instance
   * of the trained person (a collage) gets more than one box, each becoming
   * its own training image. A `null` slot means that particular crop was
   * rejected during review; an empty array means the whole photo passes
   * through unmodified. */
  crops: Record<number, (number[] | null)[]>;
  onCropsResolved: (crops: Record<number, (number[] | null)[]>) => void;
  /** Bumped by the parent to pop the review modal open when Next is blocked. */
  reviewRequestId: number;
  /** Whether the current crop set still needs a look before Next can proceed. */
  onCropsPendingChange: (pending: boolean) => void;
  /** Fired once every crop has been accepted/rejected — parent then advances. */
  onReviewFinished: () => void;
  /** Reported whenever the busy/idle state changes, so the wizard can
   * disable its own Next button while a background job is running. */
  onBusyChange?: (busy: boolean) => void;
}

type State =
  | { status: "detecting"; progress: number; jobId: string }
  | { status: "picking"; identities: api.PeopleIdentity[] }
  | { status: "matching"; progress: number; jobId: string }
  | { status: "done"; croppedCount: number; totalCount: number }
  | { status: "cancelled" }
  | { status: "error"; message: string };

function activeCrops(list: (number[] | null)[] | undefined): number[][] {
  return (list ?? []).filter((c): c is number[] => c !== null);
}

function initialState(
  images: DatasetImage[],
  crops: Record<number, (number[] | null)[]>,
): State {
  if (images.length === 0) {
    return { status: "done", croppedCount: 0, totalCount: 0 };
  }
  const alreadyResolved = images.every((img) => img.fileManagerId in crops);
  if (alreadyResolved) {
    const croppedCount = images.filter(
      (img) => activeCrops(crops[img.fileManagerId]).length > 0,
    ).length;
    return { status: "done", croppedCount, totalCount: images.length };
  }
  return { status: "detecting", progress: 0, jobId: "" };
}

export function PeopleStep({
  images,
  crops,
  onCropsResolved,
  reviewRequestId,
  onCropsPendingChange,
  onReviewFinished,
  onBusyChange,
}: PeopleStepProps) {
  const [state, setState] = useState<State>(() => initialState(images, crops));
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [review, setReview] = useState<{
    candidates: { fileManagerId: number; index: number }[];
    index: number;
  } | null>(null);
  // Purely for immediate button feedback — the actual cancellation only
  // takes effect server-side on the next between-images check, which can
  // lag a poll interval behind the click.
  const [cancelling, setCancelling] = useState(false);

  async function runSelect(embeddings: number[][]) {
    try {
      const { job_id } = await api.startPeopleSelect(
        images.map((img) => img.fileManagerId),
        embeddings,
      );
      setCancelling(false);
      setState({ status: "matching", progress: 0, jobId: job_id });
      for (;;) {
        const job = await api.getPeopleSelect(job_id);
        if (job.status === "succeeded") {
          const resolved: Record<number, (number[] | null)[]> = {};
          let croppedCount = 0;
          for (const result of job.results) {
            resolved[result.file_manager_id] = result.crops;
            if (result.crops.length > 0) croppedCount += 1;
          }
          onCropsResolved(resolved);
          setState({ status: "done", croppedCount, totalCount: images.length });
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
        setState({ status: "matching", progress: job.progress, jobId: job_id });
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      }
    } catch (err) {
      setState({
        status: "error",
        message: err instanceof Error ? err.message : "unknown error",
      });
    }
  }

  // Holds the currently-live detect function so the "Cancelled" state's
  // Retry button can restart it outside of any effect (see the effect
  // below for why) — retry always restarts from detection, regardless of
  // whether the cancellation happened during detect or during select.
  const retryRef = useRef(() => {});
  // Guards against React StrictMode's dev-only mount -> cleanup -> remount:
  // calling an async function already runs its body — and dispatches its
  // fetch — synchronously up to the first `await`, before any cleanup gets
  // a chance to run. An `abandoned`-flag-in-cleanup pattern only stops the
  // *client* from double-tracking; it can't stop the second invocation's
  // POST from firing at all, since that happens before the flag is ever
  // checked. Checking this ref synchronously, before ever calling
  // `runDetect()`, is what actually prevents two detect (and possibly
  // select) jobs from being created.
  const hasStartedRef = useRef(false);

  // Runs once when this step is entered, unless crops were already resolved
  // for the current image set — the stepper remounts this component fresh
  // on each entry, so a fresh detection naturally happens if the dataset
  // changed since the last visit.
  useEffect(() => {
    if (images.length === 0 || images.every((img) => img.fileManagerId in crops)) return;
    if (hasStartedRef.current) return;
    hasStartedRef.current = true;

    async function runDetect() {
      try {
        const { job_id } = await api.startPeopleDetect(images.map((img) => img.fileManagerId));
        setCancelling(false);
        setState({ status: "detecting", progress: 0, jobId: job_id });
        for (;;) {
          const job = await api.getPeopleDetect(job_id);
          if (job.status === "succeeded") {
            if (job.identities.length === 0) {
              const resolved: Record<number, (number[] | null)[]> = {};
              for (const img of images) resolved[img.fileManagerId] = [];
              onCropsResolved(resolved);
              setState({ status: "done", croppedCount: 0, totalCount: images.length });
            } else if (job.identities.length === 1) {
              await runSelect([job.identities[0].embedding]);
            } else {
              setState({ status: "picking", identities: job.identities });
            }
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
          setState({ status: "detecting", progress: job.progress, jobId: job_id });
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        }
      } catch (err) {
        setState({
          status: "error",
          message: err instanceof Error ? err.message : "unknown error",
        });
      }
    }

    retryRef.current = () => void runDetect();
    void runDetect();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per mount; component remounts on each step entry
  }, []);

  // Reports whether Next should be blocked pending review, every time a
  // crop set is (re)resolved.
  useEffect(() => {
    if (state.status === "done") {
      onCropsPendingChange(state.croppedCount > 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires whenever we land on "done", not on every parent re-render
  }, [state]);

  // Only the detect/match spinners (and the multi-identity picker, which
  // also blocks progress until a selection is made) should block the
  // wizard's Next button.
  useEffect(() => {
    const busy =
      state.status === "detecting" || state.status === "matching" || state.status === "picking";
    onBusyChange?.(busy);
    return () => onBusyChange?.(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires only when status changes, not on every parent re-render
  }, [state.status]);

  // The parent bumps `reviewRequestId` when the user tries to move past this
  // step while a crop set is still unreviewed — that's this modal's only
  // trigger, so Next stays on this step until it's finished. Handled as a
  // render-time adjustment (comparing against the last-seen id) rather than
  // an effect, since the state update needs to happen synchronously with
  // the id change, not one render behind it.
  const [lastReviewRequestId, setLastReviewRequestId] = useState(reviewRequestId);
  if (reviewRequestId !== lastReviewRequestId) {
    setLastReviewRequestId(reviewRequestId);
    if (state.status === "done" && state.croppedCount > 0) {
      const candidates: { fileManagerId: number; index: number }[] = [];
      for (const img of images) {
        (crops[img.fileManagerId] ?? []).forEach((crop, index) => {
          if (crop !== null) candidates.push({ fileManagerId: img.fileManagerId, index });
        });
      }
      setReview({ candidates, index: 0 });
    }
  }

  function acceptCrop() {
    setReview((prev) => (prev ? { ...prev, index: prev.index + 1 } : prev));
  }

  function rejectCrop() {
    if (!review) return;
    const { fileManagerId, index } = review.candidates[review.index];
    const current = crops[fileManagerId] ?? [];
    const next = current.map((crop, i) => (i === index ? null : crop));
    onCropsResolved({ [fileManagerId]: next });
    setReview((prev) => (prev ? { ...prev, index: prev.index + 1 } : prev));
  }

  function finishReview() {
    setReview(null);
    onReviewFinished();
  }

  function toggleSelected(index: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  function confirmSelection() {
    if (state.status !== "picking") return;
    const embeddings = [...selected].map((i) => state.identities[i].embedding);
    void runSelect(embeddings);
  }

  return (
    <div className="lt-people">
      {state.status === "detecting" && (
        <div className="lt-people-status">
          <PercentRing pct={Math.round(state.progress * 100)} />
          <p>Finding faces…</p>
          <button
            type="button"
            className="lt-people-cancel"
            disabled={cancelling}
            onClick={() => {
              setCancelling(true);
              void api.cancelPeopleDetect(state.jobId);
            }}
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      )}

      {state.status === "matching" && (
        <div className="lt-people-status">
          <PercentRing pct={Math.round(state.progress * 100)} />
          <p>Matching photos…</p>
          <button
            type="button"
            className="lt-people-cancel"
            disabled={cancelling}
            onClick={() => {
              setCancelling(true);
              void api.cancelPeopleSelect(state.jobId);
            }}
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      )}

      {state.status === "cancelled" && (
        <div className="lt-people-status">
          <p>Cancelled.</p>
          <button type="button" className="lt-people-retry" onClick={() => retryRef.current()}>
            Retry
          </button>
        </div>
      )}

      {state.status === "error" && <p className="error-message">{state.message}</p>}

      {state.status === "picking" && (
        <div className="lt-people-picker">
          <p className="lt-people-picker-label">
            More than one face was found — the same person can show up as more than one
            face here if photos catch them from different angles. Select every face that's
            you, then continue.
          </p>
          <div className="lt-people-grid">
            {state.identities.map((identity, i) => (
              <button
                key={i}
                type="button"
                className={`lt-people-cell${selected.has(i) ? " selected" : ""}`}
                aria-pressed={selected.has(i)}
                onClick={() => toggleSelected(i)}
              >
                <img src={`data:image/jpeg;base64,${identity.thumbnail}`} alt="" />
                <span>
                  {identity.face_count} photo{identity.face_count === 1 ? "" : "s"}
                </span>
                {selected.has(i) && <span className="lt-people-cell-check">✓</span>}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="lt-people-next"
            disabled={selected.size === 0}
            onClick={confirmSelection}
          >
            Next →
          </button>
        </div>
      )}

      {state.status === "done" && (
        <p className="lt-people-done">
          {state.croppedCount > 0
            ? `${state.croppedCount} of ${state.totalCount} photos contain a matched person and will be cropped (or split into separate training images, for photos with more than one instance of them).`
            : "No group photos found — every photo already focuses on this person."}
        </p>
      )}

      {review && (
        <div className="lt-people-review-overlay">
          <div className="lt-people-review">
            {review.index < review.candidates.length ? (
              <>
                <p className="lt-people-review-progress">
                  Crop {review.index + 1} of {review.candidates.length}
                </p>
                <img
                  className="lt-people-review-preview"
                  src={api.cropPreviewUrl(
                    review.candidates[review.index].fileManagerId,
                    crops[review.candidates[review.index].fileManagerId]![
                      review.candidates[review.index].index
                    ]!,
                  )}
                  alt=""
                />
                <div className="lt-people-review-actions">
                  <button type="button" onClick={rejectCrop}>
                    Skip this crop
                  </button>
                  <button type="button" className="lt-people-review-accept" onClick={acceptCrop}>
                    Use this crop
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="lt-people-review-progress">All crops reviewed.</p>
                <div className="lt-people-review-actions">
                  <button
                    type="button"
                    className="lt-people-review-accept"
                    onClick={finishReview}
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
