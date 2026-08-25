FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

RUN groupadd --system --gid 10001 satapp \
    && useradd --system --uid 10001 --gid satapp --home-dir /app satapp

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=satapp:satapp \
    app.py \
    auth.py \
    db.py \
    i18n.py \
    integrations.py \
    location_search.py \
    scheduler.py \
    settings.py \
    timezones.py \
    ./
COPY --chown=satapp:satapp static ./static
COPY --chown=satapp:satapp templates ./templates

USER satapp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz', timeout=3)"

CMD ["sh", "-c", "exec python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
