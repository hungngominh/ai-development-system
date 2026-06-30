FROM python:3.12-slim

# Node.js + npm để cài `claude` CLI (provider claude_code spawn binary này).
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# Fail fast nếu binary claude không lên PATH.
RUN claude --version

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
