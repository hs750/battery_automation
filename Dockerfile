FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/London

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install .

USER 1000:1000

CMD ["python", "-m", "battery_automation.main"]
