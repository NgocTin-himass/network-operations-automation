import json
import sqlite3

import pytest

from storage.database import initialize_database
from storage.state_tracker import process_monitoring_run


def create_device(
    status="ONLINE",
    api_available=True,
    cpu=30,
    memory=50,
):
    return {
        "device_id": "router-01",

        "identity": {
            "hostname": "Router-01",
            "ip_address": "192.0.2.10",
        },

        "reachability": {
            "status": status,
            "latency_ms": 25 if status == "ONLINE" else None,
            "packet_loss_percent": (
                0 if status == "ONLINE"
                else 100 if status == "OFFLINE"
                else None
            ),
        },

        "api": {
            "available": api_available,
            "cpu_usage": cpu if api_available else None,
            "memory_usage": memory if api_available else None,
        },

        "ssh": {
            "status": "CONNECTED",
        },

        "network": {
            "interfaces": [],
            "routes": [],
        },
    }


def create_health(status="HEALTHY", alerts=None):
    return {
        "device_id": "router-01",
        "hostname": "Router-01",
        "overall_status": status,
        "alert_count": len(alerts or []),
        "alerts": alerts or [],
    }


def offline_alert():
    return {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
        "check": "DEVICE_UNREACHABLE",
        "severity": "CRITICAL",
        "message": "Device is not reachable",
    }


def cpu_alert():
    return {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
        "check": "CPU_USAGE",
        "severity": "CRITICAL",
        "message": "CPU usage reached critical threshold",
    }


def read_table(db_path, table):
    allowed_tables = {
        "monitoring_runs",
        "device_current_state",
        "device_state_history",
        "state_events",
        "active_alerts",
        "alert_events",
         "device_metrics_history",
    }

    if table not in allowed_tables:
        raise ValueError("Invalid table")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            f"SELECT * FROM {table}"
        ).fetchall()

        return [dict(row) for row in rows]


def test_database_initialization(tmp_path):

    db_path = tmp_path / "test.db"

    initialize_database(db_path)

    tables = {
        row["name"]
        for row in read_table_names(db_path)
    }

    assert "monitoring_runs" in tables
    assert "device_current_state" in tables
    assert "active_alerts" in tables
    assert "alert_events" in tables


def read_table_names(db_path):

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row

        return connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()


def test_online_to_offline_transition(tmp_path):

    db_path = tmp_path / "test.db"

    process_monitoring_run(
        [create_device("ONLINE")],
        [create_health()],
        db_path,
    )

    process_monitoring_run(
        [create_device("OFFLINE")],
        [
            create_health(
                "CRITICAL",
                [offline_alert()],
            )
        ],
        db_path,
    )

    events = read_table(db_path, "state_events")

    assert len(events) == 1
    assert events[0]["event_type"] == "DEVICE_OFFLINE"
    assert events[0]["previous_status"] == "ONLINE"
    assert events[0]["current_status"] == "OFFLINE"


def test_offline_to_online_recovery(tmp_path):

    db_path = tmp_path / "test.db"

    process_monitoring_run(
        [create_device("OFFLINE")],
        [
            create_health(
                "CRITICAL",
                [offline_alert()],
            )
        ],
        db_path,
    )

    process_monitoring_run(
        [create_device("ONLINE")],
        [create_health()],
        db_path,
    )

    state_events = read_table(db_path, "state_events")
    alert_events = read_table(db_path, "alert_events")
    active_alerts = read_table(db_path, "active_alerts")

    assert state_events[-1]["event_type"] == "DEVICE_RECOVERED"

    assert any(
        event["event_type"] == "RESOLVED"
        for event in alert_events
    )

    assert len(active_alerts) == 0


def test_repeated_alert_is_not_duplicated(tmp_path):

    db_path = tmp_path / "test.db"

    for _ in range(3):

        process_monitoring_run(
            [create_device("OFFLINE")],
            [
                create_health(
                    "CRITICAL",
                    [offline_alert()],
                )
            ],
            db_path,
        )

    events = read_table(db_path, "alert_events")
    active = read_table(db_path, "active_alerts")

    assert len(events) == 1
    assert events[0]["event_type"] == "RAISED"
    assert len(active) == 1


def test_collection_error_does_not_clear_offline_alert(tmp_path):

    db_path = tmp_path / "test.db"

    process_monitoring_run(
        [create_device("OFFLINE")],
        [
            create_health(
                "CRITICAL",
                [offline_alert()],
            )
        ],
        db_path,
    )

    error_alert = {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
        "check": "PING_COLLECTION_ERROR",
        "severity": "WARNING",
        "message": "Unable to complete ping check",
    }

    process_monitoring_run(
        [create_device("ERROR")],
        [
            create_health(
                "WARNING",
                [error_alert],
            )
        ],
        db_path,
    )

    active = read_table(db_path, "active_alerts")

    assert any(
        alert["check_name"] == "DEVICE_UNREACHABLE"
        for alert in active
    )


def test_unavailable_api_does_not_clear_cpu_alert(tmp_path):

    db_path = tmp_path / "test.db"

    process_monitoring_run(
        [
            create_device(
                "ONLINE",
                api_available=True,
                cpu=95,
            )
        ],
        [
            create_health(
                "CRITICAL",
                [cpu_alert()],
            )
        ],
        db_path,
    )

    process_monitoring_run(
        [
            create_device(
                "ONLINE",
                api_available=False,
            )
        ],
        [create_health()],
        db_path,
    )

    active = read_table(db_path, "active_alerts")

def test_device_metrics_are_saved(tmp_path):

    db_path = tmp_path / "metrics.db"

    device = create_device(
        status="ONLINE",
        cpu=95,
        memory=62,
    )

    device["reachability"]["latency_ms"] = 350
    device["reachability"]["packet_loss_percent"] = 25

    process_monitoring_run(
        [device],
        [create_health()],
        db_path,
    )

    metrics = read_table(
        db_path,
        "device_metrics_history",
    )

    assert len(metrics) == 1

    row = metrics[0]

    assert row["latency_ms"] == 350
    assert row["packet_loss_percent"] == 25
    assert row["cpu_usage_percent"] == 95
    assert row["memory_usage_percent"] == 62


def test_alert_metric_details_are_saved(tmp_path):

    db_path = tmp_path / "alert_metrics.db"

    device = create_device(
        status="ONLINE",
        cpu=95,
        memory=62,
    )

    alert = {
        **cpu_alert(),
        "observed_value": 95,
        "threshold": 90,
        "metric_unit": "%",
    }

    process_monitoring_run(
        [device],
        [
            create_health(
                "CRITICAL",
                [alert],
            )
        ],
        db_path,
    )

    active = read_table(db_path, "active_alerts")
    events = read_table(db_path, "alert_events")

    assert len(active) == 1
    assert len(events) == 1

    assert json.loads(active[0]["observed_value"]) == 95
    assert json.loads(active[0]["threshold"]) == 90
    assert active[0]["metric_unit"] == "%"

    assert json.loads(events[0]["observed_value"]) == 95
    assert json.loads(events[0]["threshold"]) == 90
    assert events[0]["event_type"] == "RAISED"
def test_monitoring_history_is_saved(tmp_path):

    db_path = tmp_path / "history.db"

    for _ in range(3):

        process_monitoring_run(
            [create_device("ONLINE")],
            [create_health()],
            db_path,
        )

    history = read_table(
        db_path,
        "device_state_history",
    )

    runs = read_table(
        db_path,
        "monitoring_runs",
    )

    assert len(history) == 3
    assert len(runs) == 3
