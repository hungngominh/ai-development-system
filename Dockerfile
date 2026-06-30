FROM python:3.12-slim

# Node.js + npm để cài `claude` CLI (provider claude_code spawn binary này).
# git + gh (GitHub CLI) cho repo-bound bots (branch/push/PR).
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm git ca-certificates curl gnupg \
    && npm install -g @anthropic-ai/claude-code \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Fail fast if any required CLI is missing.
RUN claude --version && git --version && gh --version

WORKDIR /app

# Copy metadata + source + thư mục package-data (pyproject trỏ tới ../../skills,
# ../../docs/schema; runtime cũng đọc docs/schema để apply_schema).
COPY pyproject.toml ./
COPY src/ ./src/
COPY skills/ ./skills/
COPY docs/schema/ ./docs/schema/

RUN pip install --no-cache-dir -e .

RUN mkdir -p /data/storage

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

CMD ["ai-dev", "gateway"]
