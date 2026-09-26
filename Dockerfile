FROM node:22-bookworm-slim AS node_runtime

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Node.js 22 is used by yt-dlp's modern YouTube JavaScript/PO-token tooling.
COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
ENV PATH="/usr/local/bin:/usr/local/lib/node_modules/.bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libzbar0 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
