import time, json, cv2, logging
from ultralytics import YOLO
from base_service import BaseMQTTService
from config import EdgeConfig

logger = logging.getLogger("CameraModule")

class CameraModeManager:
    def __init__(self, timeout: int, active_sec: int, sleep_sec: int):
        self.timeout = timeout
        self.active_sec = active_sec
        self.sleep_sec = sleep_sec
        self.duty_phase = "ACTIVE"
        self.phase_start_time = time.time()
        self.sensor_still_stale = False

    def evaluate_mode(self, last_sensor_heartbeat: float, is_scheduled: bool, has_people: bool, current_motion: int):
        now = time.time()
        sensor_is_stale = (now - last_sensor_heartbeat) > self.timeout
        duty_cycle = is_scheduled or has_people

        if sensor_is_stale:
            if not self.sensor_still_stale:
                logger.warning("Motion sensor stale! FORCING continuous camera scan as fallback.")
                self.sensor_still_stale = True
            self.duty_phase = "ACTIVE"
            return "CONTINUOUS", "ACTIVE", True, self.sensor_still_stale

        self.sensor_still_stale = False

        if not duty_cycle and current_motion == 0:
            self.duty_phase = "SLEEP"
            return "IDLE", "SLEEP", False, False

        if duty_cycle:
            operating_mode = "DUTY_CYCLE"
            elapsed = now - self.phase_start_time
            if self.duty_phase == "ACTIVE":
                if elapsed < self.active_sec:
                    camera_active = True
                else:
                    self.duty_phase = "SLEEP"
                    camera_active = False
                    self.phase_start_time = now
            else:
                if elapsed < self.sleep_sec:
                    camera_active = False
                else:
                    self.duty_phase = "ACTIVE"
                    camera_active = True
                    self.phase_start_time = now
            return operating_mode, self.duty_phase, camera_active, False

        # WAKE_SCAN
        self.duty_phase = "ACTIVE"
        self.phase_start_time = now
        return "WAKE_SCAN", "ACTIVE", True, False


class CameraModule(BaseMQTTService):
    def __init__(self, config: EdgeConfig = None):
        super().__init__("Camera", config)
        
        self.has_camera = self.config.has_camera
        self.mode_manager = CameraModeManager(
            self.config.motion_sensor_timeout,
            self.config.camera_active_seconds,
            self.config.camera_sleep_seconds
        )

        self.last_sensor_heartbeat = time.time()
        self.model = None
        self.cap = None
        self.is_scheduled = False
        self.last_occupancy_count = 0
        self.current_motion = 0
        self.camera_healthy = True
        self.next_camera_retry = 0

    def load_yolo_model(self):
        if not self.has_camera or self.config.cam_mode == "fake":
            return
        try:
            self.model = YOLO("yolov8n.pt")
        except Exception as e:
            logger.error(f"YOLO load error: {e}")
            self.model = None

    def get_occupancy_count(self):
        if self.config.cam_mode == "fake":
            return self.config.occupants

        if self.model is None:
            return None

        if time.time() < self.next_camera_retry:
            return None

        try:
            if self.cap is None or not self.cap.isOpened():
                self.cap = cv2.VideoCapture(0)
                if not self.cap.isOpened():
                    logger.warning("Camera failed to open, entering retry cooldown.")
                    self.camera_healthy = False
                    self.next_camera_retry = time.time() + 60
                    return None
        except Exception as e:
            logger.error(f"Camera initialization error: {e}")
            self.camera_healthy = False
            self.next_camera_retry = time.time() + 60
            self.cap = None
            return None

        ret, frame = self.cap.read()
        if not ret:
            logger.warning("Failed to read frame from camera, releasing device.")
            self.cap.release()
            self.cap = None
            self.camera_healthy = False
            self.next_camera_retry = time.time() + 60
            return None

        try:
            self.camera_healthy = True
            results = self.model(frame, imgsz=640, verbose=False)
            return sum(1 for box in results[0].boxes if int(box.cls[0]) == 0)
        except Exception as e:
            logger.error(f"Inference error: {type(e).__name__}: {e}")
            return None

    def on_connect(self, client, userdata, flags, rc):
        self.client.subscribe([
            (f"{self.room_id}/sensors", 1),
            (f"{self.room_id}/schedule", 1)
        ])
        logger.info("Subscribed to camera topics")

    def on_message(self, client, userdata, msg):
        if not self.has_camera:
            return

        topic = msg.topic.strip()
        try:
            payload_str = msg.payload.decode().strip()
        except Exception as e:
            logger.warning(f"Error decoding camera message payload: {e}")
            return

        if topic == f"{self.room_id}/schedule":
            self.is_scheduled = payload_str.upper() == "ON"

        elif topic == f"{self.room_id}/sensors":
            try:
                data = json.loads(payload_str)
                self.current_motion = int(data.get("motion", 0))
                self.last_sensor_heartbeat = time.time()
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.warning(f"Failed to parse sensor data: {type(e).__name__}: {e}")

    def shutdown(self):
        logger.info("Shutting down camera module...")
        if self.cap and self.cap.isOpened():
            self.cap.release()
        super().shutdown()

    def run(self):
        if self.has_camera:
            self.load_yolo_model()

        # Register Camera service with the Catalog
        self.start_catalog_thread({
            "id": f"camera-{self.room_id}",
            "name": f"Camera Occupancy Module ({self.room_id})",
            "type": "device",
            "hardware": ["camera", "yolo_model"],
            "topics": [f"{self.room_id}/camera/occupancy"]
        })

        self.connect_mqtt()
        logger.info("Camera module running...")

        last_print_time = 0

        while self.running:
            if not self.has_camera:
                time.sleep(1)
                continue

            has_people = self.last_occupancy_count > 0
            op_mode, duty_phase, camera_active, _ = self.mode_manager.evaluate_mode(
                self.last_sensor_heartbeat,
                self.is_scheduled,
                has_people,
                self.current_motion
            )

            if not camera_active and self.cap and self.cap.isOpened():
                self.cap.release()
                self.cap = None

            current_time = time.time()
            if current_time - last_print_time >= 5:
                # logger.info(f"Status: is_scheduled={self.is_scheduled}, has_people={has_people}, camera_active={camera_active}")
                if camera_active:
                    c = self.get_occupancy_count()
                    if c is not None:
                        self.last_occupancy_count = c
                        logger.info(f"📷 Camera Data - Occupancy Detected: {c} people")
                        self.client.publish(f"{self.room_id}/camera/occupancy", json.dumps({"occupancy_count": c}), qos=1)
                    else:
                        logger.info("📷 Camera Data - Unmeasured (Hardware error / Cooldown)")
                else:
                    elapsed_sec = int(current_time - self.mode_manager.phase_start_time)
                    logger.info(f"📷 Camera Data - Occupancy: {self.last_occupancy_count} ({op_mode} | phase: {duty_phase} | elapsed: {elapsed_sec}s)")
                last_print_time = current_time

            time.sleep(1)

if __name__ == "__main__":
    module = CameraModule()
    module.setup_signal_handlers()
    module.run()
