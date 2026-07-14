import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Two things matter here.
 *
 * `base: './'` makes the built asset paths relative, so the Python server can
 * serve dist/ straight from the filesystem without any path rewriting.
 *
 * The proxy is what makes `npm run dev` usable: the dev server (5173) forwards
 * /api to the Python server (8765), so hot reload works against real session
 * data instead of a mock. In production both are served from the same origin,
 * so the proxy simply never comes into play.
 */
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: false,
      },
    },
  },
})
