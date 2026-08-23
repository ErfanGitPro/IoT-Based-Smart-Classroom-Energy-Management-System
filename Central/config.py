import os
from dataclasses import dataclass

@dataclass
class CentralConfig:
    broker: str = os.getenv("MQTT_BROKER", "localhost").strip()
    port: int = int(os.getenv("MQTT_PORT", "1883").strip())
    catalog_url: str = os.getenv("CATALOG_URL", "http://localhost:9090").rstrip("/")
    catalog_port: int = int(os.getenv("CATALOG_PORT", "9090").strip())
    
    # SQLite databases
    db_schedule: str = os.getenv("DB_SCHEDULE", "/app/data/schedule.db").strip()
    db_control_logs: str = os.getenv("DB_CONTROL_LOGS", "/app/data/control_logs.db").strip()
    db_sensors: str = os.getenv("DB_SENSORS", "/app/data/sensors.db").strip()
    db_classroom: str = os.getenv("DB_CLASSROOM", "/app/data/classroom.db").strip()
    
    # DB Retention and Purging settings
    purge_interval_seconds: int = int(os.getenv("PURGE_INTERVAL_SECONDS", "86400").strip())
    retention_days: int = int(os.getenv("RETENTION_DAYS", "30").strip())
    
    # Telegram Bot Settings
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "").strip() or None
    telegram_bot_url: str = os.getenv("TELEGRAM_BOT_URL", "http://localhost:5004/api/alert").rstrip("/")
    
    # ThingSpeak Adaptor Settings
    thingspeak_write_api_key: str = os.getenv("THINGSPEAK_WRITE_API_KEY", "").strip()
    thingspeak_url: str = os.getenv("THINGSPEAK_URL", "https://api.thingspeak.com/update").rstrip("/")
    thingspeak_update_interval: int = int(os.getenv("THINGSPEAK_UPDATE_INTERVAL", "15").strip())
    
    # Statistics Service Settings
    efficiency_sensitivity: float = float(os.getenv("EFFICIENCY_SENSITIVITY", "10.0").strip())
    update_metrics_interval_seconds: int = int(os.getenv("UPDATE_METRICS_INTERVAL_SECONDS", "300").strip())
    
    # AC Precool Settings
    default_ac_precool_temp: int = int(os.getenv("DEFAULT_AC_PRECOOL_TEMP", "21").strip())
    
    # Weather Service Settings
    openweather_api_key: str = os.getenv("OPENWEATHER_API_KEY", "").strip() or None
    weather_city: str = os.getenv("WEATHER_CITY", "Torino").strip()
    
    # UI Dashboard Settings
    ui_base_url: str = os.getenv("UI_BASE_URL", "http://localhost:5000").rstrip("/")
    stats_service_url: str = os.getenv("STATS_SERVICE", "http://localhost:5003").rstrip("/")
    db_service_url: str = os.getenv("DB_SERVICE", "http://localhost:5002").rstrip("/")
    weather_service_url: str = os.getenv("WEATHER_SERVICE", "http://localhost:5005").rstrip("/")
    fresh_data_max_age_seconds: int = int(os.getenv("FRESH_DATA_MAX_AGE_SECONDS", "30").strip())
    manual_hold_threshold: int = int(os.getenv("MANUAL_HOLD_THRESHOLD", "60").strip())
    flask_secret_key: str = os.getenv("FLASK_SECRET_KEY", "iot-dashboard-secret").strip()
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None
