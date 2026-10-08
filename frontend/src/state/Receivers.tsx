/**
 * The receivers the site offers (``GET /api/browse/deployments``), loaded once
 * and shared; ``useReceiver`` is the one the address names (``/:dep/...``).
 */
import { createContext, useContext, type ReactNode } from "react"
import { useParams } from "react-router"
import { useJson } from "../hooks/useJson"
import type { Deployment } from "../types/browse"

type Ctx = { receivers: Deployment[] | null; error: string | null }

const ReceiversContext = createContext<Ctx>({ receivers: null, error: null })

export function ReceiversProvider({ children }: { children: ReactNode }) {
  const { data, error } = useJson<Deployment[]>("/api/browse/deployments")
  return <ReceiversContext.Provider value={{ receivers: data, error }}>{children}</ReceiversContext.Provider>
}

export const useReceivers = () => useContext(ReceiversContext)

/** The receiver of the current address (inside ``/:dep``, where it is known to exist). */
export function useReceiver(): Deployment {
  const { dep } = useParams()
  const { receivers } = useReceivers()
  const r = receivers?.find((x) => x.name === dep)
  if (!r) throw new Error(`no receiver ${dep}`) // ReceiverRoute renders pages only for known receivers
  return r
}
