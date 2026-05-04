import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import dotenv from 'dotenv'
import path from 'path'

// Load config.env manually because Vite only looks for .env by default
const envPath = path.resolve(__dirname, 'config.env')
const envConfig = dotenv.config({ path: envPath }).parsed || {}

export default defineConfig({
  plugins: [react()],
  base: '/website/',
  define: {
    // Inject the VITE_API_URL or other envs into import.meta.env
    'import.meta.env.VITE_API_URL': JSON.stringify(envConfig.VITE_API_URL || ''),
  },
  server: {
    port: 5173,
    proxy: {
      '/panel-api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
