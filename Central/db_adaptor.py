import json
import threading
import time
import logging
from flask import Flask, jsonify, request
from base_service import BaseCentralService
from config import CentralConfig

logger = logging.getLogger("DBAdaptor")

app = Flask(__name__)
db_service_instance = None

class DBAdaptorService(BaseCentralService):
    def __init__(self, config: CentralConfig = None):
        super().__init__("DBAdaptor", config, use_mqtt=True)
        self.mqtt_connected = False
        self.init_all_databases()

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.mqtt_connected = True
            client.subscribe([("+/state/history", 1), ("+/control/ack", 1), ("+/status", 1)])
            logger.info("✅ MQTT DB Adaptor subscribed successfully")
        else:
            self.mqtt_connected = False

    def on_disconnect(self, client, userdata, rc):
        self.mqtt_connected = False
        logger.info("🔌 MQTT DB Adaptor disconnected")

    def on_message(self, client, userdata, msg):
        topic = msg.topic.strip()
        try:
            payload_str = msg.payload.decode().strip()
        except Exception as e:
            logger.warning(f"Failed to decode payload: {e}")
            return

        if topic.endswith("/status"):
            try:
                data = json.loads(payload_str) if payload_str.startswith("{") else {"status": payload_str}
                room_id = topic.split('/')[0].strip()
                edge_st = data.get("status", "OFFLINE")
                
                with self.get_db_connection(self.config.db_classroom) as conn:
                    conn.execute('''INSERT OR REPLACE INTO edge_status (room_id, status, last_updated)
                                    VALUES (?, ?, CURRENT_TIMESTAMP)''', (room_id, edge_st))
                    conn.commit()
                logger.info(f"📡 Updated LWT Edge Status for {room_id}: {edge_st}")
            except Exception as e:
                logger.error(f"Edge Status Update Error: {e}")

        elif topic.endswith("/state/history"):
            try:
                data = json.loads(payload_str)
                room_id = topic.split('/')[0].strip()
                system_mode = data.get("system_mode", "predictive")
                ac_state_raw = data.get("ac_state", "OFF")
                if isinstance(ac_state_raw, dict): 
                    ac_state_raw = json.dumps(ac_state_raw)
                vent_st = data.get("vent_state", data.get("ventilation_state", "CLOSED"))
                
                with self.get_db_connection(self.config.db_sensors) as conn:
                    conn.execute('''INSERT INTO sensor_history
                        (room_id, motion, occupancy_count, temperature, outside_temp, lamp_state, lamp_front_state, lamp_back_state, ac_state, vent_state, system_mode)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (room_id, data.get("motion", 0), data.get("occupancy_count", 0),
                         data.get("temperature", 25.0), data.get("outside_temp"), data.get("lamp_state", "OFF"),
                         data.get("lamp_front_state", data.get("lamp_state", "OFF")),
                         data.get("lamp_back_state", data.get("lamp_state", "OFF")),
                         ac_state_raw, vent_st,
                         system_mode))
                    conn.commit()
            except Exception as e:
                logger.error(f"DB Telemetry Insert Error: {e}")

        elif topic.endswith("/control/ack"):
            try:
                data = json.loads(payload_str)
                room_id = topic.split('/')[0].strip()
                dev = data.get("device", "unknown")
                
                with self.get_db_connection(self.config.db_control_logs) as conn:
                    pending = conn.execute(
                        '''SELECT id FROM control_logs WHERE room_id = ? AND status = 'pending'
                           AND (device = ? OR device LIKE ? OR ? LIKE '%' || device || '%')
                           ORDER BY datetime(timestamp) DESC, id DESC LIMIT 1''',
                        (room_id, dev, f"%{dev}%", dev)
                    ).fetchone()
                    if pending:
                        conn.execute("UPDATE control_logs SET status='acknowledged', edge_ack=? WHERE id=?", 
                                     (json.dumps(data), pending['id']))
                        conn.commit()
            except Exception as e:
                logger.error(f"ACK Update Error: {e}")

    def purge_loop(self):
        """Periodically cleans records older than retention_days from sensors.db and control_logs.db."""
        logger.info(f"🧹 Database cleaner thread started: Retention set to {self.config.retention_days} days.")
        while self.running:
            try:
                # Purge from sensors.db
                with self.get_db_connection(self.config.db_sensors) as conn:
                    c1 = conn.execute("DELETE FROM sensor_history WHERE datetime(timestamp) < datetime('now', ?)", (f"-{self.config.retention_days} days",))
                    c2 = conn.execute("DELETE FROM weather_history WHERE datetime(timestamp) < datetime('now', ?)", (f"-{self.config.retention_days} days",))
                    c3 = conn.execute("DELETE FROM efficiency_history WHERE datetime(timestamp) < datetime('now', ?)", (f"-{self.config.retention_days} days",))
                    conn.commit()
                    logger.info(f"🧹 Purged old telemetry from sensors.db (Wiped: {c1.rowcount} sensor history, {c2.rowcount} weather, {c3.rowcount} efficiency records)")

                # Purge from control_logs.db
                with self.get_db_connection(self.config.db_control_logs) as conn:
                    c4 = conn.execute("DELETE FROM control_logs WHERE datetime(timestamp) < datetime('now', ?)", (f"-{self.config.retention_days} days",))
                    conn.commit()
                    logger.info(f"🧹 Purged old control logs from control_logs.db (Wiped: {c4.rowcount} control records)")

            except Exception as e:
                logger.error(f"Error executing periodic database purge: {e}")
            
            # Wait for purge interval
            time.sleep(self.config.purge_interval_seconds)

    def start(self):
        self.connect_mqtt()
        self.start_catalog_thread({
            "id": "db-adaptor",
            "name": "SQLite Database Adaptor",
            "type": "service",
            "hardware": ["sqlite3_db"],
            "topics": ["+/state/history", "+/control/ack", "+/status"]
        })
        
        threading.Thread(target=self.purge_loop, daemon=True).start()

# Flask Endpoint Handlers
@app.route('/api/data/history')
def get_history():
    if not db_service_instance:
        return jsonify({"error": "Service uninitialized"}), 503
    rid = request.args.get('room_id')
    
    with db_service_instance.get_db_connection(db_service_instance.config.db_sensors) as conn:
        q = 'SELECT * FROM sensor_history ORDER BY timestamp DESC LIMIT 100'
        p = ()
        if rid:
            q = 'SELECT * FROM sensor_history WHERE room_id = ? ORDER BY timestamp DESC LIMIT 100'
            p = (rid,)
        rows = conn.execute(q, p).fetchall()
        return jsonify({"history": [dict(r) for r in rows]})

@app.route('/api/latest_state')
def get_latest_state():
    if not db_service_instance:
        return jsonify({"error": "Service uninitialized"}), 503
    rid = request.args.get('room_id')
    if not rid:
        return jsonify({})
        
    with db_service_instance.get_db_connection(db_service_instance.config.db_sensors) as conn:
        res = conn.execute('SELECT * FROM sensor_history WHERE room_id = ? ORDER BY timestamp DESC LIMIT 1', (rid,)).fetchone()
        return jsonify(dict(res) if res else {})

@app.route('/api/health')
def get_health():
    if not db_service_instance:
        return jsonify({"error": "Service uninitialized"}), 503
        
    with db_service_instance.get_db_connection(db_service_instance.config.db_sensors) as conn:
        res = conn.execute('SELECT MAX(timestamp) as last_ts FROM sensor_history').fetchone()
        last_ts = res['last_ts'] if res and res['last_ts'] else None
        return jsonify({
            "mqtt_connected": db_service_instance.mqtt_connected,
            "last_db_timestamp": last_ts,
            "service": "db_adaptor"
        })

if __name__ == "__main__":
    db_service_instance = DBAdaptorService()
    db_service_instance.setup_signal_handlers()
    db_service_instance.start()
    
    # Run Flask server
    app.run(host='0.0.0.0', port=5002)