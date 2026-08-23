import time, json, glob, os, logging
from base_service import BaseDeviceConnector
from config import EdgeConfig

logger = logging.getLogger("RealDeviceConnector")

class RealDeviceConnector(BaseDeviceConnector):
    def __init__(self, config: EdgeConfig = None):
        super().__init__("RealConnector", config)
        
        self.pir_gpio_pin = self.config.pir_gpio_pin
        self.w1_device_id = self.config.w1_device_id
        
        self.pir = None
        self.w1_device_path = None
        
        self.setup_motion_sensor()
        self.setup_temperature_sensor()

    def read_sensors(self):
        return self.read_temperature(), self.read_motion()

    def setup_motion_sensor(self):
        try:
            from gpiozero import MotionSensor
            self.pir = MotionSensor(self.pir_gpio_pin)
            logger.info(f"Real PIR Motion Sensor initialized on GPIO {self.pir_gpio_pin}")
        except Exception as e:
            logger.warning(f"Real PIR setup warning: {e}")

    def setup_temperature_sensor(self):
        try:
            if not self.w1_device_id:
                devices = glob.glob("/sys/bus/w1/devices/28*")
                if devices:
                    self.w1_device_id = os.path.basename(devices[0])

            if self.w1_device_id:
                self.w1_device_path = f"/sys/bus/w1/devices/{self.w1_device_id}/w1_slave"
                if os.path.exists(self.w1_device_path):
                    logger.info(f"Real Temp Sensor detected: {self.w1_device_id}")
                    return
            logger.warning("Real Temp Sensor file not found.")
        except Exception as e:
            logger.warning(f"Real Temp Sensor setup warning: {e}")

    def read_temperature(self):
        if self.w1_device_path and os.path.exists(self.w1_device_path):
            try:
                with open(self.w1_device_path, "r") as f:
                    lines = f.readlines()
                    if len(lines) >= 2 and "YES" in lines[0]:
                        temp_pos = lines[1].find("t=")
                        if temp_pos != -1:
                            return round(float(lines[1][temp_pos + 2:]) / 1000.0, 1)
            except Exception as e:
                logger.warning(f"Real Temp Sensor read error: {e}")
        return 22.0

    def read_motion(self):
        if self.pir:
            try:
                return 1 if self.pir.motion_detected else 0
            except Exception as e:
                logger.warning(f"Real PIR read error: {e}")
        return 0

    def on_connect(self, client, userdata, flags, rc):
        pass

    def on_message(self, client, userdata, msg):
        pass

    def start(self):
        self.connect_mqtt()
        self.start_catalog_thread({
            "id": f"edge-sensors-real-{self.room_id}",
            "name": f"Real Sensors Connector ({self.room_id})",
            "type": "device",
            "hardware": ["ds18b20", "pir_motion"],
            "mode": "real",
            "topics": [f"{self.room_id}/sensors", f"{self.room_id}/status"]
        })
        logger.info(f"Starting REAL sensor polling for {self.room_id}")
        
        try:
            while self.running:
                temp_val, motion_val = self.read_sensors()
                logger.info(f"Real Data - Temp: {temp_val}°C | Motion: {'Detected' if motion_val else 'None'}")
                
                payload = json.dumps({
                    "motion": motion_val,
                    "temperature": temp_val,
                    "sensor_mode": "real"
                })
                try:
                    self.client.publish(f"{self.room_id}/sensors", payload, qos=1)
                except Exception as e:
                    logger.warning(f"Failed to publish real sensor payload: {e}")
                time.sleep(5)
        except KeyboardInterrupt:
            self.shutdown()

if __name__ == "__main__":
    connector = RealDeviceConnector()
    connector.setup_signal_handlers()
    connector.start()
