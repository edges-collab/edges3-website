/**
 * GET a JSON endpoint whenever ``path`` changes (null: nothing). An answer
 * for an older path never replaces the current one; the server's ``detail``
 * is the error message.
 */
import { useEffect, useState } from "react"
import { BASE_URL } from "../utils/baseURL"

export function useJson<T>(path: string | null): { data: T | null; error: string | null } {
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string | null }>(
    { path: null, data: null, error: null })
  useEffect(() => {
    if (path === null) return
    let live = true
    fetch(`${BASE_URL}${path}`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`)
        return r.json() as Promise<T>
      })
      .then((d) => { if (live) setState({ path, data: d, error: null }) })
      .catch((e: unknown) => {
        if (live) setState({ path, data: null, error: e instanceof Error ? e.message : String(e) })
      })
    return () => { live = false }
  }, [path])
  return state.path === path && path !== null ? { data: state.data, error: state.error } : { data: null, error: null }
}
