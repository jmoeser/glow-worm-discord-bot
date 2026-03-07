FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY bot/ bot/
RUN uv sync --frozen --no-dev

RUN useradd --no-create-home --shell /bin/false app \
    && chown -R app:app /app
USER app

CMD ["uv", "run", "python", "-m", "bot.main"]
