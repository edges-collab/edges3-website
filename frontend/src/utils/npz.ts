/**
 * Load a `.npz` (a zip of `.npy` arrays) into named arrays. Used for the
 * `plots.npz` / `waterfalls.npz` files of calibration and observation runs
 * (see backend/run_single_day.py). Results are cached per URL.
 */
import JSZip from "jszip"
import { parse } from "npyjs"
import { useEffect, useState } from "react"

export type NpyArray = { data: ArrayLike<number>; shape: number[] }
export type Npz = Record<string, NpyArray>

const cache = new Map<string, Promise<Npz>>()

export function loadNpz(url: string): Promise<Npz> {
  let p = cache.get(url)
  if (!p) {
    p = (async () => {
      const r = await fetch(url)
      if (!r.ok) throw new Error(`cannot load ${url}: HTTP ${r.status}`)
      const zip = await JSZip.loadAsync(await r.arrayBuffer())
      const out: Npz = {}
      await Promise.all(
        Object.keys(zip.files)
          .filter((n) => n.endsWith(".npy"))
          .map(async (n) => {
            const parsed = parse(await zip.files[n].async("arraybuffer")) as unknown as {
              data: ArrayLike<number>
              shape: number[]
            }
            out[n.slice(0, -4)] = { data: parsed.data, shape: parsed.shape }
          }),
      )
      return out
    })()
    p.catch(() => cache.delete(url))
    cache.set(url, p)
  }
  return p
}

/** Load an npz in a component: `{ data, error }` (null while loading). */
export function useNpz(url: string | null): { data: Npz | null; error: string | null } {
  const [state, setState] = useState<{ url: string | null; data: Npz | null; error: string | null }>(
    { url: null, data: null, error: null },
  )
  useEffect(() => {
    if (!url) return
    let cancelled = false
    loadNpz(url)
      .then((data) => { if (!cancelled) setState({ url, data, error: null }) })
      .catch((e: unknown) => {
        if (!cancelled) setState({ url, data: null, error: e instanceof Error ? e.message : String(e) })
      })
    return () => { cancelled = true }
  }, [url])
  return state.url === url ? { data: state.data, error: state.error } : { data: null, error: null }
}

/** A 1-D array as plain numbers (NaN kept). */
export function arr(npz: Npz, key: string): number[] {
  const a = npz[key]
  if (!a) throw new Error(`missing array ${key}`)
  return Array.from(a.data, Number)
}

/** A 2-D array as rows. */
export function rows(npz: Npz, key: string): number[][] {
  const a = npz[key]
  if (!a) throw new Error(`missing array ${key}`)
  const [n, m] = a.shape
  const out: number[][] = new Array(n)
  for (let i = 0; i < n; i++) out[i] = Array.from((a.data as Float32Array).subarray(i * m, (i + 1) * m), Number)
  return out
}
