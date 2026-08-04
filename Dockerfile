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
        passwd \
        python3 \
        python3-pip \
        python3-venv \
        ripgrep \
        tini \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && groupadd --gid "${SANDBOX_GID}" sandbox \
    && useradd --uid "${SANDBOX_UID}" --gid "${SANDBOX_GID}" --create-home --shell /bin/bash sandbox \
    && mkdir -p /app /workspace /run/runner \
    && chown -R sandbox:sandbox /app /workspace /run/runner /home/sandbox

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts/runner-entrypoint.sh /usr/local/bin/runner-entrypoint.sh

RUN chmod 0755 /usr/local/bin/runner-entrypoint.sh \
    && python3 -m venv /opt/venv \
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
