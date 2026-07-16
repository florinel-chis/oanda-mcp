FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim

RUN useradd -r app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER app

# stdio by default; append --transport http --host 0.0.0.0 --port 8000 for HTTP
ENTRYPOINT ["oanda-mcp"]
