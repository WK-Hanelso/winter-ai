FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/workspace/src

WORKDIR /workspace

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[dev]"

CMD ["bash"]
