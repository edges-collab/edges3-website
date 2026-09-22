import { useEffect, useMemo, useRef, useState } from "react"
import type { Manifest } from "../types/manifest"
import { BASE_URL, withBaseUrl } from "../utils/baseURL"
import { useRunState } from "../state/RunContext"

export function useManifest(path: string = "/data/manifest.json") {
  const { refreshKey } = useRunState()
  const [cacheBuster, setCacheBuster] = useState(() => Date.now())
  // Skip the cache-buster bump on initial mount: the useState initialiser
  // already produced a fresh URL, so bumping again would fire a second,
  // duplicate fetch on every page load.
  const firstRun = useRef(true)

  const url = useMemo(() => {
    // Cache-buster changes only when refreshKey changes (or path changes),
    // so the URL is stable across renders and therefore pure.
    return `${withBaseUrl(path)}?t=${cacheBuster}`
  }, [path, cacheBuster])

  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false
      return
    }
    setCacheBuster(Date.now())
  }, [path, refreshKey])

  useEffect(() => {
    let cancelled = false
     
    setError(null)
    fetch(url, { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data: Manifest) => {
        if (!cancelled) setManifest(data)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)))
      })
    return () => {
      cancelled = true
    }
  }, [url, refreshKey])

  return { manifest, error }
}

/** Convenience hook for fetching other backend JSON. */
export function useJsonFetch<T>(path: string, refreshKey: number) {
  const [cacheBuster, setCacheBuster] = useState(() => Date.now())
  const firstRun = useRef(true)
  const url = useMemo(() => {
    const normalised = path.startsWith("/") ? path : `/${path}`
    return `${BASE_URL}${normalised}?t=${cacheBuster}`
  }, [path, cacheBuster])

  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false
      return
    }
    setCacheBuster(Date.now())
  }, [path, refreshKey])

  useEffect(() => {
    let cancelled = false
     
    setError(null)
    fetch(url, { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<T>
      })
      .then((d) => { if (!cancelled) setData(d) })
      .catch((e: unknown) => { if (!cancelled) setError(e instanceof Error ? e : new Error(String(e))) })
    return () => { cancelled = true }
  }, [url, refreshKey])
  return { data, error }
}
