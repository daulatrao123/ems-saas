import requests

from config import (
    API_BASE_URL,
    API_TIMEOUT_S,
    DEVICE_ID,
    API_KEY,
)

from logger import logger


class ApiClient:
    """
    Cloud API client.

    The Pi uses ONE cloud synchronization contract:

        POST /api/pi/sync

    The response may contain:
        - configuration
        - device capability
        - one queued command

    Command acknowledgement uses:

        POST /api/pi/command-ack
    """

    def __init__(self):
        self.base_url = API_BASE_URL.rstrip("/")
        self.device_id = DEVICE_ID
        self.api_key = API_KEY

        self.session = requests.Session()

        self.session.headers.update(
            {
                "X-Device-ID": self.device_id,
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ============================================================
    # SYNC
    # ============================================================

    def sync(self, snapshot: dict):
        """
        Synchronize the Pi state with the backend.

        Returns:
            dict on success
            None on network/API failure
        """

        payload = dict(snapshot)

        payload["deviceId"] = self.device_id
        payload["key"] = self.api_key

        try:
            response = self.session.post(
                f"{self.base_url}/pi/sync",
                json=payload,
                timeout=API_TIMEOUT_S,
            )

            if response.status_code != 200:
                logger.error(
                    "Pi sync failed: HTTP %s",
                    response.status_code,
                )
                return None

            data = response.json()

            if not isinstance(data, dict):
                logger.error(
                    "Pi sync returned invalid JSON object."
                )
                return None

            if data.get("success") is not True:
                logger.error(
                    "Pi sync rejected by backend."
                )
                return None

            return data

        except requests.RequestException as exc:
            logger.warning(
                "Pi cloud unavailable: %s",
                exc,
            )
            return None

        except ValueError as exc:
            logger.error(
                "Pi sync JSON decode failure: %s",
                exc,
            )
            return None

        except Exception as exc:
            logger.error(
                "Unexpected Pi sync failure: %s",
                exc,
            )
            return None

    # ============================================================
    # COMMAND ACK
    # ============================================================

    def push_ack(
        self,
        command_id: str,
        status: str,
        verification: str = "UNKNOWN",
        error: str = None,
    ) -> bool:
        """
        Acknowledge a command.

        Authentication is intentionally included in the body because
        the current backend authentication contract requires it.
        """

        payload = {
            "deviceId": self.device_id,
            "key": self.api_key,
            "command_id": str(command_id),
            "status": str(status).upper(),
            "verification_state": (
                str(verification).upper()
                if verification
                else "UNKNOWN"
            ),
            "error": error,
        }

        try:
            response = self.session.post(
                f"{self.base_url}/pi/command-ack",
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

        except requests.RequestException as exc:
            logger.warning(
                "Command ACK cloud unavailable: %s",
                exc,
            )
            return False

        except Exception as exc:
            logger.error(
                "Command ACK failure: %s",
                exc,
            )
            return False

    # ============================================================
    # CONNECTION TEST
    # ============================================================

    def ping(self) -> bool:
        try:
            response = self.session.get(
                f"{self.base_url}/../ping",
                timeout=API_TIMEOUT_S,
            )

            return response.status_code == 200

        except Exception:
            return False

    # ============================================================
    # CLOSE
    # ============================================================

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass