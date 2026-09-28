import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: ["2d5a7b582c3e4e1c84de00c86c3cc42d.vfs.cloud9.us-east-1.amazonaws.com"],
    host: true,
    port: 8080
  },
})
