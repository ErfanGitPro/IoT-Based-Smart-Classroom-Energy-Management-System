import json
import time
import os
import cherrypy

class CatalogService:
    exposed = True

    def __init__(self, broker="localhost", port=1883):
        self.broker = broker
        self.port = port
        self.devices = {}
        self.services = {}

    def GET(self, *uri, **params):
        cherrypy.response.headers['Content-Type'] = 'application/json'
        if len(uri) > 0 and uri[0] == "catalog":
            uri = uri[1:]
        
        if len(uri) == 0 or uri[0] == "broker":
            res = {
                "status": "success",
                "broker": self.broker,
                "port": self.port
            }
            return json.dumps(res).encode('utf-8')
        
        self._purge_stale_registrations()

        if uri[0] == "devices":
            if len(uri) > 1:
                device_id = uri[1]
                if device_id in self.devices:
                    res = {"status": "success", "device": self.devices[device_id]}
                else:
                    cherrypy.response.status = 404
                    res = {"status": "error", "message": "Device not found"}
                return json.dumps(res).encode('utf-8')
            res = {"status": "success", "devices": list(self.devices.values())}
            return json.dumps(res).encode('utf-8')

        elif uri[0] == "services":
            if len(uri) > 1:
                service_id = uri[1]
                if service_id in self.services:
                    res = {"status": "success", "service": self.services[service_id]}
                else:
                    cherrypy.response.status = 404
                    res = {"status": "error", "message": "Service not found"}
                return json.dumps(res).encode('utf-8')
            res = {"status": "success", "services": list(self.services.values())}
            return json.dumps(res).encode('utf-8')

        elif uri[0] == "all":
            res = {
                "status": "success",
                "broker": {"host": self.broker, "port": self.port},
                "devices": list(self.devices.values()),
                "services": list(self.services.values())
            }
            return json.dumps(res).encode('utf-8')
        
        cherrypy.response.status = 400
        res = {"status": "error", "message": "Invalid endpoint"}
        return json.dumps(res).encode('utf-8')

    def POST(self, *uri, **params):
        cherrypy.response.headers['Content-Type'] = 'application/json'
        if len(uri) > 0 and uri[0] == "catalog":
            uri = uri[1:]
        
        try:
            body = cherrypy.request.body.read().decode('utf-8')
            data = json.loads(body)
        except Exception as e:
            cherrypy.response.status = 400
            res = {"status": "error", "message": f"Invalid JSON payload: {str(e)}"}
            return json.dumps(res).encode('utf-8')

        if len(uri) > 0 and uri[0] == "register":
            reg_type = data.get("type", "device")
            reg_id = data.get("id")

            if not reg_id:
                cherrypy.response.status = 400
                res = {"status": "error", "message": "Missing 'id' field"}
                return json.dumps(res).encode('utf-8')

            entry = {
                "id": reg_id,
                "name": data.get("name", reg_id),
                "type": reg_type,
                "ip": data.get("ip", cherrypy.request.remote.ip),
                "topics": data.get("topics", []),
                "hardware": data.get("hardware", []),
                "last_seen": time.time(),
                "status": "ONLINE"
            }

            if reg_type == "service":
                self.services[reg_id] = entry
            else:
                self.devices[reg_id] = entry

            res = {
                "status": "success",
                "message": f"Successfully registered {reg_type} '{reg_id}'",
                "catalog": entry
            }
            return json.dumps(res).encode('utf-8')

        cherrypy.response.status = 400
        res = {"status": "error", "message": "Invalid POST endpoint"}
        return json.dumps(res).encode('utf-8')

    def _purge_stale_registrations(self, timeout_seconds=45):
        now = time.time()
        for dev_id, dev_data in list(self.devices.items()):
            if now - dev_data["last_seen"] > timeout_seconds:
                dev_data["status"] = "OFFLINE"
                if now - dev_data["last_seen"] > (timeout_seconds * 2):
                    del self.devices[dev_id]
        for srv_id, srv_data in list(self.services.items()):
            if now - srv_data["last_seen"] > timeout_seconds:
                srv_data["status"] = "OFFLINE"
                if now - srv_data["last_seen"] > (timeout_seconds * 2):
                    del self.services[srv_id]

if __name__ == "__main__":
    from config import CentralConfig
    config = CentralConfig()
    broker = config.broker
    catalog_port = config.catalog_port

    conf = {
        '/': {
            'request.dispatch': cherrypy.dispatch.MethodDispatcher(),
            'tools.sessions.on': True,
            'tools.response_headers.on': True,
            'tools.encode.on': True,
            'tools.encode.encoding': 'utf-8'
        }
    }

    service = CatalogService(broker=broker)
    cherrypy.tree.mount(service, '/', conf)
    cherrypy.config.update({
        'server.socket_host': '0.0.0.0',
        'server.socket_port': catalog_port
    })
    
    print(f"Starting Catalog REST Service on port {catalog_port}...")
    cherrypy.engine.start()
    cherrypy.engine.block()
