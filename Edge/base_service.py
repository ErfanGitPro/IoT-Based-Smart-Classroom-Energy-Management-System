import paho.mqtt.client as mqtt
import time, json, signal, sys, threading, requests, logging
from config import EdgeConfig

logger = logging.getLogger("EdgeService")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

class BaseMQTTService:
    def __init__(self, client_id_suffix: str, config: EdgeConfig = None):
        self.config = config or EdgeConfig()
        self.room_id = self.config.room_id
        self.catalog_url = self.config.catalog_url
        self.running = True

        # Resolve MQTT Broker host and port dynamically from the Catalog
        broker_resolved = False
        self.broker = self.config.broker
        self.broker_port = 1883
        
        logger.info("Resolving MQTT broker from Catalog REST registry...")
        for attempt in range(10):
            try:
                response = requests.get(f"{self.catalog_url}/catalog/broker", timeout=2)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        resolved_broker = data.get("broker", self.config.broker)
                        # Translate internal container references for external Edge modules
                        if resolved_broker in ("mqtt-broker", "localhost", "127.0.0.1"):
                            self.broker = self.config.broker
                        else:
                            self.broker = resolved_broker
                        self.broker_port = int(data.get("port", 1883))
                        logger.info(f"Resolved MQTT broker from Catalog: {self.broker}:{self.broker_port}")
                        broker_resolved = True
                        break
            except Exception as e:
                logger.debug(f"Catalog broker lookup attempt {attempt+1} failed: {e}")
            time.sleep(2)
        
        if not broker_resolved:
            logger.warning(f"Could not resolve broker from Catalog. Falling back to configured broker: {self.broker}")

        client_id = f"{self.room_id}_{client_id_suffix}"
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id)
        except AttributeError:
            self.client = mqtt.Client(client_id)

        self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        self.client.on_connect = self._on_connect_wrapper
        self.client.on_disconnect = self._on_disconnect_wrapper
        self.client.on_message = self.on_message

        # Set Last Will and Testament (LWT)
        self.client.will_set(
            f"{self.room_id}/status",
            json.dumps({"room_id": self.room_id, "status": "OFFLINE", "reason": "unexpected_disconnect"}),
            qos=1,
            retain=True
        )

    def _on_connect_wrapper(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"✅ Connected to Central Broker ({self.broker}) for room {self.room_id}")
            try:
                self.client.publish(
                    f"{self.room_id}/status",
                    json.dumps({"room_id": self.room_id, "status": "ONLINE"}),
                    qos=1,
                    retain=True
                )
            except Exception as e:
                logger.warning(f"Failed to publish status online: {e}")
            self.on_connect(client, userdata, flags, rc)
        else:
            logger.warning(f"Failed to connect to Central Broker ({self.broker}), rc={rc}")

    def _on_disconnect_wrapper(self, client, userdata, rc):
        if rc == 0:
            logger.info(f"🔌 Disconnected from Central ({self.broker})")
        else:
            logger.warning(f"⚠️ Lost connection to Central ({self.broker}), reconnecting... (rc={rc})")
        self.on_disconnect(client, userdata, rc)

    def on_connect(self, client, userdata, flags, rc):
        pass

    def on_disconnect(self, client, userdata, rc):
        pass

    def on_message(self, client, userdata, msg):
        pass

    def _catalog_registration_loop(self, payload: dict):
        while self.running:
            try:
                response = requests.post(f"{self.catalog_url}/catalog/register", json=payload, timeout=4)
                if response.status_code != 200:
                    logger.debug(f"Catalog registration response code: {response.status_code}")
            except Exception as e:
                logger.debug(f"Catalog registration heartbeat failed: {e}")
            time.sleep(30)

    def start_catalog_thread(self, payload: dict):
        threading.Thread(target=self._catalog_registration_loop, args=(payload,), daemon=True).start()

    def connect_mqtt(self):
        while self.running:
            try:
                self.client.connect(self.broker, self.broker_port, 60)
                self.client.loop_start()
                break
            except Exception as e:
                logger.error(f"MQTT connection failed: {e}. Retrying in 5s...")
                time.sleep(5)

    def setup_signal_handlers(self):
        def signal_handler(sig, frame):
            self.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def shutdown(self):
        self.running = False
        try:
            self.client.publish(
                f"{self.room_id}/status",
                json.dumps({"room_id": self.room_id, "status": "OFFLINE"}),
                qos=1,
                retain=True
            )
        except Exception as e:
            logger.error(f"Error publishing offline status: {e}")
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting client: {e}")


class BaseDeviceConnector(BaseMQTTService):
    # Abstract Class for Device Connectors (Real & Fake)
    def __init__(self, service_name: str, config: EdgeConfig = None):
        super().__init__(service_name, config)

    def read_sensors(self):
        raise NotImplementedError
