import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Tauri 期望固定端口与 dist 输出
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    target: 'esnext',
  },
});
