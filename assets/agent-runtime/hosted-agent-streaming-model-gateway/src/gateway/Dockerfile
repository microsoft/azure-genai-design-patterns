# Illustrative Dockerfile for the streaming model gateway. Review before production use.
#
# Pattern:
#   - Multi-stage: dependencies are installed in a builder stage from a lock file
#     with hashes; the runtime stage carries only installed packages and app.py.
#   - Approved base image mirrored into your private registry and pinned by digest.
#     There is no default: the build fails unless the pipeline passes one, e.g.
#     --build-arg BASE_IMAGE=<registry>.azurecr.io/approved/python:3.12-slim@sha256:<digest>
#   - Non-root user, read-only application directory, no secrets, no keys and no
#     connection strings. Identity comes from the user-assigned managed identity
#     at runtime; the optional bring-your-own backend key arrives as a Container
#     Apps secret reference to Key Vault.
#   - Exec-form CMD so uvicorn receives SIGTERM directly and drains open streams.
#
# Generate the lock next to this file before building (see ../README.md):
#   pip-compile --generate-hashes requirements.txt -o requirements.lock

ARG BASE_IMAGE

# ---------- builder ----------
FROM ${BASE_IMAGE} AS builder

WORKDIR /build
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes \
        --target /build/site-packages -r requirements.lock

# ---------- runtime ----------
FROM ${BASE_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/site-packages:/app

RUN groupadd --system gateway && useradd --system --gid gateway --no-create-home gateway

WORKDIR /app
COPY --from=builder /build/site-packages /app/site-packages
COPY app.py /app/app.py
RUN chmod -R a-w /app

USER gateway

# Must match the container app's ingress target port and health probes in infra/main.bicep.
EXPOSE 8080

# No HEALTHCHECK: Container Apps probes /healthz. No --proxy-headers or compression
# options: the gateway takes identity from the token, never from forwarded headers,
# and compression would buffer the event stream. uvicorn timeouts and limits can be
# set with UVICORN_* environment variables if your measurements call for them.
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
