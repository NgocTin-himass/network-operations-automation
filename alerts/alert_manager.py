from datetime import datetime, timezone
from typing import Any


SEVERITY_PRIORITY = {
    "HEALTHY": 0,
    "WARNING": 1,
    "CRITICAL": 2,
}


def deduplicate_alerts(
    alerts: list[dict],
) -> list[dict]:
    """
    Remove duplicated alerts within one monitoring run.
    """

    unique_alerts = []
    seen = set()

    for alert in alerts:

        key = (
            alert.get("device_id"),
            alert.get("check"),
            alert.get("severity"),
            alert.get("message"),
        )

        if key not in seen:
            seen.add(key)
            unique_alerts.append(alert)

    return unique_alerts


def build_alert_summary(
    health_results: list[dict],
    deduplicate: bool = True,
) -> dict[str, Any]:
    """
    Aggregate health results into a unified alert summary.
    """

    alerts = []

    devices = []

    for result in health_results:

        device_alerts = result.get("alerts", [])

        alerts.extend(device_alerts)

        devices.append({
            "device_id": result.get("device_id"),
            "hostname": result.get("hostname"),
            "overall_status": result.get("overall_status"),
            "alert_count": len(device_alerts),
        })

    if deduplicate:
        alerts = deduplicate_alerts(alerts)

    severity_counts = {
        "WARNING": 0,
        "CRITICAL": 0,
    }

    for alert in alerts:

        severity = alert.get("severity")

        if severity in severity_counts:
            severity_counts[severity] += 1

    overall_status = max(
        (
            device.get("overall_status", "HEALTHY")
            for device in devices
        ),
        key=lambda status: SEVERITY_PRIORITY.get(
            status, 0
        ),
        default="HEALTHY",
    )

    return {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "overall_status": overall_status,

        "device_count": len(devices),

        "alert_count": len(alerts),

        "severity_counts": severity_counts,

        "devices": devices,

        "alerts": alerts,
    }


def print_alert_summary(
    summary: dict[str, Any],
) -> None:
    """
    Display monitoring alerts in terminal.
    """

    print("\n" + "=" * 55)
    print("          NETWORK OPERATIONS ALERTS")
    print("=" * 55)

    print("Generated at:", summary["generated_at"])
    print("Devices:", summary["device_count"])
    print("Overall Status:", summary["overall_status"])
    print("Total Alerts:", summary["alert_count"])

    print("-" * 55)

    for alert in summary["alerts"]:

        print(
            f'[{alert["severity"]}] '
            f'{alert["hostname"]} - '
            f'{alert["check"]}'
        )

        print("Message:", alert["message"])

        if alert.get("observed_value") is not None:
            print("Observed:", alert["observed_value"])

        if alert.get("threshold") is not None:
            print("Threshold:", alert["threshold"])

        print("-" * 55)

    print("=" * 55)
