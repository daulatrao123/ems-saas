import requests

from config import (
    API_BASE_URL,
    API_TIMEOUT_S,
    DEVICE_ID,
    API_KEY,
)

from logger import logger


class ApiClient:

    def __init__(self):

        self.base_url = (
            API_BASE_URL.rstrip("/")
        )

        self.device_id = DEVICE_ID

        self.headers = {
            "X-Device-ID": self.device_id,
            "X-API-Key": API_KEY,
            "Content-Type": "application/json",
        }

        self.session = requests.Session()

        self.session.headers.update(
            self.headers
        )

    # ============================================================
    # CONFIG
    # ============================================================

    def get_config(self) -> dict:

        try:

            response = self.session.get(
                f"{self.base_url}/pi/"
                f"{self.device_id}/config",
                timeout=API_TIMEOUT_S,
            )

            if response.status_code != 200:

                logger.error(
                    "Config fetch failed: HTTP %s",
                    response.status_code,
                )

                return None

            data = response.json()

            if not isinstance(data, dict):
                return None

            return data

        except Exception as exc:

            logger.error(
                "Config fetch failed: %s",
                exc,
            )

            return None

    # ============================================================
    # COMMANDS
    # ============================================================

    def get_commands(self) -> list:

        try:

            response = self.session.get(
                f"{self.base_url}/pi/"
                f"{self.device_id}/commands",
                timeout=API_TIMEOUT_S,
            )

            if response.status_code != 200:
                return []

            data = response.json()

            commands = data.get(
                "commands",
                [],
            )

            if not isinstance(
                commands,
                list,
            ):
                return []

            return commands

        except Exception:
            return []

    # ============================================================
    # STATE
    # ============================================================

    def push_state(
        self,
        state_snapshot: dict,
    ) -> bool:

        try:

            response = self.session.post(
                f"{self.base_url}/pi/"
                f"{self.device_id}/state",
                json=state_snapshot,
                timeout=API_TIMEOUT_S,
            )

            return response.status_code == 200

        except Exception:
            return False

    # ============================================================
    # ACK
    # ============================================================

    def push_ack(
        self,
        command_id: str,
        status: str,
        verification: str,
        error: str = None,
    ) -> bool:

        try:

            payload = {
                "command_id": str(command_id),
                "status": str(status).upper(),
                "verification_state": (
                    verification
                    if verification
                    else "UNKNOWN"
                ),
                "error": error,
            }

            response = self.session.post(
                f"{self.base_url}/pi/"
                "command-ack",
                json=payload,
                timeout=API_TIMEOUT_S,
            )

            if response.status_code != 200:

                logger.error(
                    "Command ACK failed: HTTP %s",
                    response.status_code,
                )

                return False

            return True

        except Exception as exc:

            logger.error(
                "Command ACK network error: %s",
                exc,
            )

            return False