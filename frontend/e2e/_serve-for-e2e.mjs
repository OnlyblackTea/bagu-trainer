/**
 * e2e 的后端启动器（Playwright 的 webServer 用它，而不是在 `command` 里拼 shell 字符串）。
 *
 * 为什么需要它：默认 store 是 SQLite（持久），所以"换个端口"不足以隔离——数据库文件会跨轮留下，
 * 上一轮入学过的阶段让这一轮连「入学」按钮都看不到。删除 + 启动这两件事要么写成跨平台的 Node，
 * 要么就得跟 `cmd /c` 的引号与通配符搏斗（试过，Playwright 传参时那句 `del` 会被拆坏）。
 *
 * 后端进程继承 stdio，所以 Playwright 的 webServer 日志照旧能看到 `/meta`、`/tracks` 的访问记录。
 */
import { spawn } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repo = resolve(here, '..', '..')
const e2eRoot = join(repo, 'scratch', '_e2e')
const db = join(e2eRoot, 'bagu.sqlite3')

for (const suffix of ['', '-wal', '-shm']) {
  const f = db + suffix
  if (existsSync(f)) rmSync(f, { force: true })
}

const port = process.env.E2E_BACKEND_PORT ?? '8100'
const python = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'

const child = spawn(
  python,
  [
    '-m',
    'platform_api.serve',
    '--port',
    port,
    '--workspace-root',
    'scratch/_e2e',
    '--store',
    'sqlite',
    '--database-url',
    'scratch/_e2e/bagu.sqlite3',
  ],
  { cwd: repo, stdio: 'inherit' },
)

child.on('exit', (code) => process.exit(code ?? 0))
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig))
