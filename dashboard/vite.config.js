import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/studio_outputs': 'http://127.0.0.1:8000',
      '/static': 'http://127.0.0.1:8000',
    }
  },
  plugins: [
    tailwindcss(),
    react()
  ],
})
