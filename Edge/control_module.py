import time, json, threading, sys, logging, requests
from base_service import BaseMQTTService
from config import EdgeConfig

logger = logging.getLogger("ControlModule")

class HVACController:
    """Manages rules for HVAC (AC, Fan, Precool, Manual overrides, Ventilation)."""
    def __init__(self, room_id: str, default_precool: int, client):
        self.room_id = room_id
        self.default_ac_precool_temp = default_precool
        self.client = client

        self.ac_is_on = False
        self.ac_fan_speed = "LOW"
        self.ac_precool_active = False
        self.ac_precool_source = None
        self.ac_precool_class_start = None
        self.ac_precool_target_temp = self.default_ac_precool_temp
        self.ac_precool_expires = 0
        self.ac_on_since = 0
        self.ac_cooldown_period = 0
        self.ac_manual_override = False
        self.ac_manual_target = None

        self.ventilation_suggested = False
        self.ventilation_active = False
        self.ventilation_cooldown_period = 0
        self.ventilation_manual_override = False

    def resolve_manual(self, ctx, resolved, outside_temp, outside_temp_updated, current_temp):
        manual_recent = ctx["manual_recent"]

        if self.ac_manual_override and not self.ac_precool_active:
            target = str(self.ac_manual_target or "OFF").upper()
            if self.ventilation_active:
                self.ventilation_active = False
                self.ventilation_cooldown_period = time.time() + 300
                self.client.publish(f"{self.room_id}/state/ventilation", "CLOSED", qos=1, retain=True)

            if target == "OFF":
                if self.ac_is_on:
                    self.ac_is_on = False
                    self.ac_fan_speed = "LOW"
                    self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF", "reason": "MANUAL_OVERRIDE"}), qos=1, retain=True)
            else:
                desired_fan = "HIGH" if target == "ON" else (target if target in {"LOW", "MEDIUM", "HIGH"} else "HIGH")
                should_publish = (not self.ac_is_on) or (self.ac_fan_speed != desired_fan)
                self.ac_is_on = True
                self.ac_fan_speed = desired_fan
                if should_publish:
                    if not self.ac_on_since: self.ac_on_since = time.time()
                    self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "ON", "temp": self.default_ac_precool_temp, "fan": desired_fan, "reason": "MANUAL_OVERRIDE"}), qos=1, retain=True)
            resolved["ac"] = True

        if self.ac_manual_override:
            fresh_weather = (time.time() - outside_temp_updated) < 7200
            diff = current_temp - outside_temp
            cooldown_ok = time.time() > self.ventilation_cooldown_period

            if manual_recent: 
                use_vent = False
            else:
                desired_vent = (fresh_weather and self.ventilation_suggested and diff > 2.0 and 
                                current_temp > 24.0 and outside_temp < 26.0)
                use_vent = desired_vent if cooldown_ok else self.ventilation_active

            if use_vent:
                logger.info("🌬️ Ventilation overriding manual AC for free cooling")
                self.ac_manual_override = False
            else:
                resolved["ac"] = True
                resolved["vent"] = True

        if self.ventilation_manual_override:
            desired_vent = self.ventilation_suggested
            self.ventilation_active = desired_vent
            self.ventilation_cooldown_period = time.time() + 300
            self.client.publish(f"{self.room_id}/state/ventilation", "OPEN" if desired_vent else "CLOSED", qos=1, retain=True)
            resolved["vent"] = True

    def resolve_precool(self, ctx, resolved, is_scheduled, current_temp):
        if resolved["ac"]: return
        if self.ac_precool_active:
            if time.time() > self.ac_precool_expires + 300:
                logger.warning("⚠️ Precool safety timeout hit. Forcing OFF.")
                self.ac_precool_active = False; self.ac_precool_source = None; self.ac_is_on = False; self.ac_fan_speed = "LOW"
            if is_scheduled:
                self.ac_precool_active = False; self.ac_precool_source = None
                logger.info("🔄 Precool ended, returning control to automation.")
            elif (not self.ac_is_on and current_temp > self.ac_precool_target_temp + 1.0) or (self.ac_is_on and current_temp > self.ac_precool_target_temp):
                if not self.ac_is_on:
                    self.ac_is_on = True; self.ac_on_since = time.time(); self.ac_fan_speed = "HIGH"
                self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "PRECOOL", "target": self.ac_precool_target_temp}), qos=1, retain=True)
            else:
                self.ac_is_on = False
                self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF", "reason": "PRECOOL_REACHED"}), qos=1, retain=True)
            resolved["ac"] = True; resolved["vent"] = True

    def resolve_predictive(self, ctx, resolved, outside_temp, outside_temp_updated, current_temp, is_scheduled, current_occupancy, occ_high):
        if resolved["ac"] and resolved["vent"]: return

        occupied = ctx["occupied"]
        timeout = ctx["timeout"]
        time_empty = ctx["time_empty"]
        threshold_on = ctx["threshold_on"]
        threshold_off = ctx["threshold_off"]

        # Ventilation vs AC Balance
        if not resolved["vent"]:
            fresh_weather = (time.time() - outside_temp_updated) < 7200
            diff = current_temp - outside_temp
            cooldown_ok = time.time() > self.ventilation_cooldown_period
            desired_vent = (fresh_weather and self.ventilation_suggested and diff > 2.0 and
                            current_temp > 24.0 and outside_temp < 26.0)
            use_vent = desired_vent if cooldown_ok else self.ventilation_active
        else: use_vent = self.ventilation_active

        if use_vent:
            if not self.ventilation_active:
                self.ventilation_active = True
                self.ventilation_cooldown_period = time.time() + 300
                self.client.publish(f"{self.room_id}/state/ventilation", "OPEN", qos=1, retain=True)
            if self.ac_is_on and not resolved["ac"]:
                self.ac_is_on = False; self.ac_fan_speed = "LOW"
                self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF", "reason": "VENT_ACTIVE"}), qos=1, retain=True)
        else:
            if self.ventilation_active and not resolved["vent"]:
                self.ventilation_active = False
                self.ventilation_cooldown_period = time.time() + 300
                self.client.publish(f"{self.room_id}/state/ventilation", "CLOSED", qos=1, retain=True)
            if not resolved["ac"] and not self.ac_manual_override and not use_vent:
                if occupied or is_scheduled:
                    if not self.ac_is_on and current_temp > threshold_on:
                        if time.time() > self.ac_cooldown_period:
                            self.ac_is_on = True; self.ac_on_since = time.time()
                            self.ac_fan_speed = "HIGH" if current_occupancy > occ_high else ("MEDIUM" if current_occupancy > 10 else "LOW")
                            self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "ON", "temp": self.default_ac_precool_temp, "fan": self.ac_fan_speed}), qos=1, retain=True)
                    elif self.ac_is_on:
                        if current_temp < threshold_off:
                            self.ac_is_on = False; self.ac_fan_speed = "LOW"
                            self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF"}), qos=1, retain=True)
                        elif time.time() - self.ac_on_since >= 1200:
                            logger.info("⏳ Duty Cycle: AC max continuous run-time reached. Resting compressor for 5 mins.")
                            self.ac_is_on = False; self.ac_fan_speed = "LOW"
                            self.ac_cooldown_period = time.time() + 300
                            self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF", "reason": "DUTY_CYCLE"}), qos=1, retain=True)
                elif time_empty > timeout and self.ac_is_on:
                    self.ac_is_on = False; self.ac_fan_speed = "LOW"
                    self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF"}), qos=1, retain=True)


class LightingController:
    """Manages rules for Room Lamps (Front, Back, overrides)."""
    def __init__(self, room_id: str, client):
        self.room_id = room_id
        self.client = client

        self.lamp_front_is_on = False
        self.lamp_back_is_on = False
        self.lamp_front_manual = None
        self.lamp_back_manual = None

    def resolve_predictive(self, ctx, resolved, is_scheduled):
        occupied = ctx["occupied"]
        timeout = ctx["timeout"]
        time_empty = ctx["time_empty"]
        manual_recent = ctx.get("manual_recent", False)

        if not resolved["lamps"]:
            if occupied or is_scheduled:
                desired_front = self.lamp_front_manual if self.lamp_front_manual is not None else True
                desired_back = self.lamp_back_manual if self.lamp_back_manual is not None else True

                if self.lamp_front_is_on != desired_front:
                    self.client.publish(f"{self.room_id}/state/lamp/front", "ON" if desired_front else "OFF", qos=1, retain=True)
                    self.lamp_front_is_on = desired_front
                if self.lamp_back_is_on != desired_back:
                    self.client.publish(f"{self.room_id}/state/lamp/back", "ON" if desired_back else "OFF", qos=1, retain=True)
                    self.lamp_back_is_on = desired_back
            elif time_empty > timeout and not manual_recent:
                self.lamp_front_manual = None
                self.lamp_back_manual = None
                if self.lamp_front_is_on:
                    self.client.publish(f"{self.room_id}/state/lamp/front", "OFF", qos=1, retain=True)
                    self.lamp_front_is_on = False
                if self.lamp_back_is_on:
                    self.client.publish(f"{self.room_id}/state/lamp/back", "OFF", qos=1, retain=True)
                    self.lamp_back_is_on = False
            resolved["lamps"] = True


class ControlModule(BaseMQTTService):
    def __init__(self, config: EdgeConfig = None):
        super().__init__("Control", config)

        self.default_ac_precool_temp = self.config.default_ac_precool_temp
        self.threshold_base = self.config.threshold_base
        self.holdup_band = self.config.holdup_band
        self.manual_mode_hold_seconds = self.config.manual_mode_hold_seconds

        self.eval_fast = self.config.eval_fast
        self.eval_medium = self.config.eval_medium
        self.eval_slow = self.config.eval_slow
        self.occ_high = self.config.occupancy_high_threshold

        self.sensor_mode = self.config.sensor_mode
        self.sensor_connector = None

        # State Variables
        self.last_motion_time = time.time()
        self.current_temp = 25.0
        self.current_motion = 0
        self.current_occupancy = 0
        self.last_occupancy_update = 0
        self.is_scheduled = False
        self.local_schedule = []
        self.thermal_loss_rate = 0.5

        self.outside_temp = 25.0
        self.outside_temp_updated = 0

        self.last_state_report = 0
        self.last_manual_command_time = 0

        self.system_mode = "predictive"
        self.state_lock = threading.RLock()

        # Initialize Controllers
        self.hvac = HVACController(self.room_id, self.default_ac_precool_temp, self.client)
        self.lighting = LightingController(self.room_id, self.client)

        self.client.on_log = self.on_log

    def log_manual_command(self, topic, payload):
        self.last_manual_command_time = time.time()
        logger.info(f"🖐️ Received manual command on {topic}: {payload}")

    def evaluate_logic(self):
        with self.state_lock:
            occ_fresh = (time.time() - self.last_occupancy_update) < 360
            occupied = self.current_motion == 1 or (self.current_occupancy > 0 and occ_fresh)
            timeout = 300 if self.is_scheduled else 15
            holdup = self.holdup_band * (0.8 if self.is_scheduled else 1.0) + min(self.current_occupancy*0.05, 2.0)
            adaptive_base = self.threshold_base
            if self.outside_temp > 32.0: adaptive_base += 1.5
            elif self.outside_temp < 15.0: adaptive_base -= 1.0
                
            threshold_on = adaptive_base + (holdup/2)
            threshold_off = adaptive_base - (holdup/2)
            time_empty = time.time() - self.last_motion_time

            from datetime import datetime
            now_time_str = datetime.now().strftime("%H:%M")
            self.is_scheduled = False
            for item in self.local_schedule:
                if item["start"] <= now_time_str < item["end"]:
                    self.is_scheduled = True
                    break

            # Interruption check for delayed/cancelled classes
            if self.hvac.ac_precool_active and self.hvac.ac_precool_source == "schedule":
                still_scheduled = any(item["start"] == self.hvac.ac_precool_class_start for item in self.local_schedule)
                if not still_scheduled:
                    logger.info(f"🛑 [Pre-cooling] Class at {self.hvac.ac_precool_class_start} was delayed or cancelled. Interrupting precool.")
                    self.hvac.ac_precool_active = False
                    self.hvac.ac_precool_source = None
                    self.hvac.ac_precool_class_start = None
                    self.hvac.ac_is_on = False
                    self.hvac.ac_fan_speed = "LOW"
                    self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF", "reason": "SCHEDULE_CHANGE"}), qos=1, retain=True)

            # Local Pre-cooling Logic
            if not self.is_scheduled and (self.thermal_loss_rate is None or self.thermal_loss_rate <= 0.5) and self.current_temp > self.default_ac_precool_temp:
                next_class_start = None
                for item in sorted(self.local_schedule, key=lambda x: x["start"]):
                    if item["start"] > now_time_str:
                        next_class_start = item["start"]
                        break
                
                if next_class_start:
                    try:
                        today_str = datetime.now().strftime("%Y-%m-%d")
                        class_start_dt = datetime.strptime(f"{today_str} {next_class_start}", "%Y-%m-%d %H:%M")
                        mins_to_start = (class_start_dt - datetime.now()).total_seconds() / 60.0
                        temp_diff = self.current_temp - self.default_ac_precool_temp
                        loss_rate = self.thermal_loss_rate if self.thermal_loss_rate is not None else 0.5
                        dynamic_duration = min(60, max(5, int(temp_diff * 3.0 * (1.0 + loss_rate))))
                        
                        if 0 < mins_to_start <= dynamic_duration:
                            if not self.hvac.ac_precool_active:
                                logger.info(f"❄️ [Local Pre-cooling] Starting pre-cooling for class at {next_class_start} (Duration: {dynamic_duration} mins)")
                                self.hvac.ac_precool_active = True
                                self.hvac.ac_precool_source = "schedule"
                                self.hvac.ac_precool_class_start = next_class_start
                                self.hvac.ac_manual_override = False
                                self.hvac.ac_manual_target = None
                                self.hvac.ac_precool_target_temp = self.default_ac_precool_temp
                                self.hvac.ac_precool_expires = time.time() + (dynamic_duration * 60)
                                self.hvac.ac_is_on = True
                                self.hvac.ac_fan_speed = "HIGH"
                                self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "PRECOOL", "target": self.default_ac_precool_temp, "expires": self.hvac.ac_precool_expires, "source": "schedule"}), qos=1, retain=True)
                    except Exception as e:
                        logger.warning(f"Error evaluating local pre-cooling: {e}")

            if occupied or self.is_scheduled:
                self.last_motion_time = time.time()

            manual_recent = (time.time() - self.last_manual_command_time) <= self.manual_mode_hold_seconds

            # Clear manual lamp overrides only if manual hold window expires AND room is empty
            if not manual_recent and not occupied:
                if self.lighting.lamp_front_manual is not None:
                    self.lighting.lamp_front_manual = None
                if self.lighting.lamp_back_manual is not None:
                    self.lighting.lamp_back_manual = None

            # Expire manual control only if window elapses AND room is empty
            if not manual_recent and not occupied:
                if self.hvac.ac_manual_override or self.hvac.ventilation_manual_override:
                    logger.info("🔄 Manual hold expired + Room empty → clearing Manual Latch ")
                    self.hvac.ac_manual_override = False
                    self.hvac.ventilation_manual_override = False
                    self.hvac.ac_manual_target = None

            # Mode Tracking & Auto-Revert
            if self.system_mode == "manual" and not occupied and not manual_recent and not self.is_scheduled:
                logger.info("🔄 Room empty + Manual window closed → reverting to Predictive mode ")
                self.hvac.ac_is_on = False
                self.hvac.ac_fan_speed = "LOW"
                self.system_mode = "predictive"
                self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "OFF", "reason": "AUTO_REVERT_IDLE"}), qos=1, retain=True)

            if self.hvac.ac_precool_active:
                source = str(self.hvac.ac_precool_source or "schedule").lower()
                self.system_mode = "manual" if source == "manual" else "scheduled_precool"
            elif manual_recent or self.hvac.ac_manual_override or self.hvac.ventilation_manual_override or self.lighting.lamp_front_manual is not None or self.lighting.lamp_back_manual is not None:
                self.system_mode = "manual"
            else:
                self.system_mode = "predictive"

            ctx = {"occupied": occupied, "timeout": timeout, "time_empty": time_empty, "threshold_on": threshold_on, "threshold_off": threshold_off, "manual_recent": manual_recent}
            resolved = {"ac": False, "vent": False, "lamps": False}

            self.hvac.resolve_manual(ctx, resolved, self.outside_temp, self.outside_temp_updated, self.current_temp)
            self.hvac.resolve_precool(ctx, resolved, self.is_scheduled, self.current_temp)
            self.lighting.resolve_predictive(ctx, resolved, self.is_scheduled)
            self.hvac.resolve_predictive(ctx, resolved, self.outside_temp, self.outside_temp_updated, self.current_temp, self.is_scheduled, self.current_occupancy, self.occ_high)

            now = time.time()
            if now - self.last_state_report >= 5:
                ac_status = json.dumps({"status": "ON", "fan": self.hvac.ac_fan_speed}) if self.hvac.ac_is_on else json.dumps({"status": "OFF"})
                lamp_front_status = "ON" if self.lighting.lamp_front_is_on else "OFF"
                lamp_back_status = "ON" if self.lighting.lamp_back_is_on else "OFF"
                lamp_status = "ON" if (self.lighting.lamp_front_is_on or self.lighting.lamp_back_is_on) else "OFF"
                vent_status = "OPEN" if self.hvac.ventilation_active else "CLOSED"
                ac_on = bool(self.hvac.ac_is_on)
                ac_fan = self.hvac.ac_fan_speed if ac_on else "OFF"

                logger.info(f"🎛️ Control Data - AC: {'ON' if ac_on else 'OFF'} | Fan: {ac_fan} | Lamps: Front={lamp_front_status}, Back={lamp_back_status} | Vent: {vent_status} | Mode: {self.system_mode.upper()} ")
                self.client.publish(f"{self.room_id}/state/history", json.dumps({"motion": self.current_motion, "occupancy_count": self.current_occupancy, "temperature": self.current_temp, "outside_temp": self.outside_temp, "lamp_state": lamp_status, "lamp_front_state": lamp_front_status, "lamp_back_state": lamp_back_status, "ac_state": ac_status, "vent_state": vent_status, "system_mode": self.system_mode}), qos=1)
                self.last_state_report = now

    def on_message(self, client, userdata, msg):
        with self.state_lock:
            topic = msg.topic.strip()
            try: 
                payload = msg.payload.decode().strip()
            except Exception as e: 
                logger.warning(f"Failed to decode message: {e}")
                return

            if topic == f"{self.room_id}/request_state":
                ac_val = json.dumps({"status": "ON", "temp": self.default_ac_precool_temp, "fan": self.hvac.ac_fan_speed}) if self.hvac.ac_is_on else json.dumps({"status": "OFF"})
                self.client.publish(f"{self.room_id}/state/ac", ac_val, qos=1, retain=True)
                self.client.publish(f"{self.room_id}/state/lamp/front", "ON" if self.lighting.lamp_front_is_on else "OFF", qos=1, retain=True)
                self.client.publish(f"{self.room_id}/state/lamp/back", "ON" if self.lighting.lamp_back_is_on else "OFF", qos=1, retain=True)
                self.client.publish(f"{self.room_id}/state/ventilation", "OPEN" if self.hvac.ventilation_active else "CLOSED", qos=1, retain=True)
                return
            if topic == f"{self.room_id}/config/threshold":
                try:
                    cfg = json.loads(payload)
                    if "base" in cfg: self.threshold_base = float(cfg["base"])
                    if "holdup" in cfg: self.holdup_band = float(cfg["holdup"])
                except Exception as e:
                    logger.warning(f"Failed to parse threshold config: {e}")
                return
            if topic == f"{self.room_id}/ac/control":
                self.log_manual_command(topic, payload); command = payload.upper(); self.hvac.ac_manual_override = True; self.hvac.ac_manual_target = command
                if command == "OFF": self.hvac.ac_is_on = False; self.hvac.ac_fan_speed = "LOW"; self.hvac.ac_precool_active = False; self.hvac.ac_precool_source = None
                elif command in {"LOW", "MEDIUM", "HIGH"}:
                    self.hvac.ac_fan_speed = command; self.hvac.ac_is_on = True; self.hvac.ac_on_since = time.time() if not self.hvac.ac_is_on else self.hvac.ac_on_since; self.hvac.ac_precool_active = False; self.hvac.ac_precool_source = None
                else:
                    self.hvac.ac_is_on = True; self.hvac.ac_on_since = time.time() if not self.hvac.ac_is_on else self.hvac.ac_on_since; self.hvac.ac_fan_speed = "HIGH" if command == "ON" else self.hvac.ac_fan_speed; self.hvac.ac_precool_active = False; self.hvac.ac_precool_source = None
                self.client.publish(f"{self.room_id}/control/ack", json.dumps({"device": "ac", "action": command, "status": "executed"}), qos=1)
                if command == "OFF": ac_payload = json.dumps({"status": "OFF", "reason": "MANUAL_OVERRIDE"})
                else: ac_payload = json.dumps({"status": "ON", "temp": self.default_ac_precool_temp, "fan": command if command in {"LOW","MEDIUM","HIGH"} else "HIGH", "reason": "MANUAL_OVERRIDE"})
                self.client.publish(f"{self.room_id}/state/ac", ac_payload, qos=1, retain=True)
                self.last_state_report = 0
                self.evaluate_logic()
                return
            if topic == f"{self.room_id}/ac/precool":
                try:
                    data = json.loads(payload)
                    target = data.get("target_temp", self.default_ac_precool_temp)
                    source = str(data.get("source", "schedule")).strip().lower() or "schedule"
                    if source not in {"manual", "schedule"}: source = "schedule"
                    if source == "manual": self.log_manual_command(topic, payload)
                    if self.current_temp <= target:
                        logger.info("⚠️ Precool skipped (current temp already below target)")
                        self.client.publish(f"{self.room_id}/control/ack", json.dumps({"device": "ac", "action": "PRECOOL", "status": "skipped", "reason": "already_below_target", "source": source}), qos=1); return
                    self.hvac.ac_precool_active = True; self.hvac.ac_precool_source = source; self.hvac.ac_manual_override = False; self.hvac.ac_manual_target = None
                    self.hvac.ac_precool_target_temp = target; self.hvac.ac_precool_expires = time.time() + (data.get("duration_minutes", 15) * 60)
                    self.hvac.ac_is_on = True; self.hvac.ac_fan_speed = "HIGH"
                    self.client.publish(f"{self.room_id}/state/ac", json.dumps({"status": "PRECOOL", "target": target, "expires": self.hvac.ac_precool_expires, "source": source}), qos=1, retain=True)
                    self.client.publish(f"{self.room_id}/control/ack", json.dumps({"device": "ac", "action": "PRECOOL", "status": "executed", "target": target, "source": source}), qos=1)
                except Exception as e: 
                    logger.warning(f"Failed to parse precool: {e}")
                    self.client.publish(f"{self.room_id}/control/ack", json.dumps({"device": "ac", "action": "PRECOOL", "status": "failed", "error": str(e)}), qos=1)
                self.last_state_report = 0
                self.evaluate_logic()
                return
            if topic == f"{self.room_id}/ventilation/suggest":
                try:
                    d = json.loads(payload); self.log_manual_command(topic, payload)
                    self.hvac.ventilation_manual_override = True; self.hvac.ventilation_suggested = (d.get("action") == "activate")
                    self.last_state_report = 0
                    self.evaluate_logic()
                    self.client.publish(f"{self.room_id}/control/ack", json.dumps({"device": "ventilation", "action": "OPEN" if self.hvac.ventilation_suggested else "CLOSE", "status": "executed"}), qos=1)
                except Exception as e: 
                    logger.warning(f"Failed to suggest ventilation: {e}")
                return
            if "lamp" in topic and topic.endswith("/control"):
                self.log_manual_command(topic, payload)
                dev = topic.split("/")[-2]; action = str(payload).strip().upper(); val = action == "ON"
                if dev == "front": self.lighting.lamp_front_is_on = val; self.lighting.lamp_front_manual = val
                elif dev == "back": self.lighting.lamp_back_is_on = val; self.lighting.lamp_back_manual = val
                self.client.publish(f"{self.room_id}/state/lamp/{dev}", action, qos=1, retain=True)
                self.client.publish(f"{self.room_id}/control/ack", json.dumps({"device": f"lamp/{dev}", "action": action, "status": "executed"}))
                self.last_state_report = 0
                self.evaluate_logic()
                return
            if topic == "system/weather":
                try:
                    d = json.loads(payload)
                    out_t = d.get("outside_temp", d.get("outside_temperature"))
                    if out_t is not None: self.outside_temp = float(out_t); self.outside_temp_updated = time.time(); self.evaluate_logic()
                except Exception as e: 
                    logger.debug(f"Failed to parse weather: {e}")
                return
            if topic == f"{self.room_id}/sensors":
                try:
                    d = json.loads(payload)
                    self.current_motion = d.get("motion", 0) if isinstance(d, dict) else (d if isinstance(d, int) else 0)
                    self.current_temp = d.get("temperature", 25.0) if isinstance(d, dict) else 25.0
                    self.evaluate_logic()
                except Exception as e: 
                    logger.warning(f"Sensor Parse Error: {e} | Payload: {payload}")
                    return
            if topic == f"{self.room_id}/schedule/update":
                self.pull_schedule()
                return
            if topic == f"{self.room_id}/camera/occupancy":
                try:
                    d = json.loads(payload)
                    occ = d if isinstance(d, int) else (d.get("occupancy_count", d.get("count", d.get("people", 0))) if isinstance(d, dict) else 0)
                    self.current_occupancy = max(0, occ)
                    self.last_occupancy_update = time.time()
                    self.evaluate_logic()
                except Exception as e: 
                    logger.warning(f"[CAM] Parse Error: {e} | Payload: {payload}")
                    return

    def on_log(self, client, userdata, level, buf):
        if "retrying" in buf.lower() or "reconnect" in buf.lower() or "connection refused" in buf.lower() or "socket error" in buf.lower():
            logger.warning(f"MQTT Internal: {buf}")

    def on_connect(self, client, userdata, flags, rc):
        logger.info(f"Subscribed to controller topics for room {self.room_id}")
        self.client.subscribe([
            ("system/weather", 0),
            (f"{self.room_id}/request_state", 1),
            (f"{self.room_id}/config/threshold", 1),
            (f"{self.room_id}/ac/control", 1),
            (f"{self.room_id}/ac/precool", 1),
            (f"{self.room_id}/ventilation/suggest", 1),
            (f"{self.room_id}/lamp/front/control", 1),
            (f"{self.room_id}/lamp/back/control", 1),
            (f"{self.room_id}/sensors", 1),
            (f"{self.room_id}/schedule/update", 1),
            (f"{self.room_id}/camera/occupancy", 1)
        ])

    def shutdown(self):
        logger.info("Stopping controller module...")
        if self.sensor_connector:
            try:
                self.sensor_connector.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down sensor connector: {e}")
        super().shutdown()

    def pull_schedule(self):
        """Resolves dashboard REST URL from Catalog and fetches the daily schedule for this room."""
        logger.info("Fetching daily schedule from Catalog/UI...")
        resolved_ui_url = None
        for attempt in range(5):
            try:
                # Query Catalog for dashboard-ui service
                r = requests.get(f"{self.catalog_url}/catalog/services/dashboard-ui", timeout=2)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "success" and "service" in data:
                        resolved_broker = data["service"].get("ip")
                        if resolved_broker:
                            # If internal docker container, hostname, or bridge network IP
                            if resolved_broker in ("dashboard-ui", "localhost", "127.0.0.1") or resolved_broker.startswith("172.") or resolved_broker.startswith("10.") or resolved_broker.startswith("192.168."):
                                from urllib.parse import urlparse
                                parsed = urlparse(self.catalog_url)
                                resolved_ui_url = f"http://{parsed.hostname}:5000"
                            else:
                                if not resolved_broker.startswith("http"):
                                    resolved_ui_url = f"http://{resolved_broker}:5000"
                                else:
                                    resolved_ui_url = resolved_broker
                            break
            except Exception as e:
                logger.debug(f"Catalog lookup for dashboard-ui failed (attempt {attempt+1}): {e}")
            time.sleep(1)

        if not resolved_ui_url:
            from urllib.parse import urlparse
            parsed = urlparse(self.catalog_url)
            resolved_ui_url = f"http://{parsed.hostname or 'localhost'}:5000"
            logger.warning(f"Could not resolve dashboard from Catalog. Using fallback: {resolved_ui_url}")

        for attempt in range(3):
            try:
                r = requests.get(f"{resolved_ui_url}/api/schedules/today", params={"room_id": self.room_id}, timeout=4)
                if r.status_code == 200:
                    data = r.json()
                    with self.state_lock:
                        self.thermal_loss_rate = float(data.get("thermal_loss", 0.5))
                        self.local_schedule = data.get("schedules") or []
                    logger.info(f"✅ Successfully cached schedule for {self.room_id}: {self.local_schedule} (Thermal loss: {self.thermal_loss_rate})")
                    return
            except Exception as e:
                logger.warning(f"Failed to pull schedule from {resolved_ui_url} (attempt {attempt+1}): {e}")
            time.sleep(2)

    def schedule_sync_loop(self):
        """Periodically syncs schedule at Midnight, 8 AM, and Noon."""
        while self.running:
            now = time.localtime()
            if (now.tm_hour == 0 and now.tm_min == 0) or \
               (now.tm_hour == 8 and now.tm_min == 0) or \
               (now.tm_hour == 12 and now.tm_min == 0):
                try:
                    self.pull_schedule()
                except Exception as e:
                    logger.error(f"Error in scheduled schedule pull: {e}")
                time.sleep(60)
            time.sleep(10)

    def start(self, sensor_mode=None):
        if sensor_mode:
            self.sensor_mode = str(sensor_mode).strip().lower()

        if self.sensor_mode in {"real", "hardware", "false", "0"}:
            from real_device_connector import RealDeviceConnector
            logger.info(f"ControlModule selecting REAL hardware sensor connector for {self.room_id}")
            self.sensor_connector = RealDeviceConnector(self.config)
        elif self.sensor_mode in {"fake", "mock", "simulated", "true", "1"}:
            from fake_device_connector import FakeDeviceConnector
            logger.info(f"ControlModule selecting FAKE simulated sensor connector for {self.room_id}")
            self.sensor_connector = FakeDeviceConnector(self.config)

        if self.sensor_connector:
            threading.Thread(target=self.sensor_connector.start, daemon=True).start()

        self.start_catalog_thread({
            "id": f"edge-controller-{self.room_id}",
            "name": f"Room Controller ({self.room_id})",
            "type": "device",
            "hardware": ["climate_controller", "lamp_controller", "vent_controller"],
            "topics": [f"{self.room_id}/state/ac", f"{self.room_id}/state/ventilation", f"{self.room_id}/state/lamp/+"]
        })

        self.connect_mqtt()

        # Fetch initial schedule and start scheduled updates
        self.pull_schedule()
        threading.Thread(target=self.schedule_sync_loop, daemon=True).start()

        while self.running:
            self.evaluate_logic()
            time_empty = time.time() - self.last_motion_time
            if self.current_motion == 1 or time_empty < 10:
                time.sleep(self.eval_fast)
            elif time_empty < 60:
                time.sleep(self.eval_medium)
            else:
                time.sleep(self.eval_slow)

if __name__ == "__main__":
    sensor_mode_arg = None
    for arg in sys.argv[1:]:
        if arg.startswith("--sensor-mode="):
            sensor_mode_arg = arg.split("=", 1)[1]
        elif arg == "--real":
            sensor_mode_arg = "real"
        elif arg == "--fake":
            sensor_mode_arg = "fake"

    module = ControlModule()
    module.setup_signal_handlers()
    module.start(sensor_mode=sensor_mode_arg)