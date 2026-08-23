import time
import json
import logging
import threading
from base_service import BaseCentralService
from config import CentralConfig

logger = logging.getLogger("ThingSpeakAdaptor")

class ThingSpeakAdaptor(BaseCentralService):
    def __init__(self, config: CentralConfig = None):
        super().__init__("ThingSpeakAdaptor", config, use_mqtt=True)
        self.latest_data = {}
        self.latest_outside_temp = None
        
        # Load initial outside temperature from database history to avoid startup lag
        try:
            with self.get_db_connection(self.config.db_sensors) as conn:
                res = conn.execute("SELECT outside_temp FROM weather_history ORDER BY timestamp DESC LIMIT 1").fetchone()
                if res and res['outside_temp'] is not None:
                    self.latest_outside_temp = res['outside_temp']
                    logger.info(f"Loaded initial outside temperature from DB: {self.latest_outside_temp}°C")
        except Exception as e:
            logger.warning(f"Could not load initial outside temperature from DB: {e}")

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("✅ Subscribing to central telemetry topics for ThingSpeak...")
            self.client.subscribe([
                ("+/sensors", 1),
                ("+/state/ac", 1),
                ("+/camera/occupancy", 1),
                ("system/weather", 1),
                ("+/state/ventilation", 1),
                ("+/state/lamp/front", 1),
                ("+/state/lamp/back", 1)
            ])
            logger.info("✅ Subscriptions registered successfully.")

    def on_message(self, client, userdata, msg):
        topic = msg.topic.strip()
        try:
            payload_str = msg.payload.decode().strip()
            data = json.loads(payload_str) if payload_str.startswith("{") else payload_str
        except Exception as e:
            logger.warning(f"Failed to decode message: {e}")
            return

        parts = topic.split('/')
        if len(parts) >= 2:
            room_id = parts[0].strip()
            if room_id not in self.latest_data:
                self.latest_data[room_id] = {
                    "temperature": None,
                    "occupancy": 0,
                    "ac_state": 0,
                    "outside_temp": self.latest_outside_temp,
                    "vent_state": 0,
                    "lamp_front": 0,
                    "lamp_back": 0
                }

            if topic.endswith("/sensors"):
                if isinstance(data, dict) and "temperature" in data:
                    self.latest_data[room_id]["temperature"] = data["temperature"]
            elif topic.endswith("/state/ac"):
                status = data.get("status", "OFF") if isinstance(data, dict) else str(data)
                self.latest_data[room_id]["ac_state"] = 1 if "ON" in status.upper() or "PRECOOL" in status.upper() else 0
            elif topic.endswith("/camera/occupancy"):
                if isinstance(data, dict) and "occupancy_count" in data:
                    self.latest_data[room_id]["occupancy"] = data["occupancy_count"]
            elif topic.endswith("/state/ventilation"):
                status = data.get("status", data) if isinstance(data, dict) else str(data)
                self.latest_data[room_id]["vent_state"] = 1 if "OPEN" in status.upper() else 0
            elif topic.endswith("/state/lamp/front"):
                status = data.get("status", data) if isinstance(data, dict) else str(data)
                self.latest_data[room_id]["lamp_front"] = 1 if "ON" in status.upper() else 0
            elif topic.endswith("/state/lamp/back"):
                status = data.get("status", data) if isinstance(data, dict) else str(data)
                self.latest_data[room_id]["lamp_back"] = 1 if "ON" in status.upper() else 0

        elif topic == "system/weather":
            try:
                out_t = data.get("outside_temp") if isinstance(data, dict) else None
                if out_t is not None:
                    self.latest_outside_temp = out_t
                    for room_id in self.latest_data:
                        self.latest_data[room_id]["outside_temp"] = out_t
            except Exception as e:
                logger.error(f"Error parsing system/weather: {e}")

    def get_avg_efficiency_score(self, room_id):
        try:
            with self.get_db_connection(self.config.db_classroom) as conn:
                res = conn.execute("SELECT avg_efficiency_score FROM classroom_metadata WHERE room_id = ?", (room_id,)).fetchone()
                if res and res['avg_efficiency_score'] is not None:
                    return res['avg_efficiency_score']
        except Exception as e:
            logger.error(f"Failed to fetch efficiency score for {room_id}: {e}")
        return 100.0

    def upload_loop(self):
        while self.running:
            time.sleep(self.config.thingspeak_update_interval)

            for room_id, metrics in list(self.latest_data.items()):
                if metrics["temperature"] is None:
                    continue

                # Fetch classroom ThingSpeak API Key from DB
                api_key = None
                try:
                    with self.get_db_connection(self.config.db_classroom) as conn:
                        res = conn.execute("SELECT thingspeak_api_key FROM classroom_metadata WHERE room_id = ?", (room_id,)).fetchone()
                        if res and res['thingspeak_api_key']:
                            api_key = res['thingspeak_api_key'].strip()
                except Exception as e:
                    logger.error(f"Failed to fetch ThingSpeak API key for {room_id} from DB: {e}")

                if not api_key:
                    continue

                efficiency = self.get_avg_efficiency_score(room_id)
                payload = {
                    "api_key": api_key,
                    "field1": metrics["temperature"],
                    "field2": metrics["occupancy"],
                    "field3": metrics["ac_state"],
                    "field4": metrics["outside_temp"] if metrics["outside_temp"] is not None else 25.0,
                    "field5": metrics["vent_state"],
                    "field6": metrics["lamp_front"],
                    "field7": metrics["lamp_back"],
                    "field8": efficiency
                }

                try:
                    res = self.session.post(self.config.thingspeak_url, data=payload, timeout=5)
                    if res.ok:
                        logger.info(f"🚀 Posted 8 fields for {room_id} to ThingSpeak (Entry ID: {res.text})")
                    else:
                        logger.warning(f"ThingSpeak API returned status code: {res.status_code}")
                except Exception as e:
                    logger.error(f"Failed to upload data to ThingSpeak: {e}")

                time.sleep(15)  # Enforce ThingSpeak rate limits between requests

    def start(self):
        self.connect_mqtt()
        
        # Start registration heartbeat thread
        self.start_catalog_thread({
            "id": "thingspeak-adaptor",
            "name": "ThingSpeak Cloud Telemetry Adaptor",
            "type": "service",
            "hardware": ["cloud_adaptor"],
            "topics": [
                "+/sensors", "+/state/ac", "+/camera/occupancy",
                "system/weather", "+/state/ventilation", "+/state/lamp/+"
            ]
        })

        # Start periodic uploading thread
        threading.Thread(target=self.upload_loop, daemon=True).start()

if __name__ == "__main__":
    adaptor = ThingSpeakAdaptor()
    adaptor.setup_signal_handlers()
    adaptor.start()
    
    while True:
        time.sleep(10)
