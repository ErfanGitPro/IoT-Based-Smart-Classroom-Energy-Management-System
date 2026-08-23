import os
from dataclasses import dataclass

@dataclass
class EdgeConfig:
    broker: str = os.getenv("MQTT_BROKER", "localhost").strip()
    room_id: str = os.getenv("ROOM_ID", "classroom001").strip()
    catalog_url: str = os.getenv("CATALOG_URL", "http://localhost:9090").rstrip("/")
    sensor_mode: str = os.getenv("SENSOR_MODE", os.getenv("USE_FAKE_DATA", os.getenv("USE_FAKE_SENSORS", "fake"))).strip().lower()
    
    # Camera Settings
    has_camera: bool = os.getenv("HAS_CAMERA", "false").strip().lower() == "true"
    motion_sensor_timeout: int = int(os.getenv("MOTION_SENSOR_TIMEOUT", "300").strip())
    camera_active_seconds: int = int(os.getenv("CAMERA_ACTIVE_SECONDS", "30").strip())
    camera_sleep_seconds: int = int(os.getenv("CAMERA_SLEEP_SECONDS", "60").strip())
    cam_mode: str = os.getenv("CAM_MODE", "real").strip().lower()
    occupants: int = int(os.getenv("OCCUPANTS", "30").strip())
    
    # Fake / Real Hardware Settings
    temp_mode: str = os.getenv("TEMP_MODE", "normal").strip()
    motion_start_hour: int = int(os.getenv("MOTION_START_HOUR", "8").strip())
    motion_end_hour: int = int(os.getenv("MOTION_END_HOUR", "18").strip())
    w1_device_id: str = os.getenv("W1_DEVICE_ID", "").strip()
    pir_gpio_pin: int = int(os.getenv("PIR_GPIO_PIN", "17").strip())
    
    # Control Module Settings
    default_ac_precool_temp: int = int(os.getenv("DEFAULT_AC_PRECOOL_TEMP", "21").strip())
    threshold_base: float = float(os.getenv("THRESHOLD_BASE", "24.0").strip())
    holdup_band: float = float(os.getenv("HOLDUP_BAND", "1.5").strip())
    manual_mode_hold_seconds: int = int(os.getenv("MANUAL_MODE_HOLD_SECONDS", "120").strip())
    eval_fast: int = int(os.getenv("EVAL_INTERVAL_FAST", "1").strip())
    eval_medium: int = int(os.getenv("EVAL_INTERVAL_MEDIUM", "3").strip())
    eval_slow: int = int(os.getenv("EVAL_INTERVAL_SLOW", "10").strip())
    occupancy_high_threshold: int = int(os.getenv("OCCUPANCY_HIGH_THRESHOLD", "20").strip())
