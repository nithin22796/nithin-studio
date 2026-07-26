const UNIT_WIDTH = 32;
const REPEATS = 6;
const HEIGHT = 24;
const BASELINE = HEIGHT / 2;

function buildPulsePath(): string {
  const unit = "l6,0 l3,-8 l3,16 l3,-8 l17,0";
  return `M0,${BASELINE} ${Array(REPEATS).fill(unit).join(" ")}`;
}

const PATH = buildPulsePath();

export interface PulseTraceProps {
  live: boolean;
}

/**
 * The topbar's signature: a scrolling oscilloscope trace tied to the real
 * SSE connection state. It only "beats" while actually connected; otherwise
 * it flatlines to a static dashed line.
 */
export function PulseTrace({ live }: PulseTraceProps) {
  return (
    <svg
      className={`pulse-trace ${live ? "is-live" : "is-flat"}`}
      width="96"
      height={HEIGHT}
      viewBox={`0 0 96 ${HEIGHT}`}
      aria-hidden="true"
    >
      {live ? (
        <path d={PATH} className="pulse-trace-line" />
      ) : (
        <line x1="0" y1={BASELINE} x2={UNIT_WIDTH * REPEATS} y2={BASELINE} className="pulse-trace-flat" />
      )}
    </svg>
  );
}
