FROM ghcr.io/astral-sh/uv:0.11.26 AS uv

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

RUN uv sync --frozen --no-dev

RUN addgroup --system propel \
    && adduser --system --ingroup propel --home /app propel \
    && chown -R propel:propel /app

USER propel

EXPOSE 8000

CMD ["uvicorn", "propel.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

