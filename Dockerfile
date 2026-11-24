FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# CPU-only PyTorch keeps the image small (no CUDA wheels).
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[dev]"

# Bake the embedding model into the image so containers start offline.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY . .

EXPOSE 8000 8001
CMD ["sh", "scripts/start-api.sh"]
