FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libzbar0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -c "import yt_dlp, curl_cffi; print('yt-dlp:', yt_dlp.version.__version__); print('curl-cffi:', curl_cffi.__version__)"

COPY . .

CMD ["python", "bot.py"]
