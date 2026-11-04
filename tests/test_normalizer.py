import pytest

from processors.data_normalizer import (
    normalize_device_data,
    normalize_inventory,
)


def test_normalize_single_device():

    ping = {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip": "192.0.2.10",
        "status": "ONLINE",
        "latency_ms": 25.5,
        "packet_loss_percent": 0.0,
    }

    api = {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
        "platform": "cisco",
        "status": "online",
        "cpu_usage": 35,
        "memory_usage": 62,
    }

    result = normalize_device_data(
        device_id="router-01",
        ping=ping,
        api=api,
    )

    assert result["identity"]["hostname"] == "Router-01"
    assert result["identity"]["platform"] == "cisco"
    assert result["reachability"]["status"] == "ONLINE"
    assert result["api"]["cpu_usage"] == 35


def test_normalize_multiple_sources():

    ping_results = [
        {
            "device_id": "router-01",
            "hostname": "Router-01",
            "ip": "192.0.2.10",
            "status": "ONLINE",
        }
    ]

    api_devices = [
        {
            "device_id": "router-01",
            "hostname": "Router-01",
            "ip_address": "192.0.2.10",
            "platform": "cisco",
        }
    ]

    ssh_results = [
        {
            "device_id": "router-01",
            "hostname": "Router-01",
            "ip": "192.0.2.10",
            "status": "CONNECTED",
        }
    ]

    result = normalize_inventory(
        ping_results,
        api_devices,
        ssh_results,
    )

    assert len(result) == 1
    assert result[0]["device_id"] == "router-01"
    assert result[0]["reachability"]["status"] == "ONLINE"
    assert result[0]["ssh"]["status"] == "CONNECTED"


def test_missing_source_data():

    result = normalize_device_data(
        device_id="router-01",
        ping={
            "device_id": "router-01",
            "status": "OFFLINE",
        },
    )

    assert result["api"]["available"] is False
    assert result["ssh"]["status"] == "NOT_CHECKED"
    assert result["network"]["interfaces"] == []


def test_missing_device_id():

    with pytest.raises(ValueError):
        normalize_inventory(
            ping_results=[
                {"status": "ONLINE"}
            ],
            api_devices=[],
            ssh_results=[],
        )


def test_duplicate_device_id():

    with pytest.raises(ValueError):
        normalize_inventory(
            ping_results=[
                {"device_id": "router-01"},
                {"device_id": "router-01"},
            ],
            api_devices=[],
            ssh_results=[],
        )
