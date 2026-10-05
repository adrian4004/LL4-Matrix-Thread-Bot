FROM python:3.12.15-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

FROM python:3.12.15-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin bot
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY bot.py ./
USER 10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import os, sys, time; sys.exit(time.time() - os.path.getmtime('/tmp/heartbeat') > 120)"]
CMD ["python", "bot.py"]
