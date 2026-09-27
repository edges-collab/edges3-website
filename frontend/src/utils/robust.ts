/**
 * Default axis limits that ignore RFI spikes and out-of-band noise: the
 * central 90% of the finite values (optionally only where x is inside a
 * band), padded by 10% of that span on each side. Users can still zoom out
 * (double-click resets to the full autorange in Plotly).
 */
export function robustRange(
  ys: ArrayLike<number>[],
  xs?: ArrayLike<number>,
  band?: [number, number],
  lo = 0.05,
  hi = 0.95,
): [number, number] | undefined {
  const vals: number[] = []
  for (const y of ys) {
    for (let i = 0; i < y.length; i++) {
      const v = y[i]
      if (!Number.isFinite(v)) continue
      if (xs && band && !(xs[i] >= band[0] && xs[i] <= band[1])) continue
      vals.push(v)
    }
  }
  if (vals.length < 2) return undefined
  vals.sort((a, b) => a - b)
  const a = vals[Math.floor(lo * (vals.length - 1))]
  const b = vals[Math.floor(hi * (vals.length - 1))]
  const pad = (b - a) * 0.1 || Math.abs(a) * 0.05 || 1
  return [a - pad, b + pad]
}

/** Complex values (re, im) as magnitude in dB and phase in degrees. */
export function magPhase(re: number[], im: number[]): { db: number[]; deg: number[] } {
  const db = re.map((r, i) => 20 * Math.log10(Math.hypot(r, im[i])))
  const deg = re.map((r, i) => (Math.atan2(im[i], r) * 180) / Math.PI)
  return { db, deg }
}
