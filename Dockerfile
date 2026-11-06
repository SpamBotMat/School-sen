# School Sensor — образ для Render / любого Docker-хостинга
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MALLOC_ARENA_MAX=2 \
    TZ=Asia/Ashgabat

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY netschool ./netschool
COPY static ./static
COPY assets ./assets

# не root
RUN useradd --create-home --uid 10001 bot && mkdir -p /srv/data && chown -R bot /srv/data
USER bot

ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=40s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4)"
CMD ["python", "-m", "app"]
