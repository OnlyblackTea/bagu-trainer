import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 后端由 `python -m platform_api.serve` 起在 127.0.0.1:8000。开发时把这些 REST 前缀
// 转发过去，前端只写同源相对路径——既不需要 CORS，也不会把后端地址写进产物。
const BACKEND = process.env.BAGU_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    // 显式绑 IPv4：默认的 `localhost` 在本机会先解析到 ::1，而 Playwright / e2e 的
    // 就绪探测走 127.0.0.1，会一直 ECONNREFUSED。
    host: '127.0.0.1',
    port: 5173,
    proxy: Object.fromEntries(
      // **每一个**后端前缀都要在这儿：漏一个，请求就会打到 Vite 自己、拿回 index.html，
      // 前端随后在 JSON.parse 上炸出一句跟真实原因毫无关系的错（真发生过：新加的 /attempts
      // 让"最近练习"显示 `Unexpected token '<'`）。后端有哪些前缀：看 /docs。
      [
        '/tracks',
        '/tickets',
        '/enrollments',
        '/instances',
        '/submissions',
        '/attempts',
        '/meta',
      ].map((p) => [p, BACKEND]),
    ),
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**'],
  },
})
