/**
 * Global "what is currently displayed" context.
 *
 * Any component can call `useRunState()` to read the latest-run metadata and
 * the manifest refresh counter. The counter is incremented whenever the user
 * triggers a new pipeline run, which causes all ManifestPage instances to
 * re-fetch the manifest automatically.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type { ReactNode } from "react"
import { BASE_URL } from "../utils/baseURL"
import type { LatestRunInfo } from "../types/manifest"

interface RunState {
  /** Bump this number to force a re-fetch of the manifest in every page. */
  refreshKey: number
  /** Bump after the user triggers a fresh `Apply Parameters`. */
  bumpRefresh: () => void
  latest: LatestRunInfo
  /** Polls /latest_run a few times after a run so the banner updates fast. */
  refreshLatest: () => Promise<void>
}

const EMPTY_LATEST: LatestRunInfo = {
  source: null,
  run_id: null,
  dates: {},
  generated_at: null,
  actual_temperatures: {},
  has_2d: false,
  warnings: [],
}

const RunContext = createContext<RunState | null>(null)

export function RunProvider({ children }: { children: ReactNode }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const [latest, setLatest] = useState<LatestRunInfo>(EMPTY_LATEST)
  const inflight = useRef(false)

  const refreshLatest = useCallback(async () => {
    if (inflight.current) return
    inflight.current = true
    try {
      const res = await fetch(`${BASE_URL}/latest_run`, { cache: "no-store" })
      if (!res.ok) return
      const data = (await res.json()) as LatestRunInfo
      setLatest({
        source: (data.source as LatestRunInfo["source"]) ?? null,
        run_id: data.run_id ?? null,
        dates: data.dates ?? {},
        generated_at: data.generated_at ?? null,
        actual_temperatures: data.actual_temperatures ?? {},
        has_2d: data.has_2d ?? false,
        warnings: Array.isArray(data.warnings) ? data.warnings : [],
      })
    } catch {
      // Silently ignore — banner will just be empty until the next refresh.
    } finally {
      inflight.current = false
    }
  }, [])

  const bumpRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1)
    // Poll a few times so the latest-run info appears once the pipeline
    // finishes. Each poll will no-op if the fetch is still inflight.
    const delays = [200, 700, 1500, 3500]
    delays.forEach((d) => setTimeout(() => void refreshLatest(), d))
  }, [refreshLatest])

  // Refresh latest-run info whenever the refresh key changes (including the
  // initial value). `refreshLatest` is stable so it does not retrigger.
  useEffect(() => {
    void refreshLatest()
  }, [refreshKey, refreshLatest])

  const value = useMemo<RunState>(
    () => ({ refreshKey, bumpRefresh, latest, refreshLatest }),
    [refreshKey, bumpRefresh, latest, refreshLatest],
  )

  return <RunContext.Provider value={value}>{children}</RunContext.Provider>
}

export function useRunState(): RunState {
  const ctx = useContext(RunContext)
  if (!ctx) {
    throw new Error("useRunState must be used inside <RunProvider>")
  }
  return ctx
}
