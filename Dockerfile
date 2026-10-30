# syntax=docker/dockerfile:1

FROM node:25-slim AS web
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN --mount=type=cache,id=jzmedia-npm,target=/root/.npm,sharing=locked \
    if [ -n "$HTTP_PROXY" ]; then npm config set proxy "$HTTP_PROXY" && npm config set https-proxy "$HTTPS_PROXY"; fi \
 && npm ci --prefer-offline --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM node:25-slim AS help
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
WORKDIR /help
COPY docs/package.json docs/package-lock.json ./
RUN --mount=type=cache,id=jzmedia-npm,target=/root/.npm,sharing=locked \
    if [ -n "$HTTP_PROXY" ]; then npm config set proxy "$HTTP_PROXY" && npm config set https-proxy "$HTTPS_PROXY"; fi \
 && npm ci --prefer-offline --no-audit --no-fund
COPY docs/ ./
COPY version.properties /version.properties
RUN npm run build && npm run check

FROM python:3.12-slim-trixie

# 构建期代理（NAS直连留空；WSL经Docker Desktop时在compose里填BUILD_HTTP_PROXY）
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG http_proxy=""
ARG https_proxy=""
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 转色组件固定为 NAS 实片验证过的组合；更新前重新验证，不随 apt 重建漂移。
# cifs-utils/nfs-common：应用内挂载远程库（C 阶段）；无此需求也可保留（体积很小）
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ffmpeg=7:7.1.5-0+deb13u1 mesa-vulkan-drivers=25.0.7-2+deb13u1 \
      libplacebo349=7.349.0-3 libva2=2.22.0-3 libvulkan1=1.4.309.0-1 \
      cifs-utils nfs-common tzdata \
 && rm -rf /var/lib/apt/lists/*

# P5 网页播放使用完整 iHD 驱动；其他架构保持能力不足时明确阻断的行为。
# 复制发行版源以继承实际 Signed-By（Trixie 使用 .pgp，不硬编码密钥文件）。
ARG INTEL_MEDIA_DRIVER_VERSION="25.2.3+ds1-1"
ARG INTEL_GMM_VERSION="22.7.2+ds1-1"
ARG INTEL_MEDIA_DRIVER_SHA256="e0aec5839e3a6c41b7b1815ed944b85c1334d38ee744165c9788f0e0fc8803f9"
ARG INTEL_GMM_SHA256="81b668213cb59d2bcae8c66b1184277fd0228e3648ab63f4f2b3b578da291b06"
RUN if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
      sed 's/^Components: .*/Components: non-free/' /etc/apt/sources.list.d/debian.sources \
        > /etc/apt/sources.list.d/intel-media-non-free.sources \
      && apt-get update \
      && mkdir -p /tmp/jzmedia-intel-driver /usr/share/jzmedia \
      && cd /tmp/jzmedia-intel-driver \
      && apt-get download "intel-media-va-driver-non-free=${INTEL_MEDIA_DRIVER_VERSION}" \
                          "libigdgmm12=${INTEL_GMM_VERSION}" \
      && printf '%s  %s\n' \
           "$INTEL_MEDIA_DRIVER_SHA256" "intel-media-va-driver-non-free_${INTEL_MEDIA_DRIVER_VERSION}_amd64.deb" \
           "$INTEL_GMM_SHA256" "libigdgmm12_${INTEL_GMM_VERSION}_amd64.deb" \
           | sha256sum -c - \
      && sha256sum ./*.deb > /usr/share/jzmedia/intel-driver-sha256.txt \
      && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ./*.deb \
      && dpkg-query -W -f='${Package}=${Version}\n' intel-media-va-driver-non-free libigdgmm12 \
        > /usr/share/jzmedia/intel-driver-packages.txt \
      && cd / && rm -rf /tmp/jzmedia-intel-driver /var/lib/apt/lists/*; \
    fi

COPY requirements.txt .
RUN --mount=type=cache,id=jzmedia-pip,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt

# 每次发布变化的参数放在依赖安装之后，避免使 apt/pip 安装层失效。
ARG APP_VERSION="dev"
ARG GIT_SHA="unknown"
ENV JZMEDIA_BUILD_COMMIT=$GIT_SHA
LABEL org.opencontainers.image.title="jzmedia" \
      org.opencontainers.image.source="https://github.com/nojobnopay/jzmedia" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA

COPY app ./app
COPY LICENSE ./LICENSE
COPY version.properties ./version.properties
COPY --from=web /web/dist ./frontend/dist
COPY --from=help /help/.vitepress/dist ./docs/.vitepress/dist

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
