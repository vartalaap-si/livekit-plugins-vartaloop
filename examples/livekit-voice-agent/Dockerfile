# Vartaloop voice agent: one image for both processes of the example.
#
#   docker build -t vartaloop-voice-agent .
#   docker run --env-file .env vartaloop-voice-agent                       # agent worker
#   docker run --env-file .env -p 8080:8080 vartaloop-voice-agent token    # token server
#
# Model weights (Silero VAD, LiveKit turn detector) are fetched at build time so a container
# starts with everything in memory and never downloads or reads from disk during a call.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# git is needed only while pip resolves the plugin from this repository (requirements.txt).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY agent.py token_server.py entrypoint.sh ./
COPY web ./web
# The execute bit does not survive every checkout (zip downloads, some Windows clones).
RUN chmod 0755 entrypoint.sh

# Pre-download the VAD and turn-detector weights into the image.
RUN python agent.py download-files

# Run as an unprivileged user; the model cache is read-only from here on.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080
ENTRYPOINT ["./entrypoint.sh"]
CMD ["agent"]
