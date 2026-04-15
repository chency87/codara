# ============================================================
# Unified Agent Gateway (UAG) - Dockerfile
# ============================================================
# Multi-stage build for production deployment
# Stage 1: Python dependencies
# Stage 2: UI build
# Stage 3: Final runtime image
# ============================================================

# ----------------------------------------------------------------
# Stage 1: Python Dependencies
# ----------------------------------------------------------------
FROM python:3.12-slim AS python-deps

WORKDIR /app

# Install uv for fast package management
RUN pip install --no-cache-dir uv

# Copy pyproject.toml for dependency resolution
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev


# ----------------------------------------------------------------
# Stage 2: UI Build
# ----------------------------------------------------------------
FROM node:20-alpine AS ui-deps

WORKDIR /app/ui

# Copy package files
COPY ui/package.json ui/package-lock.json* ./

# Install dependencies
RUN npm ci --legacy-peer-deps

# Copy UI source
COPY ui/ ./

# Build UI (if dist doesn't exist, create empty placeholder)
RUN npm run build || mkdir -p dist


# ----------------------------------------------------------------
# Stage 3: Final Runtime Image
# ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd --gid 1000 codara && \
    useradd --uid 1000 --gid codara --shell /bin/bash --create-home codara

WORKDIR /app

# Copy Python dependencies from stage 1
COPY --from=python-deps /app ./

# Copy built UI from stage 2
COPY --from=ui-deps /app/ui/dist ./ui/dist

# Create directories for data persistence
RUN mkdir -p /data /workspaces /config && \
    chown -R codara:codara /app /data /workspaces /config

# Switch to non-root user
USER codara

# Environment variables (can be overridden at runtime)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UAG_HOST=0.0.0.0 \
    UAG_PORT=8000 \
    UAG_DATABASE_PATH=/data/codara.db \
    UAG_WORKSPACES_ROOT=/workspaces

# Expose the application port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/management/v1/health || exit 1

# Default command (can be overridden)
CMD ["python", "-m", "codara.main"]
