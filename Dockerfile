FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 默认走国内 PyPI 镜像；构建时可覆盖：
# docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG DEBIAN_MIRROR=http://mirrors.aliyun.com/debian
ARG DEBIAN_SECURITY_MIRROR=http://mirrors.aliyun.com/debian-security
ARG NODE_VERSION=20.19.2
ARG NPM_REGISTRY=https://registry.npmmirror.com

# 用官方 Node 二进制代替 Debian 的 nodejs+npm：后者会拖进 579 个包、710MB。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|https://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|https://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g; \
                s|https://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|https://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|https://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g; \
                s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g; \
                s|https://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
            /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        iputils-ping \
        ca-certificates \
        curl \
        xz-utils \
        libstdc++6; \
    case "$(dpkg --print-architecture)" in \
        amd64) node_arch=x64 ;; \
        arm64) node_arch=arm64 ;; \
        *) echo "unsupported arch: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://npmmirror.com/mirrors/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
        | tar -xJ -C /usr/local --strip-components=1; \
    node --version; \
    npm --version

WORKDIR /app

# 先只拷依赖清单装 node_modules：改 Python 源码不会让这层失效
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --registry "$NPM_REGISTRY" --omit=dev --no-audit --no-fund

COPY pyproject.toml README.md ./
COPY xiaohub ./xiaohub
COPY plugins ./plugins
COPY xiaohub.py ./

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "$PIP_INDEX_URL" --retries 5 --timeout 60 -e ".[xgimi]" \
    && mkdir -p /app/ffmpeg/bin \
    && ln -sf "$(which ffmpeg)" /app/ffmpeg/bin/ffmpeg \
    && ln -sf "$(which ffprobe)" /app/ffmpeg/bin/ffprobe

VOLUME ["/app/conf", "/app/music"]
EXPOSE 8090

CMD ["python", "xiaohub.py"]
