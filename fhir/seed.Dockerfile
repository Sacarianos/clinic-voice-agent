FROM python:3.12-slim

RUN pip install --no-cache-dir "tzdata>=2024.1"

WORKDIR /app
COPY src ./src
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

CMD ["python", "-m", "clinic_seed"]
