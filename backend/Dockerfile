# syntax=docker/dockerfile:1
#
# Coffee Shop API.
#
# Two stages: the first resolves and installs dependencies into a virtual
# environment, the second copies only that environment and the source.  The
# runtime image therefore carries no compiler, no pip cache and none of the
# development tooling -- it is smaller, and it has a smaller attack surface.

# ---------------------------------------------------------------------------
# Stage 1: build
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# `cryptography` ships wheels for this platform, but build tooling is kept
# here so a dependency without one does not break the image.
RUN apt-get update \
 && apt-get install --no-install-recommends -y build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only the requirements first: this layer is cached until they change,
# so an ordinary source edit does not reinstall the world.
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    FLASK_CONFIG=production \
    # Azure App Service only persists /home; the compose file mounts a volume
    # at the same path so the two behave alike.
    DATABASE_URL=sqlite:////home/data/database.db \
    LOG_FORMAT=json \
    PORT=8000

# curl is here for the HEALTHCHECK below and nothing else.
RUN apt-get update \
 && apt-get install --no-install-recommends -y curl \
 && rm -rf /var/lib/apt/lists/*

# A non-root user. A container that does not need root should not have it:
# it turns a remote-code-execution bug into a much smaller problem.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 coffee \
 && mkdir -p /home/data \
 && chown -R coffee:coffee /home/data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=coffee:coffee . .

# Never ship a developer's .env or a local database into an image.
RUN rm -f .env src/database/database.db

USER coffee

EXPOSE 8000

# Readiness, not liveness: this checks the database too, so an instance that
# cannot serve is taken out of rotation rather than restarted pointlessly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent "http://127.0.0.1:${PORT}/health/ready" || exit 1

# Two workers, two threads: enough concurrency for a SQLite-backed service
# without the write contention more would cause. Raise both once the database
# is Postgres.
CMD ["sh", "-c", "exec gunicorn \
      --bind 0.0.0.0:${PORT} \
      --workers 2 \
      --threads 2 \
      --timeout 60 \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile - \
      wsgi:app"]
