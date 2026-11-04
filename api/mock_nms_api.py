import os
import hmac

from pathlib import Path
from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    HTTPException,
    Depends
)

from fastapi.security import APIKeyHeader


# Load environment variables
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

API_TOKEN = os.getenv("API_TOKEN")

if not API_TOKEN:
    raise RuntimeError("API_TOKEN is not configured")


app = FastAPI(
    title="Network Management API",
    description="Mock NMS API for Network Automation Project",
    version="1.0.0"
)


# API Authentication
api_key_header = APIKeyHeader(
    name="X-API-Token",
    auto_error=False
)


def verify_api_token(
    api_token: str | None = Depends(api_key_header)
):
    if not api_token or not hmac.compare_digest(
        api_token,
        API_TOKEN
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API token"
        )

    return True


# Mock network devices
MOCK_DEVICES = [
    {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
        "platform": "cisco",
        "status": "online",
        "cpu_usage": 35,
        "memory_usage": 62
    },
    {
        "device_id": "switch-01",
        "hostname": "Switch-01",
        "ip_address": "192.0.2.20",
        "platform": "cisco",
        "status": "online",
        "cpu_usage": 25,
        "memory_usage": 48
    }
]


# Public health endpoint
@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "mock-nms-api"
    }


# Protected device list endpoint
@app.get("/devices")
def get_devices(
    authenticated: bool = Depends(verify_api_token)
):
    return {
        "count": len(MOCK_DEVICES),
        "devices": MOCK_DEVICES
    }


# Protected device detail endpoint
@app.get("/devices/{device_id}")
def get_device(
    device_id: str,
    authenticated: bool = Depends(verify_api_token)
):

    for device in MOCK_DEVICES:
        if device["device_id"] == device_id:
            return device

    raise HTTPException(
        status_code=404,
        detail=f"Device '{device_id}' not found"
    )
