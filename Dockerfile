# Faceless Video Maker - production image.
#
#   docker build -t faceless-video-maker .
#   docker run -p 8000:8000 faceless-video-maker
#
# ffmpeg and a font are the only system dependencies; everything else is pip.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY index.html ./
COPY scripts ./scripts

# Rendered videos and job state live here; mount a volume to keep them.
RUN mkdir -p /app/data /app/output
VOLUME ["/app/data", "/app/output"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
