FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY service/ service/
COPY tests/ tests/
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
CMD ["python", "-m", "service", "consume"]
