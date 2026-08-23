import paho.mqtt.client as mqtt
import time, json, signal, sys, threading, requests, logging, sqlite3, uuid
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from config import CentralConfig

logger = logging.getLogger("CentralService")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

class BaseCentralService:
    def __init__(self, client_id_suffix: str, config: CentralConfig = None, use_mqtt: bool = True):
        self.config = config or CentralConfig()
        self.running = True
        self.use_mqtt = use_mqtt
        
        # Build Resilient HTTP Session
        self.session = requests.Session()
        retries = Retry(total=5, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

        # Check for missing critical config keys
        self._check_config_warnings(client_id_suffix)

        if self.use_mqtt:
            unique_id = f"Central_{client_id_suffix}_{uuid.uuid4().hex[:6]}"
            try:
                self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, unique_id)
            except AttributeError:
                self.client = mqtt.Client(unique_id)

            self.client.reconnect_delay_set(min_delay=1, max_delay=120)
            self.client.on_connect = self._on_connect_wrapper
            self.client.on_disconnect = self._on_disconnect_wrapper
            self.client.on_message = self.on_message

    def _check_config_warnings(self, client_id_suffix: str):
        if client_id_suffix == "WeatherService" and not self.config.openweather_api_key:
            logger.warning("⚠️ OPENWEATHER_API_KEY is not configured. Real weather fetch is disabled.")
        if "Telegram" in client_id_suffix and not self.config.telegram_token:
            logger.warning("⚠️ TELEGRAM_TOKEN is not configured. Telegram integration will be disabled.")

    def _on_connect_wrapper(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"✅ Connected to MQTT Broker ({self.config.broker}:{self.config.port})")
            self.on_connect(client, userdata, flags, rc)
        else:
            logger.warning(f"❌ Failed to connect to Broker ({self.config.broker}), rc={rc}")

    def _on_disconnect_wrapper(self, client, userdata, rc):
        if rc == 0:
            logger.info("🔌 Disconnected from MQTT Broker")
        else:
            logger.warning(f"⚠️ Lost connection to Broker, reconnecting... (rc={rc})")
        self.on_disconnect(client, userdata, rc)

    def on_connect(self, client, userdata, flags, rc):
        pass

    def on_disconnect(self, client, userdata, rc):
        pass

    def on_message(self, client, userdata, msg):
        pass

    def get_db_connection(self, db_path: str):
        """Returns a resilient SQLite connection utilizing WAL mode and retry timeouts."""
        conn = None
        for attempt in range(5):
            try:
                conn = sqlite3.connect(db_path, timeout=10.0)
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.row_factory = sqlite3.Row
                return conn
            except sqlite3.OperationalError as e:
                logger.warning(f"SQLite operational error on connection attempt {attempt+1}: {e}")
                time.sleep(0.5)
        raise sqlite3.OperationalError(f"Could not connect to database {db_path} after 5 attempts.")

    def init_all_databases(self):
        """Initializes tables across all four databases if they do not exist."""
        with self.get_db_connection(self.config.db_classroom) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS classroom_metadata (
                room_id TEXT PRIMARY KEY, capacity INTEGER, has_projector BOOLEAN, has_pcs BOOLEAN,
                has_ventilation BOOLEAN, has_camera BOOLEAN, avg_efficiency_score REAL, thermal_loss_rate REAL, thingspeak_api_key TEXT)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS edge_status (
                room_id TEXT PRIMARY KEY, status TEXT, last_updated DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            conn.commit()

        # Timetable Course Schedule DB
        with self.get_db_connection(self.config.db_schedule) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS course_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT, course_name TEXT, start_time DATETIME,
                end_time DATETIME, room_id TEXT, student_count INTEGER DEFAULT 0,
                req_pcs BOOLEAN DEFAULT 0, req_projector BOOLEAN DEFAULT 0, days TEXT, status TEXT DEFAULT 'active')''')
            conn.commit()

        # Telemetry DB
        with self.get_db_connection(self.config.db_sensors) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS sensor_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                room_id TEXT, motion INTEGER, occupancy_count INTEGER, temperature REAL,
                outside_temp REAL, lamp_state TEXT, lamp_front_state TEXT, lamp_back_state TEXT,
                ac_state TEXT, vent_state TEXT, system_mode TEXT DEFAULT 'predictive')''')
            conn.execute('''CREATE TABLE IF NOT EXISTS weather_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                outside_temp REAL, city TEXT)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS efficiency_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                room_id TEXT, efficiency_score REAL)''')
            conn.commit()

        # Control Logs DB
        with self.get_db_connection(self.config.db_control_logs) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS control_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                room_id TEXT, device TEXT, action TEXT, status TEXT DEFAULT 'pending', edge_ack TEXT)''')
            conn.commit()
            
        logger.info("🗄️ Database schemas initialized successfully.")

    def _catalog_registration_loop(self, payload: dict):
        while self.running:
            try:
                response = self.session.post(f"{self.config.catalog_url}/catalog/register", json=payload, timeout=4)
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
                self.client.connect(self.config.broker, self.config.port, 60)
                self.client.loop_start()
                break
            except Exception as e:
                logger.error(f"MQTT Broker connection failed: {e}. Retrying in 5s...")
                time.sleep(5)

    def setup_signal_handlers(self):
        def signal_handler(sig, frame):
            self.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def shutdown(self):
        self.running = False
        if self.use_mqtt:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting MQTT client: {e}")
