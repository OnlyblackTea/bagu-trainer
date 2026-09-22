import { defineConfig, devices } from '@playwright/test'

/**
 * 真浏览器 e2e（spec §C.9 的「UI 改动必须在浏览器里验过」铁律）。
 *
 * 本机没有 browser-use MCP，Playwright 是可安装、可重复、可进 CI 的等价物。
 *
 * **状态隔离**：默认 store 是 SQLite（重启不失忆），所以"用独立端口"不足以隔离——数据库文件
 * 也会跨轮留下来，上一轮入学过的阶段会让这一轮连「入学」按钮都看不到（真发生过：e2e 直接超时）。
 * 于是这里把数据库也指到 scratch/_e2e 下，并由下面的启动器在每次跑之前删掉它。
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
      // A′ 后端（demo Track；工作区与数据库都隔离在 scratch/_e2e，且每次开跑前先删库）。
      // 用 Node 启动器而不是在 command 里拼 shell：`cmd /c "if exist … del …"` 的引号与
      // 通配符会被 Playwright 传参拆坏（"Invalid switch"），而且那也不是跨平台的写法。
      command: 'node e2e/_serve-for-e2e.mjs',
      cwd: '.',
      env: { E2E_BACKEND_PORT: String(BACKEND_PORT) },
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
