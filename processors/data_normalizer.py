from typing import Any


def normalize_device_data(
    device_id: str,
    ping: dict | None = None,
    api: dict | None = None,
    ssh: dict | None = None,
    interfaces: list | None = None,
    routes: list | None = None,
) -> dict[str, Any]:
    """
    Merge data collected from Ping, API and SSH
    into a unified device structure.
    """

    ping = ping or {}
    api = api or {}
    ssh = ssh or {}

    return {
        "device_id": device_id,

        "identity": {
            "hostname": (
                api.get("hostname")
                or ping.get("hostname")
                or ssh.get("hostname")
            ),
            "ip_address": (
                api.get("ip_address")
                or ping.get("ip")
                or ssh.get("ip")
            ),
            "platform": api.get("platform"),
        },

        "reachability": {
            "status": ping.get("status", "UNKNOWN"),
            "latency_ms": ping.get("latency_ms"),
            "packet_loss_percent": ping.get(
                "packet_loss_percent"
            ),
            "error": ping.get("error"),
        },

        "api": {
            "available": bool(api),
            "reported_status": api.get("status"),
            "cpu_usage": api.get("cpu_usage"),
            "memory_usage": api.get("memory_usage"),
        },

        "ssh": {
            "status": ssh.get("status", "NOT_CHECKED"),
            "error": ssh.get("error"),
            "commands": ssh.get("commands", {}),
        },

        "network": {
            "interfaces": interfaces or [],
            "routes": routes or [],
        },
    }


def _index_records(
    records: list[dict],
    source_name: str,
) -> dict[str, dict]:
    """Index collector records by device_id."""

    indexed = {}

    for record in records:

        device_id = record.get("device_id")

        if not device_id:
            raise ValueError(
                f"{source_name} record is missing device_id"
            )

        if device_id in indexed:
            raise ValueError(
                f"Duplicate device_id in {source_name}: {device_id}"
            )

        indexed[device_id] = record

    return indexed


def normalize_inventory(
    ping_results: list[dict],
    api_devices: list[dict],
    ssh_results: list[dict],
    interfaces_by_device: dict | None = None,
    routes_by_device: dict | None = None,
) -> list[dict]:
    """
    Merge multiple data sources by device_id.
    """

    ping_map = _index_records(ping_results, "Ping")
    api_map = _index_records(api_devices, "API")
    ssh_map = _index_records(ssh_results, "SSH")

    device_ids = list(dict.fromkeys(
        list(api_map) + list(ping_map) + list(ssh_map)
    ))

    interfaces_by_device = interfaces_by_device or {}
    routes_by_device = routes_by_device or {}

    normalized = []

    for device_id in device_ids:

        normalized.append(
            normalize_device_data(
                device_id=device_id,
                ping=ping_map.get(device_id),
                api=api_map.get(device_id),
                ssh=ssh_map.get(device_id),
                interfaces=interfaces_by_device.get(
                    device_id, []
                ),
                routes=routes_by_device.get(
                    device_id, []
                ),
            )
        )

    return normalized
