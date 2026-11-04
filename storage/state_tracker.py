import json
import math

from datetime import datetime, timezone
from typing import Any

from storage.database import (
    DEFAULT_DB_PATH,
    get_connection,
    initialize_database,
)


KNOWN_REACHABILITY_STATES = {
    "ONLINE",
    "OFFLINE",
}

METRIC_UNITS = {
    "CPU_USAGE": "%",
    "MEMORY_USAGE": "%",
    "LATENCY": "ms",
    "PACKET_LOSS": "%",
}


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat(timespec="microseconds")


def _serialize_value(value: Any) -> str | None:
    """Serialize metric values into JSON text."""

    if value is None:
        return None

    return json.dumps(
        value,
        ensure_ascii=False,
    )


def _to_float(value: Any) -> float | None:
    """Convert valid metric values into float."""

    if value is None:
        return None

    try:
        result = float(value)

        if not math.isfinite(result):
            return None

        return result

    except (ValueError, TypeError):
        return None


def _make_alert_key(
    device_id: str,
    check_name: str,
) -> str:
    return f"{device_id}::{check_name}"


def _can_resolve_alert(
    check_name: str,
    device: dict,
) -> bool:

    reachability = device.get(
        "reachability", {}
    )

    api_data = device.get("api", {})

    network_data = device.get(
        "network", {}
    )

    status = str(
        reachability.get("status", "UNKNOWN")
    ).upper()

    if check_name == "DEVICE_UNREACHABLE":
        return status in KNOWN_REACHABILITY_STATES

    if check_name == "PACKET_LOSS":
        return (
            status in KNOWN_REACHABILITY_STATES
            and reachability.get(
                "packet_loss_percent"
            ) is not None
        )

    if check_name == "LATENCY":
        return (
            status == "ONLINE"
            and reachability.get("latency_ms") is not None
        )

    if check_name == "CPU_USAGE":
        return (
            api_data.get("available") is True
            and api_data.get("cpu_usage") is not None
        )

    if check_name == "MEMORY_USAGE":
        return (
            api_data.get("available") is True
            and api_data.get("memory_usage") is not None
        )

    if check_name == "INTERFACE_DOWN":
        return bool(
            network_data.get("interfaces")
        )

    return True


def _record_alert_event(
    connection,
    run_id: int,
    alert_key: str,
    device_id: str,
    check_name: str,
    event_type: str,
    severity: str,
    message: str,
    timestamp: str,
    observed_value: Any = None,
    threshold: Any = None,
    metric_unit: str | None = None,
):
    connection.execute(
        """
        INSERT INTO alert_events (
            run_id,
            alert_key,
            device_id,
            check_name,
            event_type,
            severity,
            message,
            created_at,
            observed_value,
            threshold,
            metric_unit
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            alert_key,
            device_id,
            check_name,
            event_type,
            severity,
            message,
            timestamp,
            _serialize_value(observed_value),
            _serialize_value(threshold),
            metric_unit,
        ),
    )


def _insert_metrics_history(
    connection,
    run_id: int,
    device: dict,
    timestamp: str,
):
    """
    Save monitoring metrics for one device per run.

    Missing metrics are stored as NULL, never as zero.
    """

    device_id = device["device_id"]

    reachability = device.get(
        "reachability", {}
    )

    api_data = device.get(
        "api", {}
    )

    connection.execute(
        """
        INSERT INTO device_metrics_history (
            run_id,
            device_id,
            latency_ms,
            packet_loss_percent,
            cpu_usage_percent,
            memory_usage_percent,
            collected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            device_id,
            _to_float(
                reachability.get("latency_ms")
            ),
            _to_float(
                reachability.get("packet_loss_percent")
            ),
            _to_float(
                api_data.get("cpu_usage")
            ) if api_data.get("available") else None,
            _to_float(
                api_data.get("memory_usage")
            ) if api_data.get("available") else None,
            timestamp,
        ),
    )


def _synchronize_alerts(
    connection,
    run_id: int,
    device: dict,
    health_result: dict,
    timestamp: str,
) -> int:

    device_id = device["device_id"]

    current_alerts = health_result.get(
        "alerts", []
    )

    current_map = {}

    for alert in current_alerts:

        check_name = alert.get("check")
        severity = alert.get("severity")

        if not check_name:
            raise ValueError(
                "Alert is missing check name"
            )

        if severity not in ("WARNING", "CRITICAL"):
            raise ValueError(
                f"Invalid alert severity: {severity}"
            )

        alert_key = _make_alert_key(
            device_id,
            check_name,
        )

        # Use an explicit metric unit when available.
        # Otherwise infer it from the check type.
        metric_unit = (
            alert.get("metric_unit")
            or METRIC_UNITS.get(check_name)
        )

        current_map[alert_key] = {
            **alert,
            "metric_unit": metric_unit,
        }

    existing_rows = connection.execute(
        """
        SELECT *
        FROM active_alerts
        WHERE device_id = ?
        """,
        (device_id,),
    ).fetchall()

    event_count = 0

    # Register new alerts or update existing alerts.
    for alert_key, alert in current_map.items():

        check_name = alert["check"]
        severity = alert["severity"]
        message = alert["message"]

        observed_value = alert.get(
            "observed_value"
        )

        threshold = alert.get("threshold")
        metric_unit = alert.get("metric_unit")

        existing = connection.execute(
            """
            SELECT *
            FROM active_alerts
            WHERE alert_key = ?
            """,
            (alert_key,),
        ).fetchone()

        if existing is None:

            connection.execute(
                """
                INSERT INTO active_alerts (
                    alert_key,
                    device_id,
                    check_name,
                    severity,
                    message,
                    first_seen,
                    last_seen,
                    observed_value,
                    threshold,
                    metric_unit
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert_key,
                    device_id,
                    check_name,
                    severity,
                    message,
                    timestamp,
                    timestamp,
                    _serialize_value(observed_value),
                    _serialize_value(threshold),
                    metric_unit,
                ),
            )

            _record_alert_event(
                connection,
                run_id,
                alert_key,
                device_id,
                check_name,
                "RAISED",
                severity,
                message,
                timestamp,
                observed_value,
                threshold,
                metric_unit,
            )

            event_count += 1

        else:

            # Do not notify again merely because a numeric
            # measurement changed while the alert remains active.
            changed = (
                existing["severity"] != severity
                or existing["message"] != message
            )

            connection.execute(
                """
                UPDATE active_alerts
                SET severity = ?,
                    message = ?,
                    last_seen = ?,
                    observed_value = ?,
                    threshold = ?,
                    metric_unit = ?
                WHERE alert_key = ?
                """,
                (
                    severity,
                    message,
                    timestamp,
                    _serialize_value(observed_value),
                    _serialize_value(threshold),
                    metric_unit,
                    alert_key,
                ),
            )

            if changed:

                _record_alert_event(
                    connection,
                    run_id,
                    alert_key,
                    device_id,
                    check_name,
                    "UPDATED",
                    severity,
                    message,
                    timestamp,
                    observed_value,
                    threshold,
                    metric_unit,
                )

                event_count += 1

    # Resolve existing alerts only when their
    # corresponding data source was checked successfully.
    for existing in existing_rows:

        alert_key = existing["alert_key"]

        if alert_key in current_map:
            continue

        check_name = existing["check_name"]

        if not _can_resolve_alert(
            check_name,
            device,
        ):
            continue

        _record_alert_event(
            connection,
            run_id,
            alert_key,
            device_id,
            check_name,
            "RESOLVED",
            existing["severity"],
            f"Alert resolved: {existing['message']}",
            timestamp,
            existing["observed_value"],
            existing["threshold"],
            existing["metric_unit"],
        )

        connection.execute(
            """
            DELETE FROM active_alerts
            WHERE alert_key = ?
            """,
            (alert_key,),
        )

        event_count += 1

    return event_count


def process_monitoring_run(
    normalized_devices: list[dict],
    health_results: list[dict],
    db_path=DEFAULT_DB_PATH,
) -> dict[str, Any]:

    initialize_database(db_path)

    device_map = {}
    health_map = {}

    for device in normalized_devices:

        device_id = device.get("device_id")

        if not device_id:
            raise ValueError(
                "Device is missing device_id"
            )

        if device_id in device_map:
            raise ValueError(
                f"Duplicate device: {device_id}"
            )

        device_map[device_id] = device

    for result in health_results:

        device_id = result.get("device_id")

        if not device_id:
            raise ValueError(
                "Health result is missing device_id"
            )

        if device_id not in device_map:
            raise ValueError(
                f"Unknown health device: {device_id}"
            )

        if device_id in health_map:
            raise ValueError(
                f"Duplicate health result: {device_id}"
            )

        health_map[device_id] = result

    timestamp = _utc_now()

    state_event_count = 0
    alert_event_count = 0

    with get_connection(db_path) as connection:

        cursor = connection.execute(
            """
            INSERT INTO monitoring_runs (checked_at)
            VALUES (?)
            """,
            (timestamp,),
        )

        run_id = cursor.lastrowid

        for device_id, device in device_map.items():

            identity = device.get(
                "identity", {}
            )

            reachability = device.get(
                "reachability", {}
            )

            observed_status = str(
                reachability.get(
                    "status", "UNKNOWN"
                )
            ).upper()

            previous = connection.execute(
                """
                SELECT *
                FROM device_current_state
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()

            previous_status = (
                previous["reachability_status"]
                if previous is not None
                else "UNKNOWN"
            )

            if observed_status in KNOWN_REACHABILITY_STATES:
                effective_status = observed_status
            else:
                effective_status = previous_status

            # Detect only confirmed ONLINE/OFFLINE transitions.
            if (
                previous is not None
                and previous_status in KNOWN_REACHABILITY_STATES
                and observed_status in KNOWN_REACHABILITY_STATES
                and previous_status != observed_status
            ):

                if (
                    previous_status == "ONLINE"
                    and observed_status == "OFFLINE"
                ):
                    event_type = "DEVICE_OFFLINE"
                    message = "Device changed from ONLINE to OFFLINE"

                else:
                    event_type = "DEVICE_RECOVERED"
                    message = "Device changed from OFFLINE to ONLINE"

                connection.execute(
                    """
                    INSERT INTO state_events (
                        run_id,
                        device_id,
                        event_type,
                        previous_status,
                        current_status,
                        message,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        device_id,
                        event_type,
                        previous_status,
                        observed_status,
                        message,
                        timestamp,
                    ),
                )

                state_event_count += 1

            health_result = health_map.get(device_id)

            overall_status = (
                health_result.get(
                    "overall_status", "UNKNOWN"
                )
                if health_result is not None
                else (
                    previous["overall_status"]
                    if previous is not None
                    else "UNKNOWN"
                )
            )

            # Save current state history.
            connection.execute(
                """
                INSERT INTO device_state_history (
                    run_id,
                    device_id,
                    observed_reachability_status,
                    effective_reachability_status,
                    overall_status,
                    checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    device_id,
                    observed_status,
                    effective_status,
                    overall_status,
                    timestamp,
                ),
            )

            # Save current device state.
            connection.execute(
                """
                INSERT INTO device_current_state (
                    device_id,
                    hostname,
                    ip_address,
                    reachability_status,
                    overall_status,
                    first_seen,
                    last_checked_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(device_id)
                DO UPDATE SET
                    hostname = excluded.hostname,
                    ip_address = excluded.ip_address,
                    reachability_status = excluded.reachability_status,
                    overall_status = excluded.overall_status,
                    last_checked_at = excluded.last_checked_at
                """,
                (
                    device_id,
                    identity.get("hostname"),
                    identity.get("ip_address"),
                    effective_status,
                    overall_status,
                    timestamp,
                    timestamp,
                ),
            )

            # Save metrics every monitoring run.
            _insert_metrics_history(
                connection,
                run_id,
                device,
                timestamp,
            )

            # Do not clear alerts if Health Check is missing.
            if health_result is not None:

                alert_event_count += _synchronize_alerts(
                    connection,
                    run_id,
                    device,
                    health_result,
                    timestamp,
                )

    return {
        "run_id": run_id,
        "checked_devices": len(device_map),
        "state_event_count": state_event_count,
        "alert_event_count": alert_event_count,
        "checked_at": timestamp,
    }
