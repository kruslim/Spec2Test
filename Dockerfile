# The hosted app (`spec2test deploy serve`). Partner data, the usage ledger, the deployment
# config and Docling's model cache all live on the volume mounted at /data; the image holds
# code only. See docs/DEPLOY.md.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv

# Docling's PDF pipeline loads OpenCV, which needs these system libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/data/cache/huggingface \
    SPEC2TEST_DEPLOY_CONFIG=/data/deploy.toml

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra deploy --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra deploy

EXPOSE 8080
# Root, because a host's volume is mounted owned by root; the container runs nothing else.
CMD ["sh", "-c", "exec /app/.venv/bin/spec2test deploy serve \"$SPEC2TEST_DEPLOY_CONFIG\" --port 8080"]
