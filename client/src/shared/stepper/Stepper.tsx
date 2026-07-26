import type { ReactNode } from "react";
import "./Stepper.css";

export interface StepDefinition {
  label: string;
  content: ReactNode;
}

export interface StepperProps {
  steps: StepDefinition[];
  activeIndex: number;
  /** Called with the target index when a reachable step circle, or Back/Next, is clicked.
   * The caller owns validation — decide whether to actually update `activeIndex`. */
  onStepChange: (index: number) => void;
  /** Steps at or before this index are shown as done and are clickable. Steps after
   * are locked (not clickable, dimmed). Defaults to `activeIndex` (strictly linear —
   * going back doesn't let you skip back ahead past where you currently are). */
  furthestIndex?: number;
  /** Hide the built-in Next button on the current step (e.g. its content has its
   * own primary action, like a final "Submit"). */
  hideNext?: boolean;
  /** Disables the Next button (e.g. while the current step's background job
   * is still running) without hiding it. */
  nextDisabled?: boolean;
  nextLabel?: string;
  backLabel?: string;
}

export function Stepper({
  steps,
  activeIndex,
  onStepChange,
  furthestIndex,
  hideNext = false,
  nextDisabled = false,
  nextLabel = "Next",
  backLabel = "Back",
}: StepperProps) {
  const reachable = furthestIndex ?? activeIndex;
  const isLast = activeIndex === steps.length - 1;

  return (
    <div className="stepper">
      <ol className="stepper-timeline">
        {steps.map((step, index) => {
          const isDone = index < activeIndex;
          const isCurrent = index === activeIndex;
          const isReachable = index <= reachable;
          return (
            <li
              key={step.label}
              className={[
                "stepper-node",
                isCurrent ? "is-current" : "",
                isDone ? "is-done" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <button
                type="button"
                className="stepper-node-button"
                disabled={!isReachable}
                onClick={() => onStepChange(index)}
              >
                <span className="stepper-dot">{isDone ? "✓" : index + 1}</span>
                <span className="stepper-label">{step.label}</span>
              </button>
              {index < steps.length - 1 && (
                <span className={`stepper-connector ${isDone ? "is-done" : ""}`} />
              )}
            </li>
          );
        })}
      </ol>

      <div className="stepper-content" key={activeIndex}>
        {steps[activeIndex]?.content}
      </div>

      <div className="stepper-footer">
        {activeIndex > 0 && (
          <button
            type="button"
            className="stepper-back"
            onClick={() => onStepChange(activeIndex - 1)}
          >
            ← {backLabel}
          </button>
        )}
        {!isLast && !hideNext && (
          <button
            type="button"
            className="stepper-next"
            disabled={nextDisabled}
            onClick={() => onStepChange(activeIndex + 1)}
          >
            {nextLabel} →
          </button>
        )}
      </div>
    </div>
  );
}
