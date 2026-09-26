#!/bin/sh
# 一次性建出 Gitea 管理员（幂等）。
#
# 为什么必须用 CLI：官方镜像**不认** GITEA_ADMIN_* 环境变量。实测 gitea/gitea:1.22 带那些变量启动
# 后，容器健康、但 GET /api/v1/user 回 401 "user does not exist [uid: 0, name: baguadmin]"——
# 也就是说"看起来起来了"和"有管理员"是两件事。所以这个 sidecar 与 gitea 共用数据卷，直接跑
# `gitea admin user create`。
#
# 幂等：第二次跑会以 "user already exists" 失败（退出码 1），那不是错误——用 `user list` 复核。
set -u

CONF=/data/gitea/conf/app.ini
USER_NAME="${BAGU_ADMIN_USER:-baguadmin}"
USER_PASS="${BAGU_ADMIN_PASSWORD:-baguadmin123}"

for _ in $(seq 1 90); do
  if [ -f "$CONF" ]; then break; fi
  sleep 2
done
if [ ! -f "$CONF" ]; then
  echo "等不到 $CONF：Gitea 的首次初始化没完成（检查 gitea 容器的日志与数据卷权限）" >&2
  exit 1
fi

if gitea admin user create --config "$CONF" \
     --username "$USER_NAME" --password "$USER_PASS" \
     --email "$USER_NAME@example.com" --admin --must-change-password=false; then
  echo "管理员 $USER_NAME 已创建"
  exit 0
fi

if gitea admin user list --config "$CONF" | grep -qw "$USER_NAME"; then
  echo "管理员 $USER_NAME 已存在（幂等）"
  exit 0
fi
echo "建管理员 $USER_NAME 失败，且用户列表里也没有它" >&2
exit 1
