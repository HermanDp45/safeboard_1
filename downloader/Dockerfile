FROM python:3.12-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

RUN groupadd --gid 10001 vllm \
    && useradd --uid 10001 --gid 10001 --no-create-home vllm \
    && mkdir /models \
    && chown 10001:10001 /models

WORKDIR /app
COPY downloader/requirements.txt .
RUN pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r requirements.txt \
    && pip check
COPY downloader/download.py .

ENV HOME=/tmp \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    DO_NOT_TRACK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001
ENTRYPOINT ["python", "download.py"]
