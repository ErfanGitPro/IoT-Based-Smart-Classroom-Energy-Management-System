import time, json, random, logging
from base_service import BaseDeviceConnector
from config import EdgeConfig

logger = logging.getLogger("FakeDeviceConnector")

class FakeDeviceConnector(BaseDeviceConnector):
    def __init__(self, config: EdgeConfig = None):
        super().__init__("FakeConnector", config)
        
        self.temp_mode = self.config.temp_mode
        self.motion_start_hour = self.config.motion_start_hour
        self.motion_end_hour = self.config.motion_end_hour
        
        self.ac_is_on = False
        self.vent_is_open = False
        self.current_occupancy = 0
        self.last_fake_update = 0
        self.current_fake_temp = 22.0
        self.current_fake_motion = 0

    def read_sensors(self):
        return self.data_generator()

    def data_generator(self):
        now = time.time()
        if now - self.last_fake_update < 1.0:
            return round(self.current_fake_temp, 1), self.current_fake_motion
            
        self.last_fake_update = now
        
        current_hour = time.localtime().tm_hour
        if self.motion_start_hour < self.motion_end_hour:
            is_active = self.motion_start_hour <= current_hour < self.motion_end_hour
        else:
            is_active = current_hour >= self.motion_start_hour or current_hour < self.motion_end_hour
            
        if is_active:
            self.current_fake_motion = 1 if random.random() < 0.7 else 1
        else:
            self.current_fake_motion = 1 if random.random() < 0.05 else 1
            
        if self.temp_mode == "low": target_temp = 3.0
        elif self.temp_mode == "high": target_temp = 31.0
        else: target_temp = 22.0
        
        if self.ac_is_on:
            target_temp -= 5.0
        if self.vent_is_open:
            target_temp = (target_temp + 20.0) / 2.0
            
        target_temp += (self.current_occupancy * 0.05)
        
        self.current_fake_temp += (target_temp - self.current_fake_temp) * 0.1
        self.current_fake_temp += random.uniform(-0.1, 0.1)
        
        if self.temp_mode == "low": self.current_fake_temp = max(1.0, min(5.0, self.current_fake_temp))
        elif self.temp_mode == "high": self.current_fake_temp = max(22.0, min(35.0, self.current_fake_temp))
        else: self.current_fake_temp = max(12.0, min(25.0, self.current_fake_temp))
        
        return round(self.current_fake_temp, 1), self.current_fake_motion

    def on_connect(self, client, userdata, flags, rc):
        logger.info(f"Subscribed for room {self.room_id}")
        self.client.subscribe([
            (f"{self.room_id}/state/history", 1),
            (f"{self.room_id}/state/ac", 1),
            (f"{self.room_id}/state/ventilation", 1)
        ])

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload_str = msg.payload.decode().strip()
        except Exception as e:
            logger.warning(f"Failed to decode message payload: {e}")
            return

        if topic == f"{self.room_id}/state/history":
            try:
                data = json.loads(payload_str)
                self.current_occupancy = data.get("occupancy_count", 0)
            except Exception as e:
                logger.debug(f"Failed to parse history occupancy payload: {e}")
        elif topic == f"{self.room_id}/state/ac":
            try:
                data = json.loads(payload_str)
                self.ac_is_on = (data.get("status", "OFF").upper() != "OFF")
            except Exception:
                self.ac_is_on = (payload_str.upper() != "OFF")
        elif topic == f"{self.room_id}/state/ventilation":
            self.vent_is_open = (payload_str.upper() == "OPEN")

    def start(self):
        self.connect_mqtt()
        self.start_catalog_thread({
            "id": f"edge-sensors-fake-{self.room_id}",
            "name": f"Fake Sensors Connector ({self.room_id})",
            "type": "device",
            "hardware": ["mock_ds18b20", "mock_pir_motion"],
            "mode": "fake",
            "topics": [f"{self.room_id}/sensors", f"{self.room_id}/status"]
        })
        logger.info(f"Starting FAKE sensor polling for {self.room_id}")
        
        try:
            while self.running:
                temp_val, motion_val = self.read_sensors()
                logger.info(f"Fake Data - Temp: {temp_val}°C | Motion: {'Detected' if motion_val else 'None'}")
                
                payload = json.dumps({
                    "motion": motion_val,
                    "temperature": temp_val,
                    "sensor_mode": "fake"
                })
                try:
                    self.client.publish(f"{self.room_id}/sensors", payload, qos=1)
                except Exception as e:
                    logger.warning(f"Failed to publish fake sensor payload: {e}")
                time.sleep(5)
        except KeyboardInterrupt:
            self.shutdown()

if __name__ == "__main__":
    connector = FakeDeviceConnector()
    connector.setup_signal_handlers()
    connector.start()
