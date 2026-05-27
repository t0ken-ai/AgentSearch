# AgentSearch — self-hosted HTTP API + MCP server.
#
# Build:
#   docker build -t agentsearch .
#
# Run (HTTP API):
#   docker run -d --name agentsearch \
#       -p 127.0.0.1:8088:8088 \
#       -e AGENTSEARCH_TOKEN="$(openssl rand -hex 32)" \
#       -e FLUXISP_PROXY="http://USER:PASS@host:port" \  # optional
#       --shm-size=1g \
#       agentsearch
#
# The default CMD runs the HTTP API on 0.0.0.0:8088 inside the container —
# bind to 127.0.0.1 on the host and SSH-tunnel from outside, OR put it
# behind a TLS reverse proxy. Never expose port 8088 to the open internet
# without TLS + token.
#
# To run the MCP server instead of the HTTP API, override the CMD:
#   docker run --rm -i agentsearch python -m agent_search.mcp_server
# (MCP uses stdio so you'd typically wrap this in a host-side launcher.)

# ── 1. build a wheel inside a builder image ────────────────────────────
FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Build tools needed only at install time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
COPY agent_search ./agent_search

# Pre-fetch wheels into /wheels so the runtime stage can install offline.
RUN pip wheel --wheel-dir=/wheels -r requirements.txt \
 && pip wheel --wheel-dir=/wheels --no-deps -e .

# ── 2. lean runtime ────────────────────────────────────────────────────
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/var/lib/agentsearch \
    XDG_CACHE_HOME=/var/lib/agentsearch/.cache \
    XDG_DATA_HOME=/var/lib/agentsearch/.local/share \
    AGENTSEARCH_LOG=INFO

# Chromium runtime libraries. CloakBrowser ships its own patched Chromium
# binary on first launch; these libs are what that binary dynamically
# links against on Debian-based images.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tini \
        libnss3 libnspr4 libgbm1 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libcairo2 libpango-1.0-0 libcups2 libdrm2 \
        libxshmfence1 libatk1.0-0 libatk-bridge2.0-0 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
RUN groupadd --system agentsearch \
 && useradd --system --gid agentsearch --home /var/lib/agentsearch \
        --shell /usr/sbin/nologin agentsearch \
 && mkdir -p /var/lib/agentsearch /var/log/agentsearch /opt/AgentSearch \
 && chown -R agentsearch:agentsearch /var/lib/agentsearch /var/log/agentsearch /opt/AgentSearch

WORKDIR /opt/AgentSearch

# Install pre-built wheels from the builder stage.
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels agent-search \
 && rm -rf /wheels

# Copy source so `python -m agent_search.serve` resolves the package
# from /opt/AgentSearch (and venv-less invocation works).
COPY agent_search ./agent_search
COPY pyproject.toml requirements.txt ./

# Drop privileges.
USER agentsearch

# CloakBrowser pulls its patched Chromium on first launch into
# /var/lib/agentsearch/.cloakbrowser/. To bake it into the image at
# build time, uncomment:
#   RUN python -c "from agent_search.core import launch, BrowserConfig; \
#       b = launch(BrowserConfig(headless=True, humanize=False)); b.close()"

EXPOSE 8088

# Tini reaps zombie Chromium children so a runaway browser doesn't accumulate.
ENTRYPOINT ["tini", "--"]

# Default: HTTP API. The host should pass --token / AGENTSEARCH_TOKEN.
# Bind 0.0.0.0 inside the container so docker port-maps work; expose only
# 127.0.0.1:8088 on the host (see -p flag in the example above).
CMD ["python", "-m", "agent_search.serve", "--host", "0.0.0.0", "--port", "8088"]

# Health check uses /health (which still requires the token, so we use
# its 401 vs no-response distinction: any HTTP response = process is up).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:8088/health \
        | grep -E "^(200|401)$" >/dev/null || exit 1
