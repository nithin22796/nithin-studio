// Percentage → color, interpolated smoothly across named stops so the ring
// visibly warms up as progress advances: dark red at 0%, through orange,
// yellow, and light blue, landing on green at 100%.
const STOPS: [number, string][] = [
  [0, "#7a1f1f"],
  [25, "#e2874c"],
  [50, "#f0d34c"],
  [75, "#6fc3e0"],
  [100, "#5fae8d"],
];

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mix(hexA: string, hexB: string, t: number): string {
  const [ar, ag, ab] = hexToRgb(hexA);
  const [br, bg, bb] = hexToRgb(hexB);
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const b = Math.round(ab + (bb - ab) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

export function progressColor(pct: number): string {
  const clamped = Math.max(0, Math.min(100, pct));
  for (let i = 0; i < STOPS.length - 1; i++) {
    const [p0, c0] = STOPS[i];
    const [p1, c1] = STOPS[i + 1];
    if (clamped <= p1) {
      return mix(c0, c1, (clamped - p0) / (p1 - p0));
    }
  }
  return STOPS[STOPS.length - 1][1];
}
