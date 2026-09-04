import requests
from config import API_BASE_URL, DEVICE_ID, API_KEY
from logger import logger

class ApiClient:
    def __init__(self):
        self.base_url = API_BASE_URL
        self.device_id = DEVICE_ID
        self.headers = {
            "X-Device-ID": self.device_id, "X-API-Key": API_KEY, "Content-Type": "application/json"
        }

    def get_config(self) -> dict:
        try:
            r = requests.get(f"{self.base_url}/pi/{self.device_id}/config", headers=self.headers, timeout=10)
            if r.status_code == 200: return r.json()
            logger.error(f"Config fetch failed: HTTP {r.status_code}")
            return None
        except Exception as e:
            logger.error(f"Config fetch network error: {e}")
            return None

    def get_commands(self) -> list:
        try:
            r = requests.get(f"{self.base_url}/pi/{self.device_id}/commands", headers=self.headers, timeout=10)
            if r.status_code == 200: return r.json().get("commands", [])
            return []
        except Exception:
            return []

    def push_state(self, state_snapshot: dict) -> bool:
        try:
            r = requests.post(f"{self.base_url}/pi/{self.device_id}/state", json=state_snapshot, headers=self.headers, timeout=10)
            return r.status_code == 200
        except Exception:
            return False

    def push_ack(self, command_id: str, status: str, verification: str, error: str = None) -> bool:
        try:
            # Unified strict contract
            payload = {
                "command_id": command_id, 
                "status": status, 
                "verification_state": verification,
                "error": error
            }
            r = requests.post(f"{self.base_url}/pi/command-ack", json=payload, headers=self.headers, timeout=10)
            return r.status_code == 200
        except Exception:
            return False