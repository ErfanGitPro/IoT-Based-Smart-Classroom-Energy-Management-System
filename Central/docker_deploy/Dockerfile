FROM python:3.9-slim
WORKDIR /app
ENV PYTHONPATH="/app"
COPY docker_deploy/requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python"]
