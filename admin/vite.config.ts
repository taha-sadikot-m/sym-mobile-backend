import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Built into the backend: FastAPI serves app/static/admin at /admin.
export default defineConfig({
  base: '/admin/',
  plugins: [react()],
  build: {
    outDir: '../app/static/admin',
    emptyOutDir: true,
  },
  server: {
    port: 5180,
    proxy: { '/admin/api': 'http://localhost:8000' },
  },
});
