import os
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

from dotenv import load_dotenv


# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class NMSAPIError(Exception):
    """
    Custom exception for NMS API errors.
    """

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class NMSAPIClient:
    """
    HTTP client for communicating with Mock NMS API.
    """

    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout: int = 5,
    ):

        if not base_url:
            raise ValueError("API base URL is required")

        if not api_token:
            raise ValueError("API token is required")

        if timeout < 1:
            raise ValueError("Timeout must be at least 1 second")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()

        self.session.headers.update({
            "Accept": "application/json",
            "X-API-Token": api_token,
        })

    def _get(self, endpoint: str) -> dict:
        """
        Send HTTP GET request and process the response.
        """

        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                raise NMSAPIError(
                    "Invalid API response format"
                )

            return data

        except requests.exceptions.Timeout as exc:
            raise NMSAPIError(
                "API request timed out"
            ) from exc

        except requests.exceptions.ConnectionError as exc:
            raise NMSAPIError(
                "Cannot connect to NMS API"
            ) from exc

        except requests.exceptions.HTTPError as exc:

            status_code = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            detail = ""

            if exc.response is not None:
                try:
                    body = exc.response.json()
                    detail = body.get("detail", "")
                except ValueError:
                    detail = ""

            raise NMSAPIError(
                f"HTTP {status_code}: {detail or 'Request failed'}",
                status_code=status_code,
            ) from exc

        except requests.exceptions.JSONDecodeError as exc:
            raise NMSAPIError(
                "API returned invalid JSON"
            ) from exc

    def health_check(self) -> dict:
        """
        Check NMS API availability.
        """

        return self._get("/health")

    def get_devices(self) -> list:
        """
        Retrieve all network devices.
        """

        data = self._get("/devices")

        devices = data.get("devices")

        if not isinstance(devices, list):
            raise NMSAPIError(
                "Invalid devices data in API response"
            )

        return devices

    def get_device(self, device_id: str) -> dict:
        """
        Retrieve information about one device.
        """

        if not device_id:
            raise ValueError("Device ID is required")

        safe_device_id = quote(device_id, safe="")

        data = self._get(
            f"/devices/{safe_device_id}"
        )

        if "device_id" not in data:
            raise NMSAPIError(
                "Invalid device response"
            )

        return data

    def close(self):
        """
        Close HTTP session.
        """

        self.session.close()


def create_api_client() -> NMSAPIClient:
    """
    Load API configuration and create client.
    """

    load_dotenv(PROJECT_ROOT / ".env")

    base_url = os.getenv("API_BASE_URL")
    api_token = os.getenv("API_TOKEN")

    with open(
        PROJECT_ROOT / "config/settings.yaml",
        encoding="utf-8",
    ) as file:

        settings = yaml.safe_load(file)

    timeout = settings.get(
        "api", {}
    ).get("timeout_seconds", 5)

    return NMSAPIClient(
        base_url=base_url,
        api_token=api_token,
        timeout=timeout,
    )
