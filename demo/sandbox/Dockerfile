FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install \
        "ruff>=0.2.2,<1.0.0" \
        "mypy>=1.8,<2.0" \
        "bandit>=1.7,<2.0" \
        "pip-audit>=2.7,<3.0"

WORKDIR /workspace
