# 后端镜像（platform_api）。compose 里同时被 `platform` 与 `track-loader` 两个服务复用：
# 它们都要 python + git + httpx，只是 entrypoint 不同。
#
# 默认**不含**阅卷工具链：compose 栈开箱用的是桩阅卷。要在这个栈里真跑 maven（D3：阅卷跑在
# "学员自己的机器"上，容器化后那台就是宿主机），用
#   docker compose build --build-arg WITH_GRADING_TOOLCHAIN=1
# 并叠加 deploy/docker-compose.grading.yml（它把 docker socket 与 JDK/Maven 需要的路径挂进来）。
# 注意 socket = 宿主机的 root 等价权限，只在你自己的机器上这么做。
FROM python:3.12-slim AS base

ARG WITH_GRADING_TOOLCHAIN=0

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 真阅卷要的三样：JDK17、Maven、docker CLI + compose v2 插件（Track 的基础设施由 compose 起）。
# 版本钉住：compose 插件的下载地址是 GitHub release，浮动版本会让构建在某天突然不一样。
ARG COMPOSE_VERSION=v2.29.7
RUN if [ "$WITH_GRADING_TOOLCHAIN" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends openjdk-17-jdk-headless maven docker.io \
      && rm -rf /var/lib/apt/lists/* \
      && mkdir -p /usr/local/lib/docker/cli-plugins \
      && curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(dpkg --print-architecture)" \
           -o /usr/local/lib/docker/cli-plugins/docker-compose \
      && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
      && docker compose version; \
    fi

WORKDIR /app

# 依赖只跟着 pyproject 变：先拷它再装依赖，改代码时不必重装。
COPY pyproject.toml README.md ./
COPY platform_service/ ./platform_service/
COPY platform_api/ ./platform_api/
COPY paper_cli/ ./paper_cli/
# content/schema/ 是**运行时数据**，不是测试夹具：content_loader 用它校验 track.yaml 与
# ticket.yaml（`Path(__file__).parent.parent / "content" / "schema"`）。漏掉这一条，容器会在启动
# 时死在 "unknown schema: track"——一个看起来像内容有问题的报错。
COPY content/ ./content/
RUN pip install --no-cache-dir -e ".[service]"

COPY deploy/entrypoint.sh /usr/local/bin/bagu-entrypoint
RUN chmod +x /usr/local/bin/bagu-entrypoint

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=12 \
  CMD curl -fsS http://127.0.0.1:8000/meta || exit 1

ENTRYPOINT ["/usr/local/bin/bagu-entrypoint"]
