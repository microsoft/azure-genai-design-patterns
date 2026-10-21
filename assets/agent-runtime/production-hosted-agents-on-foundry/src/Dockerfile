# Illustrative Dockerfile for a hosted agent container. Not built or tested as-is.
#
# Pattern:
#   - Multi-stage: dependencies are resolved in a builder stage, the runtime
#     stage carries only the installed packages and the app code.
#   - Approved base image, pinned by digest (replace <digest> with the digest
#     your platform team publishes; do not use floating tags in production).
#   - Non-root user, read-only application directory, no secrets, no keys,
#     no connection strings. Identity comes from the platform at runtime.
#   - Exec-form CMD so the process receives SIGTERM directly and can flush
#     telemetry before exit.
#   - Hosted agent sandboxes run linux/amd64 images (observed in preview; verify).
#     Build with: docker build --platform linux/amd64 -t <registry>.azurecr.io/<repo>:<build-id> .
#     Push, record the returned digest, and reference the image by digest in the agent version.

# Base images are mirrored into the private registry so the in-network build
# does not depend on public egress.
ARG BASE_IMAGE=<registry>.azurecr.io/approved/python:3.12-slim@sha256:<digest>

# ---------- builder ----------
FROM ${BASE_IMAGE} AS builder

WORKDIR /build

# Lock file with hashes produced by your dependency tool. Installing with
# --require-hashes makes the dependency set reproducible and tamper-evident.
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes \
        --target /build/site-packages -r requirements.lock

# ---------- runtime ----------
FROM ${BASE_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/site-packages:/app

# Dedicated non-root user. The app directory is owned by root and only
# readable by the app user, so the running process cannot modify its code.
RUN groupadd --system agent && useradd --system --gid agent --home-dir /home/agent --create-home agent

WORKDIR /app
COPY --from=builder /build/site-packages /app/site-packages
COPY app_skeleton.py /app/app_skeleton.py
RUN chmod -R a-w /app

# Session-local scratch space is a cache, not a store. Durable state lives in
# Cosmos DB (see docs/architecture.md, section 10 and 14.6).
ENV AGENT_SCRATCH_DIR=/home/agent/scratch

USER agent

# Samples use port 8088 for the hosting adapter; verify the current contract.
EXPOSE 8088

# No HEALTHCHECK instruction: the platform owns liveness. The app exposes a
# cheap health handler, and readiness is proven by a real minimal turn from
# the pipeline (see docs/architecture.md, section 6.3).

CMD ["python", "-m", "app_skeleton"]
