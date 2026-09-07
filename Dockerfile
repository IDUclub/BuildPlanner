FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

COPY pyproject.toml ./
RUN poetry install --only main --no-root --no-interaction

COPY app ./app

EXPOSE 8080

# Пайплайн ждёт GenPlanner и GenBuilder минутами, поэтому таймаут воркера длинный,
# а воркеров немного: вся тяжёлая работа происходит в нижних сервисах.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--timeout", "1900", \
     "--graceful-timeout", "60", \
     "--keep-alive", "75", \
     "--bind", "0.0.0.0:8080"]
