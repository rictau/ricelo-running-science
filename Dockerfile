# Remote (HTTP) ricelo MCP server, e.g. for Cloud Run. See docs/remote.md.
# Local use doesn't need this: AI apps start ricelo themselves over stdio.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home ricelo
USER ricelo

# Cloud Run sets PORT; RICELO_PUBLIC_URL, RICELO_AUTH_SECRET,
# RICELO_OWNER_PASSWORD and GARMIN_PERSONAL_API_KEY come from the deploy.
ENV RICELO_TRANSPORT=http RICELO_HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["/app/.venv/bin/ricelo-running-science-mcp"]
