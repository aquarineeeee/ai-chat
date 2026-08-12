import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:10000',
      '/health': 'http://127.0.0.1:10000',
    },
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
  },
})
