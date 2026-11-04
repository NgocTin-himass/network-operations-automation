import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any


def parse_ping_output(output: str) -> dict[str, Any]:
    """
    Parse Linux ping output into structured data.
    """

    sent_match = re.search(
        r"(\d+)\s+packets transmitted",
        output
    )

    received_match = re.search(
        r"(\d+)\s+(?:packets\s+)?received",
        output
    )

    loss_match = re.search(
        r"([\d.]+)%\s*packet loss",
        output
    )

    latency_match = re.search(
        r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
        r"[\d.]+/([\d.]+)/",
        output
    )

    if not sent_match or not loss_match:
        raise ValueError("Unable to parse ping output")

    packets_sent = int(sent_match.group(1))
    packets_received = (
        int(received_match.group(1))
        if received_match
        else 0
    )

    packet_loss = float(loss_match.group(1))

    latency = (
        float(latency_match.group(1))
        if latency_match and packets_received > 0
        else None
    )

    return {
        "packets_transmitted": packets_sent,
        "packets_received": packets_received,
        "packet_loss_percent": packet_loss,
        "latency_ms": latency,
    }


def check_device_ping(
    device: dict[str, Any],
    count: int = 5,
    timeout_seconds: int = 1,
) -> dict[str, Any]:
    """
    Check network reachability of one device.
    """

    hostname = device.get("hostname", "Unknown")
    device_ip = device.get("ip", "")

    result = {
        "device_id": device.get("id"),
        "hostname": hostname,
        "ip": device_ip,
        "status": "ERROR",
        "latency_ms": None,
        "packet_loss_percent": None,
        "packets_transmitted": 0,
        "packets_received": 0,
        "error": None,
    }

    if not device_ip:
        result["error"] = "Missing device IP"
        return result

    if count < 1 or timeout_seconds < 1:
        result["error"] = "Invalid ping parameters"
        return result

    ping_path = shutil.which("ping")

    if ping_path is None:
        result["error"] = "Ping command not found"
        return result

    command = [
        ping_path,
        "-n",
        "-c", str(count),
        "-W", str(timeout_seconds),
        device_ip,
    ]

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=(count * timeout_seconds) + 5,
            check=False,
        )

        output = process.stdout + "\n" + process.stderr

        if process.returncode == 2:
            result["error"] = process.stderr.strip() or "Ping execution error"
            return result

        parsed = parse_ping_output(output)

        result.update(parsed)

        if parsed["packets_received"] > 0:
            result["status"] = "ONLINE"
        else:
            result["status"] = "OFFLINE"

    except subprocess.TimeoutExpired:
        result["error"] = "Ping process timed out"

    except ValueError as exc:
        result["error"] = str(exc)

    except OSError as exc:
        result["error"] = str(exc)

    return result


def collect_ping_results(
    devices: list[dict[str, Any]],
    count: int = 5,
    timeout_seconds: int = 1,
    max_workers: int = 5,
) -> list[dict[str, Any]]:
    """
    Check multiple devices concurrently.
    """

    if max_workers < 1:
        raise ValueError("max_workers must be at least 1")

    ping_function = partial(
        check_device_ping,
        count=count,
        timeout_seconds=timeout_seconds,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(ping_function, devices))

    return results
