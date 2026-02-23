import { defineConfig } from 'vite';

// Vite root = app/frontend/
// Dev:   serves index.html with HMR, proxies /races and /health to FastAPI
// Build: emits to static/ which FastAPI mounts at /static and serves as SPA
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
      '/races': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
});
