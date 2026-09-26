# 后端镜像（platform_api）。compose 里 `platform` 与 `track-loader` 两个服务都用它，只是 entrypoint
# 不同，所以这两个服务都要 python + git + httpx。
#
# **两个 target**：
#   * `lean`（默认）：桩阅卷，小（不含 JDK/Maven）。
#   * `grading`：真阅卷——多一份 JDK17 + Maven + docker CLI/compose。由
#     deploy/docker-compose.grading.yml 指定（那条命令同时提供 docker socket 与绑定挂载）。
#
# 工具链**从官方镜像拷**，不走 apt：apt 的包名/源在各 base 镜像上并不一致（实测
# `openjdk-17-jdk-headless` 在某个 python:3.12-slim 上直接 "Unable to locate package"，
# 而构建到一半失败留下的是一堆更难查的症状）。拷过来的东西版本确定、可复现。
FROM python:3.12-slim AS base

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

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

# 默认 target：桩阅卷，最小。
FROM base AS lean

# --- 工具链来源（只有构建 grading 目标时才会被拉取）--------------------------------
# JDK17 + Maven：temurin 镜像里 JAVA_HOME=/opt/java/openjdk，Maven 在 /usr/share/maven。
FROM maven:3.9-eclipse-temurin-17 AS jdk
# docker CLI + compose v2 插件：Track 的基础设施（MySQL/Redis）由 `docker compose` 起，
# 而守护进程在宿主机上（socket 由 compose 挂进来）。
FROM docker:27-cli AS dockercli

# 真阅卷。JDK/Maven/compose 三样齐全，构建时自检一次——宁可红在构建，也别红在阅卷。
FROM base AS grading
COPY --from=jdk /opt/java /opt/java
COPY --from=jdk /usr/share/maven /usr/share/maven
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/lib/docker/cli-plugins/docker-compose
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH=/opt/java/openjdk/bin:/usr/share/maven/bin:$PATH
RUN java -version && mvn -v | head -1 && docker --version && docker compose version
