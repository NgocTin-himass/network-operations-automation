import pytest

from monitors.health_check import (
    evaluate_device_health,
    check_reachability,
    check_resources,
    check_interfaces,
)


SETTINGS = {
    "ping": {
        "warning_latency_ms": 100,
        "critical_latency_ms": 300,
        "warning_packet_loss_percent": 20,
        "critical_packet_loss_percent": 100,
    },
    "resources": {
        "cpu_warning_percent": 70,
        "cpu_critical_percent": 90,
        "memory_warning_percent": 80,
        "memory_critical_percent": 95,
    },
    "interfaces": {
        "down_severity": "CRITICAL",
    },
}


def create_device():

    return {
        "device_id": "router-01",

        "identity": {
            "hostname": "Router-01",
            "ip_address": "192.0.2.10",
        },

        "reachability": {
            "status": "ONLINE",
            "latency_ms": 25,
            "packet_loss_percent": 0,
        },

        "api": {
            "available": True,
            "cpu_usage": 35,
            "memory_usage": 50,
        },

        "ssh": {
            "status": "CONNECTED",
        },

        "network": {
            "interfaces": [
                {
                    "interface": "GigabitEthernet0/0",
                    "status": "up",
                    "proto": "up",
                }
            ],
            "routes": [],
        },
    }


def test_healthy_device():

    device = create_device()

    result = evaluate_device_health(
        device,
        SETTINGS,
    )

    assert result["overall_status"] == "HEALTHY"
    assert result["alert_count"] == 0


def test_device_offline():

    device = create_device()
    device["reachability"]["status"] = "OFFLINE"

    result = check_reachability(
        device,
        SETTINGS,
    )

    assert any(
        alert["severity"] == "CRITICAL"
        for alert in result
    )

    assert any(
        alert["check"] == "DEVICE_UNREACHABLE"
        for alert in result
    )


def test_packet_loss_warning():

    device = create_device()

    device["reachability"]["packet_loss_percent"] = 25

    alerts = check_reachability(device, SETTINGS)

    assert any(
        alert["check"] == "PACKET_LOSS"
        and alert["severity"] == "WARNING"
        for alert in alerts
    )


def test_packet_loss_critical():

    device = create_device()

    device["reachability"]["packet_loss_percent"] = 100

    alerts = check_reachability(device, SETTINGS)

    assert any(
        alert["check"] == "PACKET_LOSS"
        and alert["severity"] == "CRITICAL"
        for alert in alerts
    )


def test_latency_critical():

    device = create_device()

    device["reachability"]["latency_ms"] = 350

    alerts = check_reachability(device, SETTINGS)

    assert any(
        alert["check"] == "LATENCY"
        and alert["severity"] == "CRITICAL"
        for alert in alerts
    )


def test_cpu_warning():

    device = create_device()
    device["api"]["cpu_usage"] = 75

    alerts = check_resources(device, SETTINGS)

    assert any(
        alert["check"] == "CPU_USAGE"
        and alert["severity"] == "WARNING"
        for alert in alerts
    )


def test_cpu_critical():

    device = create_device()
    device["api"]["cpu_usage"] = 95

    alerts = check_resources(device, SETTINGS)

    assert any(
        alert["check"] == "CPU_USAGE"
        and alert["severity"] == "CRITICAL"
        for alert in alerts
    )


def test_memory_critical():

    device = create_device()
    device["api"]["memory_usage"] = 96

    alerts = check_resources(device, SETTINGS)

    assert any(
        alert["check"] == "MEMORY_USAGE"
        and alert["severity"] == "CRITICAL"
        for alert in alerts
    )


def test_interface_down():

    device = create_device()

    device["network"]["interfaces"].append({
        "interface": "GigabitEthernet0/1",
        "status": "down",
        "proto": "down",
    })

    alerts = check_interfaces(device, SETTINGS)

    assert len(alerts) == 1
    assert alerts[0]["check"] == "INTERFACE_DOWN"
    assert alerts[0]["severity"] == "CRITICAL"


def test_administratively_down_ignored():

    device = create_device()

    device["network"]["interfaces"].append({
        "interface": "GigabitEthernet0/2",
        "status": "administratively down",
        "proto": "down",
    })

    alerts = check_interfaces(device, SETTINGS)

    assert len(alerts) == 0


def test_overall_status_critical():

    device = create_device()

    device["api"]["cpu_usage"] = 95
    device["reachability"]["latency_ms"] = 120

    result = evaluate_device_health(
        device,
        SETTINGS,
    )

    assert result["overall_status"] == "CRITICAL"
    assert result["alert_count"] == 2


def test_invalid_interface_severity():

    device = create_device()

    device["network"]["interfaces"].append({
        "interface": "GigabitEthernet0/1",
        "status": "down",
        "proto": "down",
    })

    invalid_settings = {
        **SETTINGS,
        "interfaces": {
            "down_severity": "INFO"
        },
    }

    with pytest.raises(ValueError):
        check_interfaces(
            device,
            invalid_settings,
        )
