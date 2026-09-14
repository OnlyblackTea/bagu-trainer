import { defineConfig, devices } from '@playwright/test'

/**
 * 真浏览器 e2e（spec §C.9 的「UI 改动必须在浏览器里验过」铁律）。
 *
 * 本机没有 browser-use MCP，Playwright 是可安装、可重复、可进 CI 的等价物。
 *
 * 用**独立端口**且 `reuseExistingServer: false`：后端的 store 是内存态，复用旧进程会
 * 让上一轮跑出来的实例状态（已 passed 的阶段不可重做）污染这一轮。
 */

const BACKEND_PORT = 8100
const FRONTEND_PORT = 5183
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`
const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}`

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  // 一个后端 store 被所有用例共享；用例按文件顺序串行，避免互相踩状态。
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: FRONTEND_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // A′ 后端（默认用 demo Track；工作区隔离在 scratch/_e2e）。
      // 反斜杠是必要的：这条命令由 Windows 的 cmd.exe 执行，正斜杠路径会被当成命令名。
      command:
        '.venv\\Scripts\\python.exe -m platform_api.serve ' +
        `--port ${BACKEND_PORT} --workspace-root scratch/_e2e`,
      cwd: '..',
      url: `${BACKEND_URL}/tracks`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --port ${FRONTEND_PORT} --strictPort`,
      cwd: '.',
      env: { BAGU_BACKEND: BACKEND_URL },
      url: FRONTEND_URL,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
