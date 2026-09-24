import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests', fullyParallel: false, workers: 1, timeout: 60000,
  reporter: [['list'], ['json', {outputFile:'test-results/results.json'}]],
  use: { baseURL:'http://127.0.0.1:13060', channel:'chrome', headless:true, trace:'retain-on-failure', screenshot:'only-on-failure' }
});
