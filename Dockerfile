FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY static ./static

# Läuft nicht als root, mit eigener ID (1000 ist auf dem VPS schon vergeben);
# Datenbank und eigene Fotos liegen im Volume /data
RUN useradd --system --uid 10001 --home-dir /app rezepte && mkdir -p /data && chown rezepte /data
USER rezepte

ENV HOST=0.0.0.0 PORT=8300 DB_PATH=/data/rezepte.db PHOTO_DIR=/data/fotos
EXPOSE 8300
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8300/api/me', timeout=4)"
CMD ["python", "app.py"]
