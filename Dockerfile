# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Codara production image
#
# The image builds the Vite dashboard, installs the Python package into a
# self-contained virtualenv, installs provider CLIs, and includes common
# project-development tools so provider agents can create/build workspaces.
# Runtime state is expected to live in mounted volumes:
# /data, /config, /logs, and /workspaces.
# ---------------------------------------------------------------------------

# Stage 1: Build the UI
FROM node:24-slim AS ui-builder

WORKDIR /app/ui

# Install dependencies first (better caching)
COPY ui/package.json ui/package-lock.json ./
RUN npm ci

# Copy source and build
COPY ui/ ./
RUN npm run build


# Stage 2: Build the Python environment
FROM python:3.12-slim AS python-builder

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Install dependencies first
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Use uv to sync the environment from the lockfile
RUN uv venv "$VIRTUAL_ENV" \
    && uv sync --frozen --no-dev


# Stage 3: Final Runtime Image
FROM python:3.12-slim AS runtime

ARG CODEX_CLI_PACKAGE="@openai/codex@latest"
ARG GEMINI_CLI_PACKAGE="@google/gemini-cli@latest"
ARG OPENCODE_CLI_PACKAGE="opencode-ai@latest"
ARG PNPM_PACKAGE="pnpm@latest"
ARG NODE_VERSION="24"
ARG NVM_VERSION="0.40.3"
ARG USERNAME="codara"
ARG USER_UID=1000
ARG USER_GID=1000

ENV VIRTUAL_ENV=/app/.venv \
    NVM_DIR=/home/${USERNAME}/.nvm \
    PATH="/app/.venv/bin:/home/${USERNAME}/.local/bin:/home/${USERNAME}/.nvm/versions/node/v${NODE_VERSION}/bin:$PATH" \
    HOME=/home/${USERNAME} \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UAG_HOST=0.0.0.0 \
    UAG_PORT=8000 \
    UAG_CONFIG_PATH=/config/codara.toml \
    UAG_CONFIG_DIR=/config \
    UAG_DATABASE_PATH=/data/codara.db \
    UAG_WORKSPACES_ROOT=/workspaces \
    UAG_LOGS_ROOT=/logs

# Install system dependencies
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        git \
        graphviz \
        locales \
        openssh-client \
        sqlite3 \
        sudo \
        tini \
        tmux \
        vim \
        wget \
    && sed -i 's/^# *\(en_US.UTF-8 UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

# Setup user and directories
RUN groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd \
        --uid "${USER_UID}" \
        --gid "${USER_GID}" \
        --shell /bin/bash \
        --create-home \
        --no-log-init \
        "${USERNAME}" \
    && mkdir -p \
        /app \
        /data \
        /config \
        /logs \
        /workspaces \
        "/home/${USERNAME}/.local/bin" \
        "/home/${USERNAME}/.local/share" \
        "/home/${USERNAME}/.history" \
    && chown -R "${USERNAME}:${USERNAME}" \
        /app \
        /data \
        /config \
        /logs \
        /workspaces \
        "/home/${USERNAME}" \
    && echo "${USERNAME} ALL=(root) NOPASSWD:ALL" > "/etc/sudoers.d/${USERNAME}" \
    && chmod 0440 "/etc/sudoers.d/${USERNAME}" \
    && git config --system --add safe.directory '*'

WORKDIR /app

# Copy built artifacts
COPY --from=python-builder --chown=${USER_UID}:${USER_GID} /app/.venv /app/.venv
COPY --from=python-builder --chown=${USER_UID}:${USER_GID} /app/src /app/src
COPY --from=python-builder --chown=${USER_UID}:${USER_GID} /app/pyproject.toml /app/README.md ./
COPY --from=ui-builder --chown=${USER_UID}:${USER_GID} /app/ui/dist /app/ui/dist

USER ${USERNAME}

# Install NVM, Node.js and provider CLIs
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh" | bash \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install "$NODE_VERSION" \
    && nvm alias default "$NODE_VERSION" \
    && nvm use default \
    && npm install -g \
        "${CODEX_CLI_PACKAGE}" \
        "${GEMINI_CLI_PACKAGE}" \
        "${OPENCODE_CLI_PACKAGE}" \
        "${PNPM_PACKAGE}" \
    && npm cache clean --force

# Setup shell environment
RUN printf '%s\n' \
        'export HISTFILE=$HOME/.history/.bash_history' \
        'export HISTSIZE=10000' \
        'export HISTFILESIZE=20000' \
        'export HISTCONTROL=ignoredups:erasedups' \
        'shopt -s histappend' \
        'PROMPT_COMMAND="history -a; history -c; history -r"' \
        'export NVM_DIR=$HOME/.nvm' \
        '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"' \
        'export PATH=/app/.venv/bin:$HOME/.local/bin:$PATH' \
        '[[ $- == *i* ]] && bind "\e[A":history-search-backward' \
        '[[ $- == *i* ]] && bind "\e[B":history-search-forward' \
        > "$HOME/.bashrc" \
    && printf '%s\n' 'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi' > "$HOME/.bash_profile"

EXPOSE 8000

# Use tini as init and start the server
ENTRYPOINT ["tini", "--"]
CMD ["codara", "dev", "--host", "0.0.0.0", "--port", "8000"]
