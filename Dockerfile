FROM python:3.12-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libnuma1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 vllm \
    && useradd --uid 10001 --gid 10001 --no-create-home vllm \
    && mkdir /models \
    && chown 10001:10001 /models

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r requirements.txt \
    && pip check
COPY service.py .
COPY k8s/serve.yaml k8s/logging.json /etc/vllm/

ENV VLLM_TARGET_DEVICE=cpu \
    VLLM_WORKER_MULTIPROC_METHOD=spawn \
    VLLM_NO_USAGE_STATS=1 \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    VLLM_LOGGING_CONFIG_PATH=/etc/vllm/logging.json

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["vllm", "serve"]
