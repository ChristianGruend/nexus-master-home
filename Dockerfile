# NEXUS-Kern (Stufe 2.2). Desktop/Audio/adb laufen später über den nativen Host-Agent, nicht im Container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NEXUS_HOST=127.0.0.1 \
    NEXUS_PORT=8299 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 nexus

WORKDIR /app

COPY requirements.txt requirements-extra.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-extra.txt

COPY --chown=nexus:nexus . .

RUN mkdir -p /app/workspace && chown nexus:nexus /app/workspace

USER nexus

VOLUME ["/app/workspace"]

EXPOSE 8299

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('NEXUS_PORT','8299')+'/api/health',timeout=4)"

CMD ["python", "run_server.py"]
