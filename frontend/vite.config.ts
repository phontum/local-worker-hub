import { defineConfig } from 'vitest/config';

// `npm run dev` serves the UI on its own port; forward API calls to the running hub so the pairing cookie flow works.
export default defineConfig({ server: { proxy: { '/api': 'http://127.0.0.1:8765' } }, test: { include: ['src/**/*.test.ts'] } });
