import subprocess

import pytest

import collectors.ping_collector as ping_collector
from collectors.ping_collector import (
    parse_ping_output,
    check_device_ping,
)


SUCCESS_OUTPUT = """
3 packets transmitted, 3 received, 0% packet loss, time 2041ms
rtt min/avg/max/mdev = 0.031/0.045/0.061/0.012 ms
"""

OFFLINE_OUTPUT = """
3 packets transmitted, 0 received, 100% packet loss, time 2041ms
"""

PARTIAL_OUTPUT = """
5 packets transmitted, 4 received, 20% packet loss, time 4080ms
rtt min/avg/max/mdev = 0.040/0.070/0.100/0.020 ms
"""


def test_parse_success():
    result = parse_ping_output(SUCCESS_OUTPUT)

    assert result["packets_transmitted"] == 3
    assert result["packets_received"] == 3
    assert result["packet_loss_percent"] == 0.0
    assert result["latency_ms"] == 0.045


def test_parse_offline():
    result = parse_ping_output(OFFLINE_OUTPUT)

    assert result["packets_transmitted"] == 3
    assert result["packets_received"] == 0
    assert result["packet_loss_percent"] == 100.0
    assert result["latency_ms"] is None


def test_parse_partial_packet_loss():
    result = parse_ping_output(PARTIAL_OUTPUT)

    assert result["packets_received"] == 4
    assert result["packet_loss_percent"] == 20.0
    assert result["latency_ms"] == 0.070


def test_invalid_ping_output():
    with pytest.raises(ValueError):
        parse_ping_output("Invalid ping response")


def test_device_online(monkeypatch):
    device = {
        "id": "test-router",
        "hostname": "Test-Router",
        "ip": "192.0.2.10",
    }

    monkeypatch.setattr(
        ping_collector.shutil,
        "which",
        lambda _: "/usr/bin/ping",
    )

    monkeypatch.setattr(
        ping_collector.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout=SUCCESS_OUTPUT,
            stderr="",
        ),
    )

    result = check_device_ping(device)

    assert result["status"] == "ONLINE"
    assert result["packet_loss_percent"] == 0.0
    assert result["error"] is None
