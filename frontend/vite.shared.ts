import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export function appConfig(name: 'student' | 'admin') {
  return defineConfig({
    base: `/${name}/`,
    plugins: [react()],
    server: {
      fs: { allow: [fileURLToPath(new URL('.', import.meta.url))] },
      proxy: { '/api': { target: 'http://127.0.0.1:4003', changeOrigin: false } },
    },
    build: { outDir: `../../dist/${name}`, emptyOutDir: true, target: ['chrome109', 'edge109', 'safari16.4'] },
  });
}
