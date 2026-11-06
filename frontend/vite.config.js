import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // The browser only talks to the Vite dev server; /api is forwarded to FastAPI.
    // Same origin in the browser, so no CORS, and the SameSite session cookie
    // (M4) just works.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
