# Parking platform API + back office
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production
WORKDIR /srv

RUN useradd --system --uid 1000 parking
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir . && mkdir -p /srv/evidence && chown -R parking /srv

USER parking
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# Apply migrations, then serve. One worker: the WebSocket broadcaster is in-process
# (use Postgres LISTEN/NOTIFY or Redis before scaling to several workers).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
