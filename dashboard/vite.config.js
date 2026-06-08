/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Only enable esbuild for dev/test (tests need it for JSX). Production build uses oxc.
export default defineConfig(({ command }) => ({
  ...(command !== 'build' && { esbuild: { jsx: 'automatic' } }),
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
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    css: false,
    server: {
      deps: {
        inline: ['@testing-library/react'],
      },
    },
  },
}))
