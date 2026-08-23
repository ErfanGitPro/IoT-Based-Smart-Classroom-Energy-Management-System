import sqlite3
import os
import requests
import statistics
import threading
import time
import atexit
import logging
from flask import Flask, jsonify, request
from base_service import BaseCentralService
from config import CentralConfig

logger = logging.getLogger("StatisticsService")

app = Flask(__name__)
stats_service_instance = None

class StatisticsService(BaseCentralService):
    def __init__(self, config: CentralConfig = None):
        super().__init__("StatisticsService", config, use_mqtt=False)
        self.update_lock = threading.Lock()
        self.scheduler_stop_event = threading.Event()
        self.init_all_databases()

    def run_efficiency_update(self):
        if not self.update_lock.acquire(blocking=False):
            return {"status": "Skipped", "message": "Update already in progress", "rooms_processed": 0}, 200

        try:
            # Fetch rooms from classroom.db
            with self.get_db_connection(self.config.db_classroom) as classroom_conn:
                rooms = [row['room_id'] for row in classroom_conn.execute("SELECT room_id FROM classroom_metadata").fetchall()]

            updated_count = 0
            for rid in rooms:
                # Query sensor_history from sensors.db
                query = """
                    SELECT temperature 
                    FROM sensor_history 
                    WHERE room_id=? 
                      AND temperature IS NOT NULL 
                      AND system_mode != 'manual' 
                    ORDER BY timestamp DESC 
                    LIMIT 20
                """
                with self.get_db_connection(self.config.db_sensors) as sensors_conn:
                    temps = [t['temperature'] for t in sensors_conn.execute(query, (rid,)).fetchall()]
                    
                    if len(temps) < 3:
                        continue
                    
                    temp_stdev = statistics.stdev(temps)
                    new_score = max(0.0, 100.0 - (temp_stdev * self.config.efficiency_sensitivity))
                    rounded_score = round(new_score, 1)

                    # Insert log to sensors.db (efficiency_history)
                    sensors_conn.execute(
                        "INSERT INTO efficiency_history (room_id, efficiency_score) VALUES (?, ?)",
                        (rid, rounded_score)
                    )
                    sensors_conn.commit()

                # Read & Update metadata in classroom.db
                with self.get_db_connection(self.config.db_classroom) as classroom_conn:
                    prev = classroom_conn.execute("SELECT avg_efficiency_score FROM classroom_metadata WHERE room_id=?",
                                        (rid,)).fetchone()
                    old_score = prev['avg_efficiency_score'] if prev and prev['avg_efficiency_score'] is not None else 100.0

                    if abs(old_score - new_score) >= 1.0:
                        classroom_conn.execute("UPDATE classroom_metadata SET avg_efficiency_score=? WHERE room_id=?",
                                     (rounded_score, rid))
                        classroom_conn.commit()

                        if (old_score - new_score) > 15.0:
                            msg = f"🚨 Efficiency Drop: {rid} fell from {old_score:.1f} to {new_score:.1f}. Check HVAC/Sensors."
                            
                            # Dynamically resolve telegram-bot URL from Catalog
                            telegram_url = self.config.telegram_bot_url
                            try:
                                r = requests.get(f"{self.config.catalog_url}/catalog/services/telegram-bot", timeout=2)
                                if r.status_code == 200:
                                    data = r.json()
                                    if data.get("status") == "success" and "service" in data:
                                        ip = data["service"].get("ip")
                                        if ip:
                                            # Default Flask webhook route on telegram-bot - /api/alert
                                            telegram_url = f"http://{ip}:5004/api/alert" if not ip.startswith("http") else f"{ip}/api/alert"
                            except Exception as lookup_err:
                                logger.debug(f"Failed to lookup telegram bot in catalog: {lookup_err}")

                            try:
                                self.session.post(telegram_url, json={"message": msg}, timeout=3)
                            except Exception as alert_err:
                                logger.error(f"Telegram alert failed: {alert_err}")
                        updated_count += 1

            return {"status": "Updated", "rooms_processed": updated_count}, 200

        except Exception as e:
            logger.error(f"Error in efficiency update: {e}")
            return {"status": "Error", "message": str(e), "rooms_processed": 0}, 500
        finally:
            self.update_lock.release()

    def scheduler_loop(self):
        logger.info(f"⏱️ Auto metrics scheduler enabled: every {self.config.update_metrics_interval_seconds} seconds")
        while not self.scheduler_stop_event.wait(self.config.update_metrics_interval_seconds):
            result, status_code = self.run_efficiency_update()
            if status_code >= 400:
                logger.warning(f"Scheduled metrics update failed: {result}")
            elif result.get('status') != 'Skipped':
                logger.info(f"Scheduled metrics update: {result}")

    def stop_scheduler(self):
        self.scheduler_stop_event.set()

    def start(self):
        # Register with Catalog
        self.start_catalog_thread({
            "id": "statistics-service",
            "name": "Classroom Energy Efficiency Statistics",
            "type": "service",
            "hardware": ["analytics_engine"],
            "topics": ["/api/statistics", "/api/update_metrics"]
        })

        # Start Scheduler Loop thread
        threading.Thread(target=self.scheduler_loop, daemon=True).start()

# Flask Endpoints
@app.route('/api/statistics')
def get_global_stats():
    if not stats_service_instance:
        return jsonify({"error": "Service uninitialized"}), 503
    
    with stats_service_instance.get_db_connection(stats_service_instance.config.db_sensors) as conn:
        res = conn.execute("SELECT AVG(temperature) AS avg_temp FROM sensor_history").fetchone()
        avg = round(res['avg_temp'], 2) if res and res['avg_temp'] else 25.0
        return jsonify({"average_temperature_celsius": avg})

@app.route('/api/update_metrics', methods=['POST'])
def update_metrics():
    if not stats_service_instance:
        return jsonify({"error": "Service uninitialized"}), 503
    result, status_code = stats_service_instance.run_efficiency_update()
    return jsonify(result), status_code

if __name__ == "__main__":
    stats_service_instance = StatisticsService()
    stats_service_instance.setup_signal_handlers()
    stats_service_instance.start()
    
    # Register stop hook on clean system exit
    atexit.register(stats_service_instance.stop_scheduler)

    # Run Flask app
    app.run(host='0.0.0.0', port=5003)