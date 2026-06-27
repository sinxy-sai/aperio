FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install \
        ruff \
        mypy \
        bandit \
        pip-audit

WORKDIR /workspace

