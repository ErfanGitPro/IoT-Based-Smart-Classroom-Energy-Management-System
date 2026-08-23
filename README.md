# IoT-Based Smart Classroom Energy Management System

An intelligent smart-building platform designed to optimize classroom energy consumption through IoT automation, AI-powered occupancy detection, predictive HVAC control, weather-aware ventilation, and automated course-room matching.

The system combines edge computing devices, real-time sensors, MQTT communication, machine learning, and a centralized management server to reduce energy waste while maintaining occupant comfort.

---

## 🏷️ Badges

![Python](https://img.shields.io/badge/Python-3.9+-blue)
![Flask](https://img.shields.io/badge/Flask-3.0-green)
![Docker](https://img.shields.io/badge/Docker-Containerized-blue)
![MQTT](https://img.shields.io/badge/MQTT-Mosquitto-orange)
![SQLite](https://img.shields.io/badge/Database-SQLite-lightgrey)
![YOLOv8](https://img.shields.io/badge/AI-YOLOv8-red)
![Raspberry Pi](https://img.shields.io/badge/Platform-Raspberry%20Pi-darkgreen)

---

## 🏗️ System Architecture

The project follows a microservices architecture coordinated via **MQTT** and **SQLite** databases:

```mermaid
graph TD
    subgraph Central Server
        UI[Flask Web Dashboard - Port 5000]
        Catalog[CherryPy Resource Catalog - Port 9090]
        DB[SQLite Database Adaptor]
        Stats[Statistics Service]
        Telegram[Telegram Bot REST Webhook - Port 5004]
        ThingSpeak[ThingSpeak Cloud Adaptor]
    end

    subgraph Message Broker
        MQTT[Eclipse Mosquitto Broker]
    end

    subgraph Edge Devices (Classrooms)
        Connector[Device Connector: PIR & Temp]
        YOLO[YOLOv8 Occupancy Camera]
        EdgeCtrl[Edge Room Controller]
    end

    %% Communication Flows
    Connector -->|Publish Telemetry| MQTT
    YOLO -->|Publish Occupancy| MQTT
    EdgeCtrl -->|Subscribe Control / Publish State| MQTT
    MQTT <--> Central Server
    
    %% Webhooks & API
    Stats -->|HTTP POST Alerts| Telegram
    Telegram -->|Push Notifications| UserTelegram[Telegram User Client]
    ThingSpeak -->|POST Telemetry| Cloud[ThingSpeak Cloud Analytics]
    
    %% Service Discovery & Schedules
    Connector -->|HTTP Register| Catalog
    EdgeCtrl -->|HTTP Register| Catalog
    EdgeCtrl -.->|HTTP GET Schedules| UI
```

### 🧩 Key Components

#### Central Server Services
* **Flask UI Dashboard (`port 5000`)**: Interactive dashboard showing live room telemetry, status/heartbeats, manual overrides, course schedules, and analytical charts.
* **Resource Catalog (`catalog_service.py` - CherryPy `port 9090`)**: REST API endpoint for device/service registration, heartbeats, and dynamic service discovery.
* **SQLite Database Adaptor (`db_adaptor.py`)**: Subscribes to telemetry, logs, and Last Will events and writes them to the databases.
* **Statistics Service (`statistics_service.py`)**: Audits energy efficiency using temperature standard deviation and sends webhook alerts to the Telegram bot if anomalies are found.
* **Telegram Dashboard (`telegram_dashboard.py`)**: Acts as a chatbot interface and hosts a Flask webhook server (`port 5004`) for event-driven alerts.
* **ThingSpeak Adaptor (`thingspeak_adaptor.py`)**: Connects local data to the ThingSpeak Cloud for remote monitoring.

#### Edge Devices
* **Device Connector (`device_connector.py`)**: Reads temperature and motion sensors (supporting physical GPIO sensors or fallback simulated connector modes).
* **Camera Module (`camera_module.py`)**: Counts room occupants using a YOLOv8 object detection model.
* **Edge Room Controller (`control_module.py`)**: Runs local decision loops based on predictive schedules, safety policies, and manual overrides.

---

## ✨ Features

### Smart Monitoring
* **Real-time Telemetry**: Continuously tracks indoor temperature and physical motion.
* **AI Occupancy Detection**: YOLOv8-based people counter dynamically adjusts room states.
* **Last Will Protection**: MQTT Last Will and Testament (LWT) registers instant disconnect alerts.
* **Dynamic Heartbeats**: 45-second service timeouts inside the CherryPy Catalog.

### Intelligent Energy Optimization
* **Edge-side Pre-cooling**: Edge controllers parse schedule times from the central server and calculate dynamic pre-cooling runtimes before classes start using:
  $$\text{duration} = \min(60, \max(5, \text{temp\_diff} \times 3.0 \times (1.0 + \text{thermal\_loss})))$$
* **Weather-Aware Ventilation**: Suggests free-cooling dampers activation if outdoor weather is optimal.
* **Duty-Cycle Protection**: Restricts excessive toggling of HVAC compressors to extend service life.
* **Retained States**: Publishes crucial control topics with MQTT `retain=True` to recover actuator statuses instantly.

### Automated Classroom Assignment
* **Constraint-Aware Matching**: Evaluates physical equipment, capacity, and student count.
* **Score Allocation**: Rates classrooms dynamically:
  $$\text{Score} = \text{Avg\_Efficiency} - (\text{Capacity} - \text{Students}) \times 0.1 - \text{Thermal\_Loss} \times 15 - \frac{\text{Overrides}}{5}$$

### Centralized Remote Control
* **Web Dashboard**: Modern view transition tabs tracking schedules, efficiency, and analytics charts.
* **Telegram Bot Integration**: Multi-user registration with webhook alerts pushing instant notifications on efficiency drops.
* **ThingSpeak Cloud Export**: Forwards key analytics parameters to cloud feeds.

---

## 🛠️ Technology Stack

| Category             | Technology                    | Description                                  |
| -------------------- | ----------------------------- | -------------------------------------------- |
| **Backend**          | Python 3.9+                  | Core programming language                   |
| **Web UI**           | Flask                         | Web application framework for dashboard     |
| **Service Catalog**  | CherryPy                      | REST catalog service                        |
| **Messaging**        | MQTT (Eclipse Mosquitto)      | Real-time publish/subscribe communication    |
| **Database**         | SQLite                        | Local database engine                       |
| **AI Vision**        | YOLOv8, OpenCV                | AI-driven occupancy detection               |
| **Frontend**         | HTML, Bootstrap 5, Chart.js   | UI template styling and analytics graph      |
| **Cloud Analytics**  | ThingSpeak API                | Cloud database dashboard export             |
| **Notifications**    | Telegram Bot API              | Alerts and interactive console interface    |
| **Weather**          | OpenWeatherMap API            | Dynamic outdoor metrics                     |
| **Containerization** | Docker, Docker Compose        | Microservices lifecycle orchestration        |
| **Hardware**         | Raspberry Pi 4/5 (Optional)   | Target platform for edge nodes               |

---

## 📂 Project Organization

```text
├── Central/
│   ├── UI/                      # Flask dashboard frontend & templates
│   │   ├── app.py
│   │   └── templates/           # HTML Pages (dashboard, charts, schedules, control)
│   ├── base_service.py          # Central base class implementing Catalog/MQTT hooks
│   ├── catalog_service.py       # REST Catalog Discovery service
│   ├── config.py                # Environment configuration parser
│   ├── db_adaptor.py            # SQLite listener and writer
│   ├── docker_deploy/           # Central docker-compose environment
│   │   ├── docker-compose.yml
│   │   ├── mosquitto.conf
│   │   └── requirements.txt
│   ├── room_selector.py         # Room allocation heuristics matcher
│   ├── statistics_service.py    # Audits efficiency deviations
│   ├── telegram_dashboard.py    # Telegram alerts webhook service
│   ├── thermal_modeler.py       # Physics formulas and models
│   └── thingspeak_adaptor.py    # ThingSpeak interface
│
└── Edge/
	├── base_service.py          # Edge base class with registration logic
	├── camera_module.py         # YOLOv8 occupant tracking
	├── config.py                # Edge environment parameter classes
	├── control_module.py        # Local Room Actuator Controller
	├── device_connector.py      # Sensor driver/simulator
	├── docker_deploy/           # Edge docker-compose environment
	│   ├── docker-compose.yml
	│   └── .env.room[1-5]       # Environment settings for multiple simulated rooms
	├── fake_device_connector.py # Physics simulation fallback
	└── real_device_connector.py # Physical GPIO interface bindings

```

---

## ⚙️ Configuration (.env)

The environment parameters are set inside `.env` or room-specific `.env` configuration files:

```env
# Broker settings
MQTT_BROKER_HOST=127.0.0.1
CATALOG_URL=http://localhost:9090

# Room Information
ROOM_ID=classroom001

# Camera and AI features
HAS_CAMERA=true
CAM_MODE=fake
CAMERA_ACTIVE_SECONDS=30
CAMERA_SLEEP_SECONDS=60

# Temperature thresholds & schedules
DEFAULT_AC_PRECOOL_TEMP=21
THRESHOLD_BASE=24.0
HOLDUP_BAND=1.5
MANUAL_MODE_HOLD_SECONDS=60
```

---

## 🚀 Running the Project

### Prerequisites
Ensure you have Docker and Docker Compose installed:
```bash
docker --version
docker compose version
```

### 1. Central Services
To spin up the centralized broker, Catalog service, DB adaptor, and dashboard UI:
```bash
cd Central/docker_deploy
docker compose up -d --build
```
Open [http://localhost:5000](http://localhost:5000) in your browser to view the Dashboard.

### 2. Edge Nodes (Rooms)
To boot edge simulators for different classrooms:
```bash
cd Edge/docker_deploy
# Launch classroom 1
docker compose --env-file .env.room1 -p edge_room1 up -d --build
# Launch classroom 2
docker compose --env-file .env.room2 -p edge_room2 up -d --build
```

---

## 🛜 MQTT Topic Specification

### 1. Room Status & Discovery
* **Topic**: `{room_id}/status`
* **QoS**: 1 (Retained)
* **Payload**: `{"status": "ONLINE"}` (LWT triggers `{"status": "OFFLINE"}` automatically upon crash).

### 2. Telemetry Data
* **Topic**: `{room_id}/sensors`
* **Payload**:
  ```json
  {
    "motion": 1,
    "temperature": 24.5
  }
  ```

### 3. Camera Occupancy
* **Topic**: `{room_id}/camera/occupancy`
* **Payload**:
  ```json
  {
    "occupancy_count": 12
  }
  ```

### 4. Actuator Control & State (QoS 1, Retained)
* **Topics**: `{room_id}/ac/state`, `{room_id}/lamp/+/state`, `{room_id}/ventilation/state`
* **Control Commands**: `ON`, `OFF`, `LOW`, `MEDIUM`, `HIGH` on `{room_id}/ac/control`
* **Pre-cooling Command**:
  ```json
  {
    "target_temp": 21,
    "duration_minutes": 15,
    "source": "schedule"
  }
  ```

---

## 💻 Running on Windows (Docker Desktop)

If you are running the project on **Windows with Docker Desktop**:

1. **Remove Host Networking**: In `Central/docker_deploy/docker-compose.yml` and `Edge/docker_deploy/docker-compose.yml`, remove or comment out `network_mode: "host"`.
2. **Publish Ports**: Expose individual ports explicitly (e.g. Dashboard at `5000:5000`, Catalog at `9090:9090`, MQTT Broker at `1883:1883`, Telegram bot port if necessary).
3. **Disable RPi Hardware Drivers**: Remove the device binds mappings:
   ```yaml
   devices:
     - "/dev/video0:/dev/video0"
     - "/dev/gpiomem:/dev/gpiomem"
   ```
4. **Mock Hardware Settings**: Ensure `SENSOR_MODE=fake` and `CAM_MODE=fake` are set in your environment file.

---

## 💾 Database Schema

The database adaptor interacts with `classroom_data.db` containing:
* **`sensor_history`**: Tracks temperature, motion, occupant count, and active states.
* **`classroom_metadata`**: Holds capacity records, thermal coefficients, and hardware settings.
* **`course_schedule`**: Course timetables and classroom allocations.
* **`control_logs`**: Logs manual command overrides and automated runs.
* **`efficiency_history`**: Holds calculated energy performance metrics.
* **`weather_history`**: Log history of local external conditions.

---

## 👥 Authors
This project was developed by:
* 👤 **Seyed Erfan Ghoreishi**
* 👤 **Ehsan Nikpey**
* 👤 **Alireza Nourishad**
* 👤 **Shabnam Amouie**

---

## 📜 License
This project was developed for educational and research purposes.

---

## 💖 Acknowledgments
Special thanks to:
* **Flask, CherryPy & Bootstrap**
* **Eclipse Mosquitto & Paho MQTT**
* **YOLOv8 & OpenCV**
* **OpenWeatherMap & ThingSpeak APIs**
* **Raspberry Pi Foundation**
