# syntax=docker/dockerfile:1

# --- Builder: resolve deps with uv, produce a venv ---
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Install deps first (cache layer independent of source changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Now install the project itself
COPY src ./src
RUN uv sync --frozen --no-dev

# --- Runtime: slim image, no compilers ---
FROM python:3.11-slim AS runtime

# lightgbm's prebuilt wheel needs libgomp at runtime; libpq5 for psycopg2-binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
# scripts/run_*.py — lets a one-off ECS task run modeling scripts inside the VPC
# instead of over a bandwidth-capped local SSM tunnel (see scripts/run_in_ecs.sh).
# All their deps (pandas/sklearn/mlflow/etc.) are already in the venv above.
COPY scripts ./scripts
# player_projection.py's DEFAULT_CACHE_DIR resolves find_repo_root() at import
# time (players.py imports it transitively) — pyproject.toml only needs to
# exist here as find_repo_root()'s anchor, not be otherwise meaningful in-container.
COPY pyproject.toml ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8000

CMD ["uvicorn", "portalpoint.main:app", "--host", "0.0.0.0", "--port", "8000"]
