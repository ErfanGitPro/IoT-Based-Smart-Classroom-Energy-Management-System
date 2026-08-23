import time
import os
import requests
import sqlite3
import logging
import json
from flask import Flask, jsonify
from base_service import BaseCentralService
from config import CentralConfig

logger = logging.getLogger("WeatherService")

app = Flask(__name__)
weather_service_instance = None

class WeatherService(BaseCentralService):
    def __init__(self, config: CentralConfig = None):
        super().__init__("WeatherService", config, use_mqtt=True)
        self.init_all_databases()

    def get_outside_temp(self):
        if not self.config.openweather_api_key:
            return 25.0
        try:
            url = f"http://api.openweathermap.org/data/2.5/weather?q={self.config.weather_city}&appid={self.config.openweather_api_key}&units=metric"
            res = self.session.get(url, timeout=10)
            return res.json()["main"]["temp"] if res.status_code == 200 else 25.0
        except Exception as e:
            logger.error(f"Failed to fetch outdoor temperature: {e}")
            return 25.0

    def persist_weather_sample(self, outside_temp):
        try:
            with self.get_db_connection(self.config.db_sensors) as conn:
                conn.execute(
                    'INSERT INTO weather_history (outside_temp, city) VALUES (?, ?)',
                    (outside_temp, self.config.weather_city)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist weather sample: {e}")

    def suggest_ventilation(self):
        outside = self.get_outside_temp()
        self.persist_weather_sample(outside)

        # Publish system broadcast weather
        self.client.publish("system/weather",
                            json.dumps({"outside_temp": outside, "city": self.config.weather_city, "timestamp": time.time()}), retain=True)

        # Fetch rooms with ventilation from classroom.db
        try:
            with self.get_db_connection(self.config.db_classroom) as classroom_conn:
                vent_rooms = [row['room_id'] for row in classroom_conn.execute("SELECT room_id FROM classroom_metadata WHERE has_ventilation = 1").fetchall()]
        except Exception as e:
            logger.error(f"Failed to fetch ventilation rooms: {e}")
            vent_rooms = []

        # Query latest sensor readings for each room in sensors.db
        try:
            with self.get_db_connection(self.config.db_sensors) as sensors_conn:
                for rid in vent_rooms:
                    row = sensors_conn.execute("SELECT temperature FROM sensor_history WHERE room_id = ? AND temperature IS NOT NULL ORDER BY timestamp DESC LIMIT 1", (rid,)).fetchone()
                    if row:
                        temp = row['temperature']
                        if temp and outside < temp and temp > 24:
                            msg = f"🌬️ Ventilation Alert for {rid}: Outside {outside}°C, Inside {temp}°C."
                            logger.info(msg)
                            # Dynamically resolve telegram-bot URL from Catalog
                            telegram_url = self.config.telegram_bot_url
                            try:
                                r = requests.get(f"{self.config.catalog_url}/catalog/services/telegram-bot", timeout=2)
                                if r.status_code == 200:
                                    data = r.json()
                                    if data.get("status") == "success" and "service" in data:
                                        ip = data["service"].get("ip")
                                        if ip:
                                            # Default Flask webhook route on telegram-bot
                                            telegram_url = f"http://{ip}:5004/api/alert" if not ip.startswith("http") else f"{ip}/api/alert"
                            except Exception as lookup_err:
                                logger.debug(f"Failed to lookup telegram bot in catalog: {lookup_err}")

                            try:
                                self.session.post(telegram_url, json={"message": msg}, timeout=3)
                            except Exception as alert_err:
                                logger.error(f"Telegram alert request failed: {alert_err}")
                                
                            self.client.publish(f"{rid}/ventilation/suggest", json.dumps({
                                "action": "activate", "outside_temp": outside, "inside_temp": temp,
                                "priority": "high" if (temp - outside) > 5 else "normal"
                            }), retain=False)
        except Exception as e:
            logger.error(f"Error suggesting ventilation: {e}")

    def start(self):
        self.connect_mqtt()
        logger.info("🌍 Weather service connected to MQTT.")
        
        # Start Flask app thread
        from threading import Thread
        Thread(target=lambda: app.run(host='0.0.0.0', port=5005), daemon=True).start()

        while self.running:
            self.suggest_ventilation()
            time.sleep(3600)

@app.route('/api/weather', methods=['GET'])
def get_weather():
    if not weather_service_instance:
        return jsonify({"error": "Service uninitialized"}), 503
    temp = weather_service_instance.get_outside_temp()
    return jsonify({"outside_temperature": temp, "city": weather_service_instance.config.weather_city})

if __name__ == "__main__":
    weather_service_instance = WeatherService()
    weather_service_instance.setup_signal_handlers()
    weather_service_instance.start()