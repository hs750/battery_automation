FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/London

RUN useradd --uid 1000 --create-home --shell /bin/bash app
WORKDIR /app

# Install dependencies first so source-only changes don't invalidate the dep layer.
# We materialise an empty package skeleton so `pip install .` can resolve the
# project metadata, then discard it before copying the real source.
COPY pyproject.toml ./
RUN mkdir -p src/battery_automation && \
    : > src/battery_automation/__init__.py && \
    pip install . && \
    rm -rf src

COPY src ./src
RUN pip install --no-deps .

USER app

CMD ["python", "-m", "battery_automation.main"]
