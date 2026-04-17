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

FROM node:20-alpine AS ui-builder

WORKDIR /app/ui

COPY ui/package.json ui/package-lock.json ./
RUN npm ci

COPY ui/ ./
RUN npm run build


FROM node:20-bookworm-slim AS node-runtime


FROM python:3.12-slim AS python-builder

ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv venv "$VIRTUAL_ENV" \
    && uv pip install -r uv.lock \
    && uv pip install --no-deps .


FROM python:3.12-slim AS runtime

ARG CODEX_CLI_PACKAGE="@openai/codex"
ARG GEMINI_CLI_PACKAGE="@google/gemini-cli"
ARG OPENCODE_CLI_PACKAGE="opencode-ai"
ARG USERNAME="codara"
ARG USER_UID=1000
ARG USER_GID=1000

ARG NODE_VERSION="24"
ARG NVM_VERSION="0.40.3"
ARG PNPM_VERSION="latest"
ENV NVM_DIR=/home/${USERNAME}/.nvm
ENV NVM_SYMLINK_CURRENT=true


ENV VIRTUAL_ENV=/app/.venv \
    UV_TOOL_BIN_DIR=/home/${USERNAME}/.local/bin \
    PATH="/app/.venv/bin:/home/${USERNAME}/.local/bin:$PATH" \
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
    UAG_ISOLATED_ENVS_ROOT=/workspaces/isolated_envs \
    UAG_LOGS_ROOT=/logs

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
    && sed -i 's/^# *\\(en_US.UTF-8 UTF-8\\)/\\1/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

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
        /workspaces/isolated_envs \
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

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/bin/npm /usr/local/bin/npm
COPY --from=node-runtime /usr/local/bin/npx /usr/local/bin/npx
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=python-builder --chown=${USER_UID}:${USER_GID} /app/.venv /app/.venv
COPY --from=python-builder --chown=${USER_UID}:${USER_GID} /app/src /app/src
COPY --from=python-builder --chown=${USER_UID}:${USER_GID} /app/pyproject.toml /app/README.md ./
COPY --from=ui-builder --chown=${USER_UID}:${USER_GID} /app/ui/dist /app/ui/dist




USER ${USERNAME}

RUN mkdir -p "$NVM_DIR" \
    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh | bash \
    && source "$NVM_DIR/nvm.sh" \
    && nvm install "$NODE_VERSION" \
    && nvm alias default "$NODE_VERSION" \
    && nvm use default \
    && corepack enable \
    && corepack prepare pnpm@${PNPM_VERSION} --activate \
    && npm install -g \
        "${CODEX_CLI_PACKAGE}" \
        "${GEMINI_CLI_PACKAGE}" \
        "${OPENCODE_CLI_PACKAGE}" \
        "${PNPM_PACKAGE}" 

RUN printf '%s\n' \
        'export HISTFILE=$HOME/.history/.bash_history' \
        'export HISTSIZE=10000' \
        'export HISTFILESIZE=20000' \
        'export HISTCONTROL=ignoredups:erasedups' \
        'shopt -s histappend' \
        'PROMPT_COMMAND="history -a; history -c; history -r"' \
        'export PATH=/app/.venv/bin:$HOME/.local/bin:$PATH' \
        '[[ $- == *i* ]] && bind "\e[A":history-search-backward' \
        '[[ $- == *i* ]] && bind "\e[B":history-search-forward' \
        > "$HOME/.bashrc" \
    && printf '%s\n' 'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi' > "$HOME/.bash_profile"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${UAG_PORT}/management/v1/health" >/dev/null || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["codara", "serve", "--host", "0.0.0.0", "--port", "8000"]
