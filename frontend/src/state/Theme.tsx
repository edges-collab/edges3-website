/**
 * Light or dark: "auto" (the system's setting, the default), "light" or
 * "dark", remembered in this browser. The resolved theme is set on <html>
 * as Bootstrap's ``data-bs-theme`` (index.html sets it before the page
 * loads, so it does not flash), and the figures follow it (utils/plotComponent).
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

export type ThemeMode = "auto" | "light" | "dark"
const KEY = "edges.theme"

const systemDark = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches

function stored(): ThemeMode {
  try {
    const v = window.localStorage.getItem(KEY)
    return v === "light" || v === "dark" ? v : "auto"
  } catch {
    return "auto" // storage blocked: follow the system
  }
}

type Ctx = { mode: ThemeMode; dark: boolean; setMode: (m: ThemeMode) => void }
const ThemeContext = createContext<Ctx>({ mode: "auto", dark: false, setMode: () => {} })

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(stored)
  const [sysDark, setSysDark] = useState(systemDark)
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)")
    if (!mq) return
    const on = (e: MediaQueryListEvent) => setSysDark(e.matches)
    mq.addEventListener("change", on)
    return () => mq.removeEventListener("change", on)
  }, [])
  const dark = mode === "dark" || (mode === "auto" && sysDark)
  useEffect(() => {
    document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light")
  }, [dark])
  const setMode = (m: ThemeMode) => {
    setModeState(m)
    try {
      if (m === "auto") window.localStorage.removeItem(KEY)
      else window.localStorage.setItem(KEY, m)
    } catch {
      // storage blocked: the choice lasts for this page only
    }
  }
  return <ThemeContext.Provider value={{ mode, dark, setMode }}>{children}</ThemeContext.Provider>
}

export const useTheme = () => useContext(ThemeContext)
