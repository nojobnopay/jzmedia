FROM node:25-slim AS web
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -n "$HTTP_PROXY" ]; then npm config set proxy "$HTTP_PROXY" && npm config set https-proxy "$HTTPS_PROXY"; fi \
 && npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM node:25-slim AS help
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
WORKDIR /help
COPY docs/package.json docs/package-lock.json ./
RUN if [ -n "$HTTP_PROXY" ]; then npm config set proxy "$HTTP_PROXY" && npm config set https-proxy "$HTTPS_PROXY"; fi \
 && npm ci --no-audit --no-fund
COPY docs/ ./
RUN npm run build

FROM python:3.12-slim

# 构建期代理（NAS直连留空；WSL经Docker Desktop时在compose里填BUILD_HTTP_PROXY）
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG http_proxy=""
ARG https_proxy=""
ARG APP_VERSION="dev"
ARG GIT_SHA="unknown"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.title="jzmedia" \
      org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.revision=$GIT_SHA

COPY requirements.txt .
# cifs-utils/nfs-common：应用内挂载远程库（C 阶段）；无此需求也可保留（体积很小）
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg cifs-utils nfs-common \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=web /web/dist ./frontend/dist
COPY --from=help /help/.vitepress/dist ./docs/.vitepress/dist

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
