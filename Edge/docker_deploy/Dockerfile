# ==========================================
# STAGE 1: Light (for Sensors & Controller)
# ==========================================
FROM python:3.9-slim AS light
WORKDIR /app

# Copy requirements
COPY docker_deploy/requirements.txt .

# Install dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt gpiozero lgpio

# Copy application code
COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["python", "-u", "app.py"]


# ==========================================
# STAGE 2: RPi (for Camera / YOLO Service)
# ==========================================
FROM python:3.9-slim AS rpi
WORKDIR /app

# install libraries needed for OpenCV
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 libatomic1 && \
    rm -rf /var/lib/apt/lists/*

COPY docker_deploy/requirements.txt .

# install dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt "numpy<2.0" opencv-python-headless==4.8.1.78 && \
    pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch==2.0.1 torchvision==0.15.2 ultralytics==8.0.196 "numpy<2.0"

# YOLOv8 model
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# Copy application code
COPY *.py ./

ENV PYTHONUNBUFFERED=1
CMD ["python", "-u", "app.py"]