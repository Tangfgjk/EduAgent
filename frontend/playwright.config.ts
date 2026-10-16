import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';

const python = resolve('..', process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
export default defineConfig({
  testDir: './e2e', workers: 1, fullyParallel: false, timeout: 30_000,
  expect: { timeout: 7000 },
  use: { baseURL: 'http://127.0.0.1:8781', viewport: { width: 1440, height: 1000 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium', ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}) } }],
  webServer: { command: `"${python}" ../scripts/serve_frontend_test.py`, url: 'http://127.0.0.1:8781/health/ready', reuseExistingServer: false, timeout: 30_000 },
});
