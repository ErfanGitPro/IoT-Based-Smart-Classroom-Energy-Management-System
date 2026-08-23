import sqlite3
import os
import statistics
import logging
from datetime import datetime
from config import CentralConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ThermalModeler")

config = CentralConfig()

def get_db(db_path):
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def calculate_thermal_loss(room_id, days=30):
    """Calculate thermal loss rate during overnight stable periods (AC OFF)."""
    # Check latest state in sensors.db
    try:
        with get_db(config.db_sensors) as sensors_conn:
            latest_ac = sensors_conn.execute("SELECT ac_state FROM sensor_history WHERE room_id=? ORDER BY timestamp DESC LIMIT 1",
                                     (room_id,)).fetchone()
            if latest_ac and "ON" in latest_ac['ac_state'].upper():
                logger.info(f"Skipping {room_id} modeling because AC is currently ON.")
                return None

            data = sensors_conn.execute("""SELECT timestamp, temperature
                                   FROM sensor_history
                                   WHERE room_id=?
                                     AND time (timestamp) BETWEEN '02:00'
                                     AND '05:00'
                                     AND timestamp
                                       > datetime('now'
                                       , ?)
                                   ORDER BY timestamp""",
                                (room_id, f'-{days} days')).fetchall()
    except Exception as e:
        logger.error(f"Error reading sensor history for {room_id}: {e}")
        return None

    if len(data) < 10:
        logger.info(f"Insufficient telemetry data for {room_id} ({len(data)} points found, need at least 10).")
        return None

    decay_rates = []
    for i in range(len(data) - 1):
        t1, t2 = data[i]['temperature'], data[i + 1]['temperature']
        
        try:
            dt1 = datetime.strptime(data[i]['timestamp'].split('.')[0], '%Y-%m-%d %H:%M:%S')
            dt2 = datetime.strptime(data[i + 1]['timestamp'].split('.')[0], '%Y-%m-%d %H:%M:%S')
            hours = (dt2 - dt1).total_seconds() / 3600.0
        except Exception:
            continue
            
        if hours > 0: 
            decay_rates.append(abs(t1 - t2) / hours)

    if not decay_rates:
        return None
    avg_decay = statistics.mean(decay_rates)
    loss_rate = min(1.0, max(0.0, avg_decay / 2.0))
    rounded_loss_rate = round(loss_rate, 2)

    # Update classroom_metadata in classroom.db
    try:
        with get_db(config.db_classroom) as classroom_conn:
            classroom_conn.execute("UPDATE classroom_metadata SET thermal_loss_rate=? WHERE room_id=?", (rounded_loss_rate, room_id))
            classroom_conn.commit()
            logger.info(f"Updated thermal loss rate for {room_id} to {rounded_loss_rate}")
    except Exception as e:
        logger.error(f"Failed to update thermal loss rate for {room_id} in database: {e}")
        
    return rounded_loss_rate

if __name__ == "__main__":
    try:
        with get_db(config.db_classroom) as classroom_conn:
            rooms = classroom_conn.execute("SELECT room_id FROM classroom_metadata").fetchall()
    except Exception as e:
        logger.error(f"Failed to fetch classrooms: {e}")
        rooms = []
        
    for r in rooms:
        rid = r['room_id']
        logger.info(f"Modeling {rid}...")
        res = calculate_thermal_loss(rid)
        logger.info(f" -> Loss Rate for {rid}: {res}")