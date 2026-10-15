import {defineConfig, devices} from '@playwright/test';

const PORT = 4300;

export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.results',
  snapshotPathTemplate: '{testDir}/__baselines__/{testFilePath}/{arg}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env['CI'],
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `npx ng serve --port ${PORT} --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: true,
    timeout: 180_000,
  },
  projects: [
    {name: 'chromium', use: {...devices['Desktop Chrome'], deviceScaleFactor: 1}},
    // Mobile Safari engine with touch; behaviour checks only (screenshot baselines are Chromium's).
    {name: 'webkit-mobile', use: {...devices['iPhone 13']}, testMatch: /responsive[\\/]drawers\.spec\.ts$/},
  ],
});
