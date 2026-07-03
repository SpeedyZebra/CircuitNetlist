const { defineConfig } = require("@playwright/test");

const PORT = Number(process.env.CIRCUIT_NETLIST_TEST_PORT || 8765);
const PYTHON = process.env.CIRCUIT_NETLIST_PYTHON || "python";
const BASE_URL = `http://127.0.0.1:${PORT}`;

module.exports = defineConfig({
  testDir: "./tests/browser",
  timeout: 45_000,
  expect: {
    timeout: 5_000
  },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE_URL,
    browserName: "chromium",
    viewport: { width: 1400, height: 900 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure"
  },
  webServer: {
    command: `${PYTHON} run.py --host 127.0.0.1 --port ${PORT} --no-browser`,
    url: BASE_URL,
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: "pipe",
    stderr: "pipe"
  }
});
