import os
import logging
from datetime import datetime
import threading
import time
import json
import requests
import telebot
from flask import Flask, request, jsonify
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger("TelegramDashboard")

from config import CentralConfig
config = CentralConfig()
TOKEN = config.telegram_token
UI_BASE_URL = config.ui_base_url
CATALOG_URL = config.catalog_url

bot = telebot.TeleBot(TOKEN) if TOKEN else None
app = Flask(__name__)

# Multi-user chat registration
registered_chat_ids = set()
if config.telegram_chat_id:
    registered_chat_ids.add(str(config.telegram_chat_id))

muted_rooms = {}  # Maps room_id -> expiration_timestamp
daily_report_time = "08:00"  # Default report time


def _api_get(path, params=None, timeout=6):
    try:
        res = requests.get(f"{UI_BASE_URL}{path}", params=params, timeout=timeout)
        if res.ok:
            return res.json()
    except Exception:
        return None
    return None


def _api_post_form(path, data, timeout=8):
    try:
        res = requests.post(f"{UI_BASE_URL}{path}", data=data, timeout=timeout)
        payload = {}
        try:
            payload = res.json()
        except Exception:
            payload = {}
        return res.status_code, payload
    except Exception as e:
        return 503, {"error": str(e)}


def _api_post_json(path, data, timeout=8):
    try:
        res = requests.post(f"{UI_BASE_URL}{path}", json=data, timeout=timeout)
        payload = {}
        try:
            payload = res.json()
        except Exception:
            payload = {}
        return res.status_code, payload
    except Exception as e:
        return 503, {"error": str(e)}


def _broadcast_message(text):
    if not bot or not registered_chat_ids:
        return False
    success = False
    for chat_id in list(registered_chat_ids):
        try:
            bot.send_message(chat_id, text)
            success = True
        except Exception:
            pass
    return success


def _parse_timestamp(raw):
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    value = value.replace("T", " ")
    value = value.split(".")[0]
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _seconds_since(raw):
    ts = _parse_timestamp(raw)
    if not ts:
        return None
    return max(0, int((datetime.utcnow() - ts).total_seconds()))


def _load_classrooms_map():
    data = _api_get("/api/classrooms") or {}
    rooms = data.get("classrooms", []) if isinstance(data, dict) else []
    return {(r.get("room_id") or "").strip(): r for r in rooms if (r.get("room_id") or "").strip()}


def _load_discovery_map():
    data = _api_get("/api/scan_devices")
    result = {}
    if not isinstance(data, list):
        return result
    for item in data:
        rid = (item.get("room_id") or "").strip()
        if not rid:
            continue
        types = item.get("types") or []
        result[rid] = {
            "types": [str(t).lower() for t in types],
            "motion_sensor": item.get("motion_sensor") or None,
            "temp_sensor": item.get("temp_sensor") or None,
        }
    return result


def get_main_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(KeyboardButton("🏫 /rooms"), KeyboardButton("🖥️ /topology"))
    markup.row(KeyboardButton("🔔 /alerts"), KeyboardButton("📊 /report"))
    return markup


def get_room_control_markup(room_id):
    markup = InlineKeyboardMarkup()
    health = _api_get("/api/system_health", params={"room_id": room_id}) or {}
    state = _api_get("/api/latest_state", params={"room_id": room_id}) or {}
    age_secs = _seconds_since(state.get("timestamp"))
    fresh_limit = int(health.get("fresh_data_max_age_seconds") or 30)
    sensors_fresh = age_secs is not None and age_secs <= fresh_limit
    edge_online = bool(health.get("edge_mqtt_connected"))
    is_online = sensors_fresh or edge_online

    btn_refresh = InlineKeyboardButton("🔄 Refresh Status", callback_data=f"refresh_{room_id}")
    
    if not is_online:
        markup.row(btn_refresh)
        return markup

    btn_ac = InlineKeyboardButton("❄️ Toggle AC", callback_data=f"ctrl_{room_id}:ac:toggle")
    btn_vent = InlineKeyboardButton("🌬️ Toggle Vent", callback_data=f"ctrl_{room_id}:vent:toggle")
    btn_front = InlineKeyboardButton("💡 Toggle Front Lamp", callback_data=f"ctrl_{room_id}:lamp_front:toggle")
    btn_back = InlineKeyboardButton("💡 Toggle Back Lamp", callback_data=f"ctrl_{room_id}:lamp_back:toggle")
    
    markup.row(btn_ac, btn_vent)
    markup.row(btn_front, btn_back)
    markup.row(btn_refresh)
    return markup


def _format_room_status(room_id):
    state = _api_get("/api/latest_state", params={"room_id": room_id}) or {}
    health = _api_get("/api/system_health", params={"room_id": room_id}) or {}
    discovery = _load_discovery_map().get(room_id, {})
    room_meta = _load_classrooms_map().get(room_id, {})

    age_secs = _seconds_since(state.get("timestamp"))
    fresh_limit = int(health.get("fresh_data_max_age_seconds") or 30)
    sensors_fresh = age_secs is not None and age_secs <= fresh_limit

    ui_online = bool(health.get("ui_mqtt_connected"))
    edge_online = bool(health.get("edge_mqtt_connected"))
    edge_online_effective = sensors_fresh or edge_online

    temp_state = discovery.get("temp_sensor") or ("fallback" if sensors_fresh else "offline")
    motion_state = discovery.get("motion_sensor") or ("fallback" if sensors_fresh else "offline")

    has_camera = int(room_meta.get("has_camera") or 0) == 1
    camera_discovered = "camera" in (discovery.get("types") or [])
    camera_state = "online" if camera_discovered else ("not available" if not has_camera else "offline")

    if not edge_online_effective:
        return (
            f"📍 Room: {room_id}\n"
            f"🌡️ Temp: --°C | 👥 Occ: -- | ⚙️ Mode: Offline\n"
            f"❄️ AC: OFF | 💡 Lamps: Front=OFF, Back=OFF | 🌬️ Vent: CLOSED\n"
            f"📡 Edge: ❌ Offline | ☁️ MQTT: {'✅ Online' if ui_online else '❌ Offline'}\n"
            f"🛠️ Sensors: [Temp: offline] [Motion: offline] [Cam: {camera_state}]"
        )

    ac_raw = state.get("ac_state") or "OFF"
    try:
        ac_data = json.loads(ac_raw) if isinstance(ac_raw, str) and ac_raw.startswith('{') else ac_raw
        if not isinstance(ac_data, dict):
            ac_data = {"status": str(ac_raw)}
    except Exception:
        ac_data = {"status": str(ac_raw)}
    
    ac_status = str(ac_data.get("status", "OFF")).upper()
    ac_reason = str(ac_data.get("reason", "")).replace("_", " ").title()
    ac_display = f"{ac_status}" + (f" ({ac_reason})" if ac_reason else "")
    
    vent_state = str(state.get("vent_state") or "CLOSE").upper()
    system_mode = str(state.get("system_mode") or "predictive").replace("_", " ").title()
    
    occupancy = max(0, int(state.get("occupancy_count") or 0))
    motion = int(state.get("motion") or 0)
    if occupancy == 0 and motion == 1:
        occupancy = 1

    lamp_front = state.get('lamp_front_state') or state.get('lamp_state') or 'OFF'
    lamp_back = state.get('lamp_back_state') or state.get('lamp_state') or 'OFF'

    return (
        f"📍 Room: {room_id}\n"
        f"🌡️ Temp: {state.get('temperature')}°C | 👥 Occ: {occupancy} | ⚙️ Mode: {system_mode}\n"
        f"❄️ AC: {ac_display} | 💡 Lamps: Front={lamp_front}, Back={lamp_back} | 🌬️ Vent: {vent_state}\n"
        f"📡 Edge: ✅ Online | ☁️ MQTT: {'✅ Online' if ui_online else '❌ Offline'}\n"
        f"🛠️ Sensors: [Temp: {temp_state}] [Motion: {motion_state}] [Cam: {camera_state}]"
    )


def _format_alert(alert):
    severity = str(alert.get("severity") or "warning").upper()
    room_id = str(alert.get("room_id") or "-")
    msg = str(alert.get("message") or "Alert")
    ts = str(alert.get("timestamp") or "")
    icon = "🚨" if severity == "DANGER" else "⚠️"
    suffix = f" ({ts})" if ts else ""
    return f"{icon} [{severity}] {room_id}: {msg}{suffix}"


@app.route('/api/alert', methods=['POST'])
def receive_internal_alert():
    data = request.get_json(silent=True) or {}
    msg = data.get("message")
    if not msg:
        return jsonify({"error": "No message provided"}), 400
    
    success = _broadcast_message(f"🚨 PUSH ALERT 🚨\n{msg}")
    if success:
        return jsonify({"status": "Alert sent"}), 200
    else:
        return jsonify({"error": "Failed to send alert (no chat IDs registered)"}), 503


def _generate_report_text():
    data = _api_get("/api/report")
    if data and isinstance(data, dict) and "report_text" in data:
        return data["report_text"]
    return "📊 *System Report*\n\n⚠️ Unable to fetch report from UI service right now."


def _register_with_catalog():
    while True:
        try:
            requests.post(f"{CATALOG_URL}/catalog/register", json={
                "id": "telegram-bot-service",
                "name": "Telegram Bot Interface",
                "type": "service",
                "hardware": ["telegram_bot", "rest_webhook"],
                "topics": ["/api/alert"]
            }, timeout=4)
        except Exception:
            pass
        time.sleep(30)


if bot:
    @bot.message_handler(commands=['start'])
    def handle_start(message):
        chat_id_str = str(message.chat.id)
        registered_chat_ids.add(chat_id_str)
        markup = get_main_keyboard()
        bot.reply_to(
            message,
            "✅ **Telegram alert service registered!**\n"
            "You will now receive real-time push alerts and reports.\n"
            "Use /help for available commands.",
            reply_markup=markup
        )

    @bot.message_handler(commands=['help'])
    def handle_help(message):
        bot.reply_to(
            message,
            "📋 **Available Commands:**\n\n"
            "🚀 /start - Register alert service & show bottom menu buttons\n"
            "🏫 /rooms - List all configured classrooms and status\n"
            "🔍 /status <room_id> - View live telemetry & health\n"
            "🖥️ /topology - View system network topology & health\n"
            "🎮 /control <room_id> <device> <action> - Send manual command\n\n"
            "**Supported Devices:**\n"
            "❄️ `ac`, 🌬️ `ventilation`, 💡 `lamp_front`, 💡 `lamp_back`\n\n"
            "**Supported Actions:**\n"
            "🔹 AC: `ON`, `OFF`, `LOW`, `MEDIUM`, `HIGH`, `PRECOOL`\n"
            "🔹 Vent: `OPEN`, `CLOSE`\n"
            "🔹 Lamp: `ON`, `OFF`\n\n"
            "🔔 /alerts - Show current system alerts\n"
            "🔇 /mute <room_id> [mins] - Silence a room\n"
            "🔊 /unmute <room_id> - Restore room alerts\n"
            "📊 /report - Generate an instant system summary\n"
            "⚙️ /settings report_time <HH:MM> - Set daily report time"
        )

    @bot.message_handler(commands=['rooms'])
    def handle_rooms(message):
        rooms_map = _load_classrooms_map()
        rooms = sorted([rid for rid in rooms_map.keys() if rid])
        if not rooms:
            bot.reply_to(message, "No classrooms configured.")
            return
        
        lines = ["🏫 **Classrooms:**"]
        markup = InlineKeyboardMarkup()
        for rid in rooms:
            state = _api_get("/api/latest_state", params={"room_id": rid}) or {}
            health = _api_get("/api/system_health", params={"room_id": rid}) or {}
            age_secs = _seconds_since(state.get("timestamp"))
            fresh_limit = int(health.get("fresh_data_max_age_seconds") or 30)
            sensors_fresh = age_secs is not None and age_secs <= fresh_limit
            edge_online = bool(health.get("edge_mqtt_connected"))
            is_online = sensors_fresh or edge_online
            status_str = "🟢 Online" if is_online else "🔴 Offline"
            lines.append(f"- {rid}: {status_str}")
            
            markup.add(InlineKeyboardButton(f"🚪 {rid} ({'Online' if is_online else 'Offline'})", callback_data=f"room_{rid}"))
            
        bot.reply_to(message, "\n".join(lines), reply_markup=markup)

    @bot.message_handler(commands=['status'])
    def handle_status(message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            bot.reply_to(message, "⌨️ Usage: /status <room_id>")
            return
        room_id = parts[1].strip()
        markup = get_room_control_markup(room_id)
        bot.reply_to(message, _format_room_status(room_id), reply_markup=markup)

    @bot.message_handler(commands=['topology'])
    def handle_topology(message):
        health = _api_get("/api/system_health") or {}
        devices = _api_get("/api/scan_devices") or []
        
        lines = ["🖥️ **System Topology & Health**\n"]
        lines.append("⚙️ **Microservices:**")
        lines.append("- Catalog Service: ✅ ONLINE")
        
        mqtt_status = "✅ ONLINE" if health.get("ui_mqtt_connected") else "❌ OFFLINE"
        lines.append(f"- MQTT Broker: {mqtt_status}")
        lines.append("- Web UI Server: ✅ ONLINE")
        
        edge_net = "✅ ONLINE" if health.get("edge_mqtt_connected") else "❌ OFFLINE"
        lines.append(f"- Edge Data Link: {edge_net}")
        
        lines.append("\n🚪 **Classroom Controllers & Devices:**")
        if not devices:
            lines.append("No active edge devices registered in Catalog.")
        else:
            for d in devices:
                rid = d.get("room_id", "Unknown")
                status = str(d.get("status", "offline")).upper()
                status_icon = "✅" if status == "ONLINE" else "❌"
                dtypes = ", ".join(d.get("types", []))
                lines.append(f"- Room {rid}: {status_icon} {status} ({dtypes})")
                
        bot.reply_to(message, "\n".join(lines))

    @bot.callback_query_handler(func=lambda call: call.data.startswith('room_'))
    def handle_room_select_callback(call):
        room_id = call.data.split('_', 1)[1]
        status_text = _format_room_status(room_id)
        markup = get_room_control_markup(room_id)
        bot.send_message(call.message.chat.id, status_text, reply_markup=markup)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith('refresh_'))
    def handle_refresh_callback(call):
        room_id = call.data.split('_', 1)[1]
        new_text = _format_room_status(room_id)
        markup = get_room_control_markup(room_id)
        try:
            bot.edit_message_text(new_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id, "🔄 Status refreshed!")
        except Exception:
            bot.answer_callback_query(call.id, "Status is already up to date.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith('ctrl_'))
    def handle_control_callback(call):
        data_part = call.data[5:]  # strip 'ctrl_'
        parts = data_part.split(':')
        if len(parts) < 3:
            return
        room_id = parts[0]
        device = parts[1]
        action = parts[2]
        
        state = _api_get("/api/latest_state", params={"room_id": room_id}) or {}
        
        target_action = None
        if device == 'ac':
            ac_raw = state.get("ac_state") or "OFF"
            try:
                ac_data = json.loads(ac_raw) if isinstance(ac_raw, str) and ac_raw.startswith('{') else ac_raw
                if not isinstance(ac_data, dict): ac_data = {"status": str(ac_raw)}
            except Exception: ac_data = {"status": str(ac_raw)}
            status = str(ac_data.get("status", "OFF")).upper()
            target_action = "OFF" if status in {"ON", "PRECOOL"} else "ON"
        elif device == 'vent':
            vent_state = str(state.get("vent_state") or "CLOSE").upper()
            target_action = "CLOSE" if vent_state in {"OPEN", "ON"} else "OPEN"
            device = 'ventilation'
        elif device == 'lamp_front':
            lamp_val = str(state.get("lamp_front_state") or state.get("lamp_state") or "OFF").upper()
            target_action = "OFF" if lamp_val == "ON" else "ON"
            device = 'lamp_front'
        elif device == 'lamp_back':
            lamp_val = str(state.get("lamp_back_state") or state.get("lamp_state") or "OFF").upper()
            target_action = "OFF" if lamp_val == "ON" else "ON"
            device = 'lamp_back'
            
        if not target_action:
            bot.answer_callback_query(call.id, "❌ Unable to determine current state.")
            return
            
        status_code, payload = _api_post_form(
            "/api/control",
            {"room_id": room_id, "device": device, "action": target_action}
        )
        if status_code == 200:
            bot.answer_callback_query(call.id, f"✅ Command sent: {device} -> {target_action}")
            
            # Poll for up to 5 seconds to wait for state change
            for _ in range(10):
                time.sleep(0.5)
                state_check = _api_get("/api/latest_state", params={"room_id": room_id}) or {}
                
                # Check if target action has been applied
                if device == 'ac':
                    ac_raw = state_check.get("ac_state") or "OFF"
                    try:
                        ac_data = json.loads(ac_raw) if isinstance(ac_raw, str) and ac_raw.startswith('{') else ac_raw
                        if not isinstance(ac_data, dict): ac_data = {"status": str(ac_raw)}
                    except Exception: ac_data = {"status": str(ac_raw)}
                    current_ac = str(ac_data.get("status", "OFF")).upper()
                    if current_ac == target_action: break
                elif device == 'ventilation':
                    vent_state = str(state_check.get("vent_state") or "CLOSE").upper()
                    target_matched = (target_action == 'CLOSE' and vent_state == 'CLOSED') or (target_action == 'OPEN' and vent_state == 'OPEN')
                    if target_matched: break
                elif device == 'lamp_front':
                    lamp_val = str(state_check.get("lamp_front_state") or state_check.get("lamp_state") or "OFF").upper()
                    if lamp_val == target_action: break
                elif device == 'lamp_back':
                    lamp_val = str(state_check.get("lamp_back_state") or state_check.get("lamp_state") or "OFF").upper()
                    if lamp_val == target_action: break
            
            try:
                new_text = _format_room_status(room_id)
                markup = get_room_control_markup(room_id)
                bot.edit_message_text(new_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
            except Exception:
                pass
        else:
            err = payload.get("error") if isinstance(payload, dict) else "Unknown error"
            bot.answer_callback_query(call.id, f"❌ Failed: {err}", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data in ['cmd_rooms', 'cmd_topology', 'cmd_alerts', 'cmd_report'])
    def handle_cmd_callbacks(call):
        cmd = call.data
        if cmd == 'cmd_rooms':
            handle_rooms(call.message)
        elif cmd == 'cmd_topology':
            handle_topology(call.message)
        elif cmd == 'cmd_alerts':
            handle_alerts(call.message)
        elif cmd == 'cmd_report':
            handle_report(call.message)
        bot.answer_callback_query(call.id)

    @bot.message_handler(func=lambda msg: msg.text in ["🏫 /rooms", "🏫 Rooms", "🖥️ /topology", "🖥️ Topology", "🔔 /alerts", "🔔 Alerts", "📊 /report", "📊 Report"])
    def handle_text_buttons(message):
        text = message.text
        if "rooms" in text or "Rooms" in text:
            handle_rooms(message)
        elif "topology" in text or "Topology" in text:
            handle_topology(message)
        elif "alerts" in text or "Alerts" in text:
            handle_alerts(message)
        elif "report" in text or "Report" in text:
            handle_report(message)

    @bot.message_handler(commands=['alerts'])
    def handle_alerts(message):
        data = _api_get("/api/alerts") or {}
        alerts = data.get("alerts", []) if isinstance(data, dict) else []
        if not alerts:
            bot.reply_to(message, "✅ No active alerts.")
            return
        lines = [_format_alert(a) for a in alerts[:10]]
        bot.reply_to(message, "🔔 **Current Alerts:**\n" + "\n".join(lines))

    @bot.message_handler(commands=['control'])
    def handle_control(message):
        parts = (message.text or "").split()
        if len(parts) < 4:
            bot.reply_to(message, "⌨️ Usage: /control <room_id> <device> <action>")
            return

        room_id = parts[1].strip()
        device = parts[2].strip().lower().replace("/", "_")
        action = parts[3].strip().upper()

        valid_devices = {"ac", "ventilation", "lamp_front", "lamp_back"}
        if device not in valid_devices:
            bot.reply_to(message, "❌ Invalid device. Use: ac, ventilation, lamp_front, lamp_back")
            return

        status_code, payload = _api_post_form(
            "/api/control",
            {"room_id": room_id, "device": device, "action": action}
        )
        if status_code == 200:
            bot.reply_to(message, f"✅ **Command sent:** {room_id} {device} {action}")
            return

        err = payload.get("error") if isinstance(payload, dict) else "Unknown error"
        bot.reply_to(message, f"❌ **Command failed ({status_code}):** {err}")

    @bot.message_handler(commands=['mute'])
    def handle_mute(message):
        parts = (message.text or "").split()
        if len(parts) < 2:
            bot.reply_to(message, "⌨️ Usage: /mute <room_id> [duration_minutes]\nExample: /mute room001 60")
            return
            
        room_id = parts[1].strip()
        duration_mins = 60
        if len(parts) >= 3:
            try:
                duration_mins = int(parts[2].strip())
            except ValueError:
                bot.reply_to(message, "Duration must be an integer (minutes).")
                return
                
        muted_rooms[room_id] = time.time() + (duration_mins * 60)
        bot.reply_to(message, f"🔇 Alerts for {room_id} are muted for {duration_mins} minutes.")

    @bot.message_handler(commands=['unmute'])
    def handle_unmute(message):
        parts = (message.text or "").split()
        if len(parts) < 2:
            bot.reply_to(message, "⌨️ Usage: /unmute <room_id>")
            return
        room_id = parts[1].strip()
        muted_rooms.pop(room_id, None)
        bot.reply_to(message, f"🔊 Alerts for {room_id} have been unmuted.")

    @bot.message_handler(commands=['settings'])
    def handle_settings(message):
        global daily_report_time
        parts = (message.text or "").split()
        if len(parts) < 3:
            bot.reply_to(message, "⌨️ Usage: /settings report_time HH:MM\nExample: /settings report_time 08:00")
            return
            
        setting_key = parts[1].lower()
        setting_val = parts[2]
        
        if setting_key == "report_time":
            try:
                datetime.strptime(setting_val, "%H:%M")
                daily_report_time = setting_val
                _api_post_json("/api/settings", {"report_time": setting_val})
                bot.reply_to(message, f"✅ Daily report time updated to {daily_report_time}.")
            except ValueError:
                bot.reply_to(message, "⚠️ Invalid time format. Use HH:MM (24-hour format).")
        else:
            bot.reply_to(message, f"⚠️ Unknown setting: {setting_key}")

    @bot.message_handler(commands=['report'])
    def handle_report(message):
        msg = bot.reply_to(message, "⏳ Generating report, please wait...")
        report_text = _generate_report_text()
        bot.edit_message_text(report_text, chat_id=message.chat.id, message_id=msg.message_id, parse_mode="Markdown")

def alert_polling_loop():
    seen_alerts = set()
    while True:
        time.sleep(300)
        if not registered_chat_ids:
            continue
            
        data = _api_get("/api/alerts") or {}
        alerts = data.get("alerts", []) if isinstance(data, dict) else []
        
        for a in reversed(alerts):
            room_id = str(a.get('room_id') or "-")
            sig = f"{room_id}_{a.get('type')}_{a.get('timestamp')}"
            if sig not in seen_alerts:
                seen_alerts.add(sig)
                if len(seen_alerts) > 1000:
                    seen_alerts.clear()
                
                if time.time() < muted_rooms.get(room_id, 0):
                    continue
                
                msg = _format_alert(a)
                _broadcast_message(f"⚠️ AUTOMATIC ALERT ⚠️\n{msg}")


def daily_report_loop():
    global daily_report_time
    last_report_date = None
    
    settings = _api_get("/api/settings") or {}
    if "report_time" in settings:
        daily_report_time = settings["report_time"]
        
    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        if current_time == daily_report_time and now.date() != last_report_date:
            if registered_chat_ids:
                report_text = _generate_report_text()
                _broadcast_message(f"🌅 *Good Morning!*\nHere is your daily {daily_report_time} summary:\n\n{report_text}")
                last_report_date = now.date()
        time.sleep(30)

if __name__ == "__main__":
    if not TOKEN:
        logger.warning("TELEGRAM_TOKEN not set. Bot disabled.")
    else:
        logger.info("Starting Telegram background loops, Catalog registration, and Flask server...")
        threading.Thread(target=alert_polling_loop, daemon=True).start()
        threading.Thread(target=daily_report_loop, daemon=True).start()
        threading.Thread(target=_register_with_catalog, daemon=True).start()
        
        # Start Flask on port 5004 for REST alerts
        threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5004), daemon=True).start()
        
        logger.info("Starting Telegram bot polling...")
        bot.infinity_polling(timeout=30, long_polling_timeout=30)