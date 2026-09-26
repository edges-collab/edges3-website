/**
 * Helpers for the "Last night" page: decoding waterfalls, site-local
 * time strings for Plotly, LST tick positions and robust colour limits.
 */
import type { EncodedArray } from "../types/night"

/** Decode a base64 float32 array into rows (NaN kept). */
export function decodeRows(enc: EncodedArray): Float32Array[] {
  const bin = atob(enc.data)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  const flat = new Float32Array(bytes.buffer)
  const [nRows, nCols] = enc.shape
  const rows: Float32Array[] = new Array(nRows)
  for (let i = 0; i < nRows; i++) rows[i] = flat.subarray(i * nCols, (i + 1) * nCols)
  return rows
}

/**
 * POSIX seconds -> "YYYY-MM-DD HH:MM:SS" in site-local time. Plotly treats
 * such strings as naive dates, so the axis shows site time whatever the
 * browser's time zone is.
 */
export function toSiteTime(t: number, utcOffsetHours: number): string {
  return new Date((t + utcOffsetHours * 3600) * 1000)
    .toISOString()
    .slice(0, 19)
    .replace("T", " ")
}

export function siteTimes(
  t: (number | null)[],
  utcOffsetHours: number,
): (string | null)[] {
  return t.map((x) => (x === null ? null : toSiteTime(x, utcOffsetHours)))
}

/**
 * Times (POSIX s) at which LST crosses each whole hour, for a secondary
 * axis. LST is unwrapped (24 -> 0) and linearly interpolated between
 * neighbouring samples; gap rows (null) are skipped.
 */
export function lstTicks(
  t: (number | null)[],
  lst: (number | null)[],
): { t: number; label: string }[] {
  const pts: [number, number][] = []
  let offset = 0
  let prev: number | null = null
  for (let i = 0; i < t.length; i++) {
    const ti = t[i]
    const li = lst[i]
    if (ti === null || li === null) continue
    if (prev !== null && li + offset < prev - 12) offset += 24
    const u = li + offset
    pts.push([ti, u])
    prev = u
  }
  const ticks: { t: number; label: string }[] = []
  for (let i = 1; i < pts.length; i++) {
    const [t0, u0] = pts[i - 1]
    const [t1, u1] = pts[i]
    if (!(u1 > u0)) continue
    for (let h = Math.ceil(u0); h <= u1; h++) {
      if (h === u0 && i > 1) continue // counted in the previous interval
      if (t1 - t0 > 600) continue // don't place ticks inside gaps
      const tt = t0 + ((h - u0) / (u1 - u0)) * (t1 - t0)
      const hh = ((h % 24) + 24) % 24
      ticks.push({ t: tt, label: `${String(hh).padStart(2, "0")}h` })
    }
  }
  return ticks
}

/** The q-quantiles (0..1) of the finite values of `rows` (sampled). */
export function robustRange(
  rows: ArrayLike<number>[],
  lo = 0.02,
  hi = 0.98,
  transform?: (v: number) => number,
): [number, number] | null {
  const vals: number[] = []
  const stride = Math.max(1, Math.floor((rows.length * (rows[0]?.length ?? 0)) / 200000))
  let k = 0
  for (const row of rows) {
    for (let j = 0; j < row.length; j++) {
      if (k++ % stride) continue
      const v = transform ? transform(row[j]) : row[j]
      if (Number.isFinite(v)) vals.push(v)
    }
  }
  if (vals.length === 0) return null
  vals.sort((a, b) => a - b)
  return [vals[Math.floor(lo * (vals.length - 1))], vals[Math.floor(hi * (vals.length - 1))]]
}

/** Shift a YYYY-MM-DD date by `days`. */
export function shiftDate(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}
