/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': '/src' } },
  server: { port: 5173, host: '0.0.0.0', proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom' },
})
