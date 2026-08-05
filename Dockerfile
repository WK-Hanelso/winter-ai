FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/workspace/src:/workspace

WORKDIR /workspace

COPY pyproject.toml ./
COPY src ./src
COPY configs ./configs
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 libvulkan1 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir ".[dev]"

CMD ["bash"]
