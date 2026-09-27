/**
 * Resolve a calibration/observation request in the catalog as its options
 * change, find an existing run with the same key (then show it at once),
 * start it on request, and poll while it is queued or running.
 *
 * Every response is checked against the request it was made for, so a slow
 * answer for old options never replaces the current run; and the id returned
 * when starting is adopted, since the key can change between resolving and
 * starting (e.g. a catalog update).
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { BASE_URL } from "../utils/baseURL"
import type { Resolved, RunDetail, RunKind } from "../types/runs"

class HttpError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE_URL}${path}`, init)
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new HttpError(r.status, body.detail ?? `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
})

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function useRun<I, R>(kind: RunKind, request: unknown) {
  const key = JSON.stringify(request)
  const [resolved, setResolved] = useState<{ key: string; value: Resolved<I> } | null>(null)
  const [detail, setDetail] = useState<{ key: string; value: RunDetail<I, R> } | null>(null)
  const [error, setError] = useState<{ key: string; message: string } | null>(null)
  const current = useRef(key) // the request the page shows now
  const timer = useRef<number | null>(null)

  const stopPolling = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }

  /** Fetch a run's detail for request ``k`` until it is finished. */
  const follow = useCallback((k: string, id: string) => {
    stopPolling()
    let failures = 0
    const tick = () => {
      call<RunDetail<I, R>>(`/api/${kind}s/${id}`)
        .then((d) => {
          if (current.current !== k) return
          failures = 0
          setDetail({ key: k, value: d })
          if (d.status.state === "queued" || d.status.state === "running") {
            timer.current = window.setTimeout(tick, 2000)
          }
        })
        .catch((e: unknown) => {
          if (current.current !== k) return
          if (e instanceof HttpError && e.status === 404) {
            setError({ key: k, message: message(e) })
            return
          }
          failures += 1 // e.g. a restart: keep trying, more slowly
          timer.current = window.setTimeout(tick, Math.min(30000, 2000 * 2 ** failures))
        })
    }
    tick()
  }, [kind])

  // resolve whenever the request changes
  useEffect(() => {
    current.current = key
    stopPolling()
    call<Resolved<I>>(`/api/${kind}s/resolve`, post(JSON.parse(key)))
      .then((v) => {
        if (current.current !== key) return
        setResolved({ key, value: v })
        if (v.status) follow(key, v.id)
      })
      .catch((e: unknown) => {
        if (current.current === key) setError({ key, message: message(e) })
      })
    return stopPolling
  }, [kind, key, follow])

  const start = useCallback(() => {
    const k = current.current
    setError(null)
    call<RunDetail<I, R>>(`/api/${kind}s`, post(JSON.parse(k)))
      .then((d) => {
        if (current.current !== k) return
        // adopt the id the server started (the key may have changed)
        setResolved((r) => (r && r.key === k ? { key: k, value: { ...r.value, id: d.id, status: d.status } } : r))
        setDetail({ key: k, value: d })
        follow(k, d.id)
      })
      .catch((e: unknown) => {
        if (current.current === k) setError({ key: k, message: message(e) })
      })
  }, [kind, follow])

  const res = resolved && resolved.key === key ? resolved.value : null
  const det = detail && detail.key === key && res && detail.value.id === res.id ? detail.value : null
  return { resolved: res, detail: det, error: error && error.key === key ? error.message : null, start }
}
