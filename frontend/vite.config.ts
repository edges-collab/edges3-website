import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'
import { defineConfig } from 'vite'

// Production: the FastAPI backend (uvicorn) serves the built `dist/`
// itself under `/`, with `/data/*` for OUTPUT_ROOT (manifest, runs,
// saved). So no Vite server is needed in the runtime — `npm run build`
// once, then uvicorn.
//
// This dev-server proxy below only matters if you want hot-reloading
// during local frontend development (`npm run dev`). In that mode
// Vite proxies API + static paths to a locally-running uvicorn on
// `127.0.0.1:8003`.
const BACKEND_PORT = process.env.VITE_PROXY_PORT || 8003

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] })
  ],
  server: {
    proxy: {
      '/health':           { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
      '/latest_run':       { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
      '/available_dates':  { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
      '/run_pipeline':     { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
      '/save_outputs':     { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
      '/download':         { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
      '/pipeline':         { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
      '/data':             { target: `http://127.0.0.1:${BACKEND_PORT}`, changeOrigin: false },
    },
  },
})
