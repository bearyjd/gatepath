# syntax=docker/dockerfile:1
#
# mockportal-host — runs the existing mockportal/ package on 0.0.0.0:18080.
#
# Build context MUST be the repo root so we can COPY mockportal/ verbatim —
# see ../compose.yml `context: ../..` for the declaration. Matches the same
# pattern used by tests/e2e-docker/gateway/Dockerfile.
#
# mockportal is stdlib-only; no pip install needed.
FROM python:3.12-slim

COPY mockportal /app/mockportal
COPY tests/e2e-android/mockportal-host/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /app
EXPOSE 18080

ENTRYPOINT ["/entrypoint.sh"]
