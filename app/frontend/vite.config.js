import { defineConfig } from 'vite';

// Vite root = app/frontend/
// Dev:   serves index.html with HMR, proxies /races and /health to FastAPI
// Build: emits to static/ which FastAPI mounts at /static and serves as SPA
//
// TODO [CHORE]: See app/main.py for the plan to fully decouple frontend from
//   backend.  Once done, `base` should be '/' (or a CDN prefix), the `outDir`
//   should target a dist/ folder outside the backend tree, and the API base
//   URL should come from an env-var (import.meta.env.VITE_API_BASE) so the
//   frontend can point at any backend host without a rebuild.
export default defineConfig({
  root: '.',
  base: '/static/',       // production asset URLs become /static/assets/...
  publicDir: 'public',
  build: {
    outDir: 'static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    open: true,
    proxy: {
      '/races': { target: 'http://localhost:8000', ws: true },
      '/videos': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
});
