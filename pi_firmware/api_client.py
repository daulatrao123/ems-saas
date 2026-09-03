import requests

class ApiClient:
    def __init__(self, config, logger):
        self.cfg = config
        self.log = logger
        self.session = requests.Session()

    def _url(self, path):
        return f"{self.cfg.backendUrl}{path}"

    def sync(self, payload):
        try:
            r = self.session.post(self._url("/api/pi/sync"), json=payload, timeout=15)
            if r.status_code == 200:
                return r.json()
            self.log.error(f"Sync failed {r.status_code}: {r.text[:200]}")
        except Exception as e:
            self.log.error(f"Sync exception: {e}")
        return None

    def ack_command(self, command_id, success, result=None, error=None):
        payload = {
            "deviceId": self.cfg.deviceId,
            "key": self.cfg.apiKey,
            "command_id": command_id,
            "success": bool(success),
            "error": error,
            "result": result
        }
        try:
            r = self.session.post(self._url("/api/pi/command-ack"), json=payload, timeout=10)
            if r.status_code == 200:
                return True
            self.log.error(f"ACK failed {r.status_code}: {r.text[:200]}")
        except Exception as e:
            self.log.error(f"ACK exception: {e}")
        return False