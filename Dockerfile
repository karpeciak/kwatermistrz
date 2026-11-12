# Jeden obraz dla API i wszystkich agentów – różni je tylko polecenie startowe
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

RUN useradd --create-home kwatermistrz
USER kwatermistrz

EXPOSE 8000
CMD ["uvicorn", "kwatermistrz.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
