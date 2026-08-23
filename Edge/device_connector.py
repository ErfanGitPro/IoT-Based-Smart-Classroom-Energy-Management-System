import sys
from config import EdgeConfig
from real_device_connector import RealDeviceConnector
from fake_device_connector import FakeDeviceConnector

class DeviceConnector:
    def __init__(self, config: EdgeConfig = None):
        self.config = config or EdgeConfig()
        if self.config.sensor_mode == "fake":
            self.impl = FakeDeviceConnector(self.config)
        elif self.config.sensor_mode == "real":
            self.impl = RealDeviceConnector(self.config)
        else:
            raise ValueError(f"Unknown sensor mode: {self.config.sensor_mode}")

    def start(self):
        self.impl.setup_signal_handlers()
        self.impl.start()

    def shutdown(self):
        self.impl.shutdown()


if __name__ == "__main__":
    connector = DeviceConnector()
    connector.start()
