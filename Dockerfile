# JINX engine image (Railway-ready). Phase 1: paper mode only; no live execution path exists.
# Stage 1 fetches the pinned upstreams it needs at runtime (GMGN client, Mako stream) and
# compiles GMGN's TypeScript client unmodified. Node 22: GMGN's undici dependency needs >= 20.18.1.
FROM node:22-bookworm-slim AS upstream
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY UPSTREAM_PINS.json ./
COPY scripts/ ./scripts/
RUN python3 scripts/upstream_sync.py gmgn mako && scripts/build_gmgn_bridge.sh

FROM python:3.12-slim-bookworm
COPY --from=upstream /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Upstream trees ship with their LICENSE files (MIT obligations, see docs/LICENSES.md).
COPY --from=upstream /app/upstream ./upstream
COPY --from=upstream /app/build/gmgn ./build/gmgn
COPY UPSTREAM_PINS.json ./
COPY jinx/ ./jinx/
ENV JINX_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "jinx", "run"]
