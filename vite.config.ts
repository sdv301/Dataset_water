import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    base: '/flood/v2/',
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      port: 3547,
      strictPort: true,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
      hmr: process.env.DISABLE_HMR !== 'true',
      watch: process.env.DISABLE_HMR === 'true'
        ? null
        : {
            ignored: [
              '**/models/**',
              '**/data/**',
              '**/*.joblib',
              '**/*.db',
              '**/python_code/**',
              '**/Реки/**',
              '**/dist/**',
            ],
          },
    },
    build: {
      // Разбиваем крупные зависимости на отдельные чанки, чтобы убрать warning >500kB
      // и улучшить кэш браузера (maplibre грузится только на вкладках с картой).
      // Функция вместо списка пакетов — обходит отсутствие "." exports у react-map-gl.
      // Лимит 1100: maplibre-gl неделим и весит ~1МБ сам по себе (не app-код).
      chunkSizeWarningLimit: 1100,
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes('node_modules/maplibre-gl') || id.includes('node_modules/react-map-gl') || id.includes('node_modules/@vis.gl')) return 'vendor-maplibre';
            if (id.includes('node_modules/recharts')) return 'vendor-charts';
            if (id.includes('node_modules/motion') || id.includes('node_modules/framer-motion')) return 'vendor-motion';
            return undefined;
          },
        },
      },
    },
  };
});
