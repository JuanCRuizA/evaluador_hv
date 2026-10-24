FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

COPY . .

EXPOSE 8000
CMD ["uv", "run", "flask", "--app", "wsgi", "run", "--host", "0.0.0.0", "--port", "8000", "--debug"]
