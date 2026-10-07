/**
 * Says when the site has been updated since this tab loaded it, so a tab
 * left open does not keep running old code against a new backend. It
 * compares the bundle this page runs with the one the server's index.html
 * names now (checked every 5 minutes and when the tab regains focus). In the
 * Vite dev server there is no bundle name, and nothing is checked.
 */
import { useEffect, useState } from "react"

const BUNDLE = /assets\/index-[\w-]+\.js/
const CHECK_MS = 5 * 60 * 1000

function runningBundle(): string | null {
  const s = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/index-"]')
  return s?.src.match(BUNDLE)?.[0] ?? null
}

export default function UpdateBanner() {
  const [stale, setStale] = useState(false)
  useEffect(() => {
    const mine = runningBundle()
    if (!mine) return
    const check = () => {
      fetch(`${window.location.origin}${import.meta.env.BASE_URL}`, { cache: "no-store" })
        .then((r) => (r.ok ? r.text() : ""))
        .then((html) => {
          const now = html.match(BUNDLE)?.[0]
          if (now && now !== mine) setStale(true)
        })
        .catch(() => {}) // offline or restarting: try again later
    }
    const timer = window.setInterval(check, CHECK_MS)
    window.addEventListener("focus", check)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener("focus", check)
    }
  }, [])
  if (!stale) return null
  return (
    <div className="alert alert-info d-flex align-items-center gap-2 m-2 mb-0 py-1 small">
      The site has been updated since this page was loaded.
      <button className="btn btn-sm btn-primary" onClick={() => window.location.reload()}>Reload</button>
    </div>
  )
}
