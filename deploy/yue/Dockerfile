# ESP Live Sound Studio optional YuE renderer.
# This image is intentionally separate from the customer web service because YuE has a heavy,
# CUDA-specific Python stack. It exposes no host port in the Compose overlay.
FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG YUE_COMMIT=9f1394bae1d8d218fea750c1413c2d9d731c7310

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-dev git git-lfs ffmpeg libsndfile1 libsndfile1-dev \
      build-essential ninja-build curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python3 -m pip install --no-cache-dir \
      torch torchaudio --index-url https://download.pytorch.org/whl/cu121

WORKDIR /opt
RUN git clone https://github.com/multimodal-art-projection/YuE.git yue \
    && cd /opt/yue \
    && git checkout "${YUE_COMMIT}" \
    && python3 -m pip install --no-cache-dir -r requirements.txt \
    && rm -f /opt/yue/inference/xcodec_mini_infer \
    && git clone https://huggingface.co/m-a-p/xcodec_mini_infer /opt/yue/inference/xcodec_mini_infer

# YuE's official inference currently requests flash_attention_2 explicitly.
RUN python3 -m pip install --no-cache-dir flash-attn --no-build-isolation \
    && python3 -m pip install --no-cache-dir fastapi uvicorn[standard] pydantic

COPY deploy/yue/server.py /opt/esp-yue/server.py

ENV YUE_SOURCE_ROOT=/opt/yue \
    YUE_WORK_ROOT=/var/lib/yue \
    HF_HOME=/root/.cache/huggingface \
    TRANSFORMERS_CACHE=/root/.cache/huggingface/hub \
    TOKENIZERS_PARALLELISM=false

RUN mkdir -p /var/lib/yue /root/.cache/huggingface

EXPOSE 8011
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8011/health >/dev/null || exit 1

CMD ["python3", "-m", "uvicorn", "server:app", "--app-dir", "/opt/esp-yue", "--host", "0.0.0.0", "--port", "8011", "--workers", "1"]
