#!/usr/bin/env bash
# 一条命令把 bagu-trainer 跑起来（拓扑 A：全部装在你自己的机器上）。
#
#   bash scripts/quickstart.sh --content-dir ../seckill-lab
#
# 它做四件事：建 venv → 装依赖 → 构建前端（有 node 的话）→ 起后端并打印地址。
# 默认就是**能持久**的配置：SQLite + 桩阅卷（零外部依赖，重启不丢进度）。
#
# 想换真阅卷 / 真 Forge / PostgreSQL，用 `--` 把参数原样转给 platform_api.serve：
#
#   bash scripts/quickstart.sh --content-dir ../seckill-lab -- --grading docker --forge gitea
#
# 不是"安装器"：它不碰系统包（JDK/Maven/Docker 要你自己装）、不写 systemd unit、不改你的 git 配置。
# 那些是部署决策，写死在脚本里只会让你以后看不懂自己的机器。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8000
HOST=127.0.0.1
STORE="${BAGU_STORE:-sqlite}"
DATABASE_URL="${BAGU_DATABASE_URL:-}"
CONTENT_DIR=""
SKIP_WEB=0
PYTHON="${PYTHON:-python3}"
PASSTHROUGH=()

usage() {
  cat <<'EOF'
用法: bash scripts/quickstart.sh [选项] [-- <platform_api.serve 的参数>]

  --content-dir DIR   把哪个 Track 目录部署上来（可重复；不给就只跑内置 demo Track）
  --port N            监听端口（默认 8000）
  --host ADDR         监听地址（默认 127.0.0.1，只给本机用）
  --store NAME        sqlite（默认）/ postgres / memory
  --database-url URL  sqlite 是文件路径；postgres 是 libpq 连接串
  --skip-web          不构建前端
  -h, --help          这段文字
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --content-dir) CONTENT_DIR="${CONTENT_DIR:+$CONTENT_DIR }$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --store) STORE="$2"; shift 2 ;;
    --database-url) DATABASE_URL="$2"; shift 2 ;;
    --skip-web) SKIP_WEB=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; PASSTHROUGH=("$@"); break ;;
    *) echo "未知参数：$1" >&2; usage >&2; exit 2 ;;
  esac
done

venv_python() { echo "$REPO/.venv/bin/python"; }

# uv 的官方安装位置是 ~/.local/bin，而它**不一定**在非登录 shell 的 PATH 里（很多人装完忘了 source
# profile）。既然它能让"发行版没装 python3-venv"这类环境照样跑起来，这里就顺手找一下。
if ! command -v uv >/dev/null 2>&1 && [ -x "$HOME/.local/bin/uv" ]; then
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

VENV_HINT='建 venv 失败。最常见的原因是发行版把 venv/ensurepip 拆成了单独的包：
  Debian / Ubuntu:  sudo apt install python3-venv
  Fedora / RHEL:    sudo dnf install python3-virtualenv
  Alpine:           sudo apk add python3 py3-virtualenv
或者装 uv（https://docs.astral.sh/uv/）：`uv venv` 不需要这些系统包，本脚本检测到 uv 会自动用它。'

# --- 1) Python：>=3.11（pyproject 的 requires-python）--------------------------------
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "找不到 $PYTHON。装一个 Python 3.11+ 再来（或用 PYTHON=... 指定解释器）。" >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "$PYTHON 版本太旧：需要 3.11+（当前 $("$PYTHON" -V 2>&1)）。" >&2
  exit 1
fi

# --- 2) venv + 依赖（已存在就复用：反复跑这个脚本不该重装）---------------------------
VENV="$REPO/.venv"
if [ ! -x "$(venv_python)" ]; then
  if command -v uv >/dev/null 2>&1; then
    # uv 优先：`python -m venv` 在不少发行版上因为 ensurepip 被拆包而直接失败（见下面的提示）。
    echo "==> 建 venv（uv）：$VENV"
    uv venv "$VENV" || exit 2
  else
    echo "==> 建 venv（python -m venv）：$VENV"
    if ! "$PYTHON" -m venv "$VENV"; then
      echo >&2
      echo "$VENV_HINT" >&2
      exit 2
    fi
  fi
fi
if ! command -v uv >/dev/null 2>&1 && ! "$(venv_python)" -m pip --version >/dev/null 2>&1; then
  # venv 建出来了但没有 pip（ensurepip 被拆包时的典型残局），而这里又没有 uv 可用。
  echo "==> $VENV 里没有可用的 pip。" >&2
  echo "$VENV_HINT" >&2
  exit 2
fi
EXTRAS="service"
if [ "$STORE" = "postgres" ]; then EXTRAS="service,postgres"; fi
echo "==> 安装依赖（extras: $EXTRAS）"
if command -v uv >/dev/null 2>&1; then
  VIRTUAL_ENV="$VENV" uv pip install --quiet -e "$REPO[$EXTRAS]" || exit 2
else
  "$(venv_python)" -m pip install --quiet --upgrade pip
  "$(venv_python)" -m pip install --quiet -e "$REPO[$EXTRAS]" || exit 2
fi

# --- 3) 前端：有 node 就构建成静态文件，没有就只提示（后端本身不依赖前端）-------------
WEB_DIR="$REPO/frontend/dist"
if [ "$SKIP_WEB" -eq 0 ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "==> 构建前端"
    ( cd "$REPO/frontend" && npm install --silent && npm run build )
  else
    echo "==> 没装 node/npm：跳过前端构建。后端照常工作，你可以另找一台机器跑前端。"
  fi
fi

# --- 4) 起后端 ----------------------------------------------------------------------
ARGS=(--host "$HOST" --port "$PORT" --store "$STORE")
if [ -n "$DATABASE_URL" ]; then ARGS+=(--database-url "$DATABASE_URL"); fi
if [ -n "$CONTENT_DIR" ]; then
  for dir in $CONTENT_DIR; do ARGS+=(--content-dir "$dir"); done
fi
if [ ${#PASSTHROUGH[@]} -gt 0 ]; then ARGS+=("${PASSTHROUGH[@]}"); fi

BACKEND="http://$HOST:$PORT"
echo
echo "==> 后端：$BACKEND   （进度存储：$STORE）"
if [ -d "$WEB_DIR" ]; then
  echo "==> 前端（静态）：把 $WEB_DIR 交给任意静态服务器，或在本机跑："
  echo "    python3 -m http.server 5173 --directory $WEB_DIR"
  echo "    前端与后端不同源时，需要把 /tracks /tickets /enrollments /instances /submissions /attempts /meta"
  echo "    这些前缀反代到 $BACKEND（开发时由 Vite 代理负责，见 frontend/vite.config.ts）。"
else
  echo "==> 前端：cd frontend && npm install && npm run dev （Vite 会把上面那些前缀代理到 \$BAGU_BACKEND）"
fi
if [ "$HOST" = "0.0.0.0" ]; then
  echo "!! 监听 0.0.0.0 且平台**没有认证**：确认前面有反向代理/防火墙，别把端口直接开到公网。"
fi
echo "==> 自检：curl -s $BACKEND/meta"
echo

cd "$REPO"
exec "$(venv_python)" -m platform_api.serve "${ARGS[@]}"
