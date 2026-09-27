/**
 * Resolve a calibration/observation request in the catalog as its options
 * change, find an existing run with the same key (then show it at once),
 * start it on request, and poll while it is queued or running.
 */
import { useCallback, useEffect, useRef, useState } from "react"
import { BASE_URL } from "../utils/baseURL"
import type { Resolved, RunDetail, RunKind } from "../types/runs"

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE_URL}${path}`, init)
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail ?? `HTTP ${r.status}`)
  }
  return r.json() as Promise<T>
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
})

export function useRun<I, R>(kind: RunKind, request: unknown) {
  const key = JSON.stringify(request)
  const [resolved, setResolved] = useState<{ key: string; value: Resolved<I> } | null>(null)
  const [detail, setDetail] = useState<RunDetail<I, R> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const poll = useRef<number | null>(null)

  const stopPolling = () => {
    if (poll.current !== null) window.clearTimeout(poll.current)
    poll.current = null
  }

  const follow = useCallback((id: string) => {
    stopPolling()
    const tick = () => {
      call<RunDetail<I, R>>(`/api/${kind}s/${id}`)
        .then((d) => {
          setDetail(d)
          if (d.status.state === "queued" || d.status.state === "running") {
            poll.current = window.setTimeout(tick, 2000)
          }
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
    }
    tick()
  }, [kind])

  // resolve whenever the request changes
  useEffect(() => {
    let cancelled = false
    stopPolling()
    setError(null)
    setDetail(null)
    call<Resolved<I>>(`/api/${kind}s/resolve`, post(JSON.parse(key)))
      .then((v) => {
        if (cancelled) return
        setResolved({ key, value: v })
        if (v.status) follow(v.id)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => { cancelled = true; stopPolling() }
  }, [kind, key, follow])

  const start = useCallback(() => {
    setError(null)
    call<RunDetail<I, R>>(`/api/${kind}s`, post(JSON.parse(key)))
      .then((d) => { setDetail(d); follow(d.id) })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [kind, key, follow])

  const current = resolved && resolved.key === key ? resolved.value : null
  const shown = detail && current && detail.id === current.id ? detail : null
  return { resolved: current, detail: shown, error, start }
}
