from typing import Any


SEVERITY_LEVELS = {
    "HEALTHY": 0,
    "WARNING": 1,
    "CRITICAL": 2,
}


def _to_number(value: Any) -> float | None:
    """Convert a metric to float safely."""

    if value is None:
        return None

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _create_alert(
    device: dict,
    check: str,
    severity: str,
    message: str,
    observed_value: Any = None,
    threshold: Any = None,
) -> dict:

    identity = device.get("identity", {})

    return {
        "device_id": device.get("device_id"),
        "hostname": identity.get("hostname"),
        "ip_address": identity.get("ip_address"),
        "check": check,
        "severity": severity,
        "message": message,
        "observed_value": observed_value,
        "threshold": threshold,
    }


def _evaluate_metric(
    device: dict,
    value: Any,
    warning_threshold: Any,
    critical_threshold: Any,
    check_name: str,
    unit: str,
) -> list[dict]:

    alerts = []

    metric = _to_number(value)
    warning = _to_number(warning_threshold)
    critical = _to_number(critical_threshold)

    if metric is None:
        return alerts

    if warning is not None and critical is not None:
        if critical < warning:
            raise ValueError(
                f"Invalid thresholds for {check_name}"
            )

    if critical is not None and metric >= critical:

        alerts.append(
            _create_alert(
                device=device,
                check=check_name,
                severity="CRITICAL",
                message=f"{check_name} reached critical threshold",
                observed_value=metric,
                threshold=critical,
            )
        )

    elif warning is not None and metric >= warning:

        alerts.append(
            _create_alert(
                device=device,
                check=check_name,
                severity="WARNING",
                message=f"{check_name} reached warning threshold",
                observed_value=metric,
                threshold=warning,
            )
        )

    return alerts


def check_reachability(
    device: dict,
    settings: dict,
) -> list[dict]:

    alerts = []

    reachability = device.get("reachability", {})
    status = str(
        reachability.get("status", "UNKNOWN")
    ).upper()

    # Device completely unreachable
    if status == "OFFLINE":

        alerts.append(
            _create_alert(
                device=device,
                check="DEVICE_UNREACHABLE",
                severity="CRITICAL",
                message="Device is not reachable",
            )
        )

    elif status == "ERROR":

        alerts.append(
            _create_alert(
                device=device,
                check="PING_COLLECTION_ERROR",
                severity="WARNING",
                message="Unable to complete ping check",
            )
        )

    ping_settings = settings.get("ping", {})

    # Packet loss check
    packet_loss = reachability.get(
        "packet_loss_percent"
    )

    alerts.extend(
        _evaluate_metric(
            device=device,
            value=packet_loss,
            warning_threshold=ping_settings.get(
                "warning_packet_loss_percent"
            ),
            critical_threshold=ping_settings.get(
                "critical_packet_loss_percent"
            ),
            check_name="PACKET_LOSS",
            unit="%",
        )
    )

    # Latency check
    latency = reachability.get("latency_ms")

    alerts.extend(
        _evaluate_metric(
            device=device,
            value=latency,
            warning_threshold=ping_settings.get(
                "warning_latency_ms"
            ),
            critical_threshold=ping_settings.get(
                "critical_latency_ms"
            ),
            check_name="LATENCY",
            unit="ms",
        )
    )

    return alerts


def check_resources(
    device: dict,
    settings: dict,
) -> list[dict]:

    alerts = []

    api_data = device.get("api", {})
    resource_settings = settings.get(
        "resources", {}
    )

    if not api_data.get("available"):
        return alerts

    # CPU
    alerts.extend(
        _evaluate_metric(
            device=device,
            value=api_data.get("cpu_usage"),
            warning_threshold=resource_settings.get(
                "cpu_warning_percent"
            ),
            critical_threshold=resource_settings.get(
                "cpu_critical_percent"
            ),
            check_name="CPU_USAGE",
            unit="%",
        )
    )

    # Memory
    alerts.extend(
        _evaluate_metric(
            device=device,
            value=api_data.get("memory_usage"),
            warning_threshold=resource_settings.get(
                "memory_warning_percent"
            ),
            critical_threshold=resource_settings.get(
                "memory_critical_percent"
            ),
            check_name="MEMORY_USAGE",
            unit="%",
        )
    )

    return alerts


def check_interfaces(
    device: dict,
    settings: dict,
) -> list[dict]:

    alerts = []

    interfaces = device.get(
        "network", {}
    ).get("interfaces", [])

    interface_settings = settings.get(
        "interfaces", {}
    )

    severity = str(
        interface_settings.get(
            "down_severity", "CRITICAL"
        )
    ).upper()

    if severity not in ("WARNING", "CRITICAL"):
        raise ValueError(
            "Interface down_severity must be WARNING or CRITICAL"
        )

    for interface in interfaces:

        name = interface.get(
            "interface", "Unknown"
        )

        status = str(
            interface.get("status", "")
        ).strip().lower()

        protocol = str(
            interface.get(
                "proto",
                interface.get("protocol", "")
            )
        ).strip().lower()

        # Ignore administratively disabled interfaces
        if "administratively down" in status:
            continue

        # Interface should be operationally up/up
        if status != "up" or protocol != "up":

            alerts.append(
                _create_alert(
                    device=device,
                    check="INTERFACE_DOWN",
                    severity=severity,
                    message=(
                        f"Interface {name} is not operational "
                        f"(status={status}, protocol={protocol})"
                    ),
                    observed_value={
                        "status": status,
                        "protocol": protocol,
                    },
                    threshold="up/up",
                )
            )

    return alerts


def evaluate_device_health(
    device: dict,
    settings: dict,
) -> dict:

    alerts = []

    alerts.extend(
        check_reachability(device, settings)
    )

    alerts.extend(
        check_resources(device, settings)
    )

    alerts.extend(
        check_interfaces(device, settings)
    )

    overall_status = max(
        (
            alert["severity"]
            for alert in alerts
        ),
        key=lambda severity: SEVERITY_LEVELS[severity],
        default="HEALTHY",
    )

    return {
        "device_id": device.get("device_id"),
        "hostname": device.get(
            "identity", {}
        ).get("hostname"),
        "overall_status": overall_status,
        "alert_count": len(alerts),
        "alerts": alerts,
    }


def evaluate_inventory_health(
    devices: list[dict],
    settings: dict,
) -> list[dict]:

    return [
        evaluate_device_health(device, settings)
        for device in devices
    ]
