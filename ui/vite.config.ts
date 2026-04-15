import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  base: '/dashboard',
  server: {
    proxy: {
      '/v1': 'http://localhost:8000',
      '/management': 'http://localhost:8000',
    }
  }
})
