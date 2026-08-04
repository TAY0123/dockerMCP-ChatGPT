FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG SANDBOX_UID=10001
ARG SANDBOX_GID=10001

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        fd-find \
        git \
        jq \
        nodejs \
        npm \
        python3 \
        python3-pip \
        python3-venv \
        ripgrep \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && groupadd --gid "${SANDBOX_GID}" sandbox \
    && useradd --uid "${SANDBOX_UID}" --gid "${SANDBOX_GID}" --create-home --shell /bin/bash sandbox \
    && mkdir -p /app /workspace /run/runner \
    && chown -R sandbox:sandbox /app /workspace /run/runner /home/sandbox

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install --no-cache-dir . \
    && chown -R sandbox:sandbox /opt/venv

ENV PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORKSPACE=/workspace \
    RUNNER_SOCKET=/run/runner/runner.sock

USER sandbox:sandbox
WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["docker-mcp-server"]
