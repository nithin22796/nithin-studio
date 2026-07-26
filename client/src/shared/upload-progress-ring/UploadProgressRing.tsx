import { useEffect, useState } from "react";
import { progressColor } from "../percent-ring/progressColor";
import "./UploadProgressRing.css";

export interface UploadItem {
  id: string | number;
  name: string;
  /** 0–1 */
  progress: number;
  status: "uploading" | "done" | "error";
  error?: string;
}

export interface UploadProgressRingProps {
  uploads: UploadItem[];
  /** Called once the success celebration (or the error state's timeout) finishes. */
  onExited?: () => void;
}

type Phase = "progress" | "spin" | "check" | "exit";

const SPIN_MS = 600;
const CHECK_HOLD_MS = 2000;
const EXIT_MS = 450;
const ERROR_DISMISS_MS = 4000;

export function UploadProgressRing({ uploads, onExited }: UploadProgressRingProps) {
  const [phase, setPhase] = useState<Phase>("progress");
  const [wasAllDone, setWasAllDone] = useState(false);
  const [celebrating, setCelebrating] = useState(false);

  const errorCount = uploads.filter((u) => u.status === "error").length;
  const resolvedCount = uploads.filter((u) => u.status !== "uploading").length;
  const current = uploads.find((u) => u.status === "uploading");
  const fraction =
    uploads.length === 0 ? 0 : (resolvedCount + (current?.progress ?? 0)) / uploads.length;
  const pct = Math.round(fraction * 100);
  const allDone = uploads.length > 0 && resolvedCount === uploads.length;
  const color = errorCount > 0 ? "var(--fault)" : progressColor(pct);

  // Kick off the celebration the instant a run finishes cleanly, without
  // waiting a render behind an effect — an errored run just stays on
  // "progress" (red) and is dismissed by the timer effect below instead.
  if (allDone !== wasAllDone) {
    setWasAllDone(allDone);
    const shouldCelebrate = allDone && errorCount === 0;
    setCelebrating(shouldCelebrate);
    setPhase(shouldCelebrate ? "spin" : "progress");
  }

  // Chains the spin -> check -> hold -> exit sequence once a clean run
  // starts celebrating. Gated on `celebrating` rather than `phase` itself —
  // `phase` changes on every step of this same sequence, and re-running an
  // effect on its own dependency change would tear down and cancel whatever
  // timers it had just scheduled before they get a chance to fire.
  useEffect(() => {
    if (!celebrating) return;
    const t1 = setTimeout(() => setPhase("check"), SPIN_MS);
    const t2 = setTimeout(() => setPhase("exit"), SPIN_MS + CHECK_HOLD_MS);
    const t3 = setTimeout(() => onExited?.(), SPIN_MS + CHECK_HOLD_MS + EXIT_MS);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scoped to the start of a celebration run only
  }, [celebrating]);

  // A run that finished with errors doesn't celebrate — just dismiss it
  // after a beat so it doesn't linger forever.
  useEffect(() => {
    if (!allDone || errorCount === 0) return;
    const timer = setTimeout(() => onExited?.(), ERROR_DISMISS_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scoped to the completed+errored transition only
  }, [allDone, errorCount]);

  if (uploads.length === 0) return null;

  const showCheck = phase === "check" || phase === "exit";
  const ringClassName = [
    "upload-progress-ring",
    phase === "spin" && "upload-progress-ring-spin",
    phase === "exit" && "upload-progress-ring-exit",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={ringClassName}
      style={{ background: `conic-gradient(${color} ${pct * 3.6}deg, var(--hairline) 0deg)` }}
    >
      <div className="upload-progress-ring-inner">
        {showCheck ? (
          <svg className="upload-progress-ring-check" viewBox="0 0 52 52" fill="none">
            <path d="M15 27l7 7 15-15" className="upload-progress-ring-check-mark" />
          </svg>
        ) : (
          <span className="upload-progress-ring-pct">{pct}%</span>
        )}
      </div>
    </div>
  );
}
