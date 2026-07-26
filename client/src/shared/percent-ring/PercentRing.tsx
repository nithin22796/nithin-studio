import { progressColor } from "./progressColor";
import "./PercentRing.css";

export interface PercentRingProps {
  pct: number;
  /** Outer diameter in rem. */
  size?: number;
  /** Overrides the percentage-interpolated color (e.g. for an error state). */
  color?: string;
}

export function PercentRing({ pct, size = 4.5, color }: PercentRingProps) {
  const ringColor = color ?? progressColor(pct);
  const innerSize = size * 0.78;

  return (
    <div
      className="percent-ring"
      style={{
        width: `${size}rem`,
        height: `${size}rem`,
        background: `conic-gradient(${ringColor} ${pct * 3.6}deg, var(--hairline) 0deg)`,
      }}
    >
      <div
        className="percent-ring-inner"
        style={{ width: `${innerSize}rem`, height: `${innerSize}rem` }}
      >
        <span className="percent-ring-pct">{pct}%</span>
      </div>
    </div>
  );
}
