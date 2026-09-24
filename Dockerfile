FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["sh", "-c", "if [ -d /data ] && [ -w /data ]; then export DATABASE_URL=sqlite+aiosqlite:////data/preview.db; else export DATABASE_URL=sqlite+aiosqlite:///./preview.db; fi; alembic upgrade head && exec python -m uvicorn trading_copilot.main:app --host 0.0.0.0 --port ${PORT}"]
