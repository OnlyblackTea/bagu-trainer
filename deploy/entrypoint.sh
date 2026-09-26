#!/bin/sh
# 后端容器的入口：把 track-loader 载入时拿到的 Gitea 令牌接进环境，然后交给 platform_api.serve。
#
# 为什么令牌要绕这一圈：令牌是**运行时**才存在的（loader 启动时才向 Gitea 申请），而容器环境变量在
# 创建时就固定了。所以 loader 把它写进共享卷，这里读进来。想自己指定就设 BAGU_GITEA_TOKEN——这里
# 不覆盖已有值。
set -eu

SECRETS_DIR="${BAGU_SECRETS_DIR:-/run/bagu}"
TOKEN_FILE="$SECRETS_DIR/gitea-token"

if [ -z "${BAGU_GITEA_TOKEN:-}" ] && [ -f "$TOKEN_FILE" ]; then
  BAGU_GITEA_TOKEN="$(cat "$TOKEN_FILE")"
  export BAGU_GITEA_TOKEN
fi

if [ "${BAGU_FORGE:-stub}" = "gitea" ] && [ -z "${BAGU_GITEA_TOKEN:-}" ]; then
  echo "BAGU_FORGE=gitea 但没有可用令牌：track-loader 没成功，或 $SECRETS_DIR 没挂进来。" >&2
  echo "先看 docker compose logs track-loader。" >&2
  exit 3
fi

# 工作区可能是从宿主机绑定挂载进来的（grading 叠加文件就是这么做的），属主与容器里的 root 不一致，
# git 会以 "dubious ownership" 拒绝任何操作。这里只对**工作区根**放行，不开 '*"。
git config --global --add safe.directory '/var/lib/bagu/ws/*' 2>/dev/null || true

exec python -m platform_api.serve "$@"
