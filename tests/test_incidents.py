import json
import sqlite3
from datetime import datetime, timedelta, timezone

from incidents.manager import synchronize_incidents
from incidents.outbox import (
    claim_pending_events,
    dispatch_pending_events,
    mark_event_delivered,
    mark_event_failed,
)
from storage.database import get_connection, initialize_database


def insert_alert_event(
    db_path,
    *,
    alert_key="router-01::CPU_USAGE",
    device_id="router-01",
    check_name="CPU_USAGE",
    event_type="RAISED",
    severity="CRITICAL",
    message="CPU usage reached critical threshold",
    created_at=None,
):
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    with get_connection(db_path) as connection:
        run_cursor = connection.execute(
            "INSERT INTO monitoring_runs (checked_at) VALUES (?)",
            (created_at,),
        )
        run_id = run_cursor.lastrowid

        cursor = connection.execute(
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
                created_at,
                json.dumps(95),
                json.dumps(90),
                "%",
            ),
        )
        return int(cursor.lastrowid)


def read_rows(db_path, table):
    allowed = {
        "incidents",
        "incident_events",
        "agent_outbox",
        "alert_events",
    }
    if table not in allowed:
        raise ValueError("Unsupported test table")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
        ]


def test_initialize_database_adds_incident_tables_without_removing_old_tables(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {
        "monitoring_runs",
        "device_current_state",
        "alert_events",
        "device_metrics_history",
        "incidents",
        "incident_events",
        "agent_outbox",
    }.issubset(tables)


def test_legacy_database_migration_preserves_rows_and_adds_columns(tmp_path):
    db_path = tmp_path / "legacy.db"

    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE monitoring_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at TEXT NOT NULL
            );

            CREATE TABLE active_alerts (
                alert_key TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                check_name TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE alert_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                alert_key TEXT NOT NULL,
                device_id TEXT NOT NULL,
                check_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE device_metrics_history (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                device_id TEXT NOT NULL
            );

            INSERT INTO active_alerts (
                alert_key, device_id, check_name, severity,
                message, first_seen, last_seen
            )
            VALUES (
                'router-01::CPU_USAGE', 'router-01', 'CPU_USAGE',
                'CRITICAL', 'CPU critical', '2026-11-24T01:00:00+00:00',
                '2026-11-24T01:05:00+00:00'
            );
            """
        )

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row

        alert = connection.execute(
            "SELECT * FROM active_alerts WHERE alert_key = ?",
            ("router-01::CPU_USAGE",),
        ).fetchone()

        active_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(active_alerts)"
            )
        }
        event_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(alert_events)"
            )
        }
        metric_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(device_metrics_history)"
            )
        }

    assert alert["message"] == "CPU critical"
    assert {"observed_value", "threshold", "metric_unit"} <= active_columns
    assert {"observed_value", "threshold", "metric_unit"} <= event_columns
    assert {
        "latency_ms",
        "packet_loss_percent",
        "cpu_usage_percent",
        "memory_usage_percent",
        "collected_at",
    } <= metric_columns


def test_raised_alert_creates_incident_and_outbox_event(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path)

    result = synchronize_incidents(db_path)

    incidents = read_rows(db_path, "incidents")
    incident_events = read_rows(db_path, "incident_events")
    outbox = read_rows(db_path, "agent_outbox")

    assert result["incidents_created"] == 1
    assert result["outbox_events_created"] == 1
    assert len(incidents) == 1
    assert incidents[0]["status"] == "OPEN"
    assert incidents[0]["device_id"] == "router-01"
    assert len(incident_events) == 1
    assert incident_events[0]["event_type"] == "CREATED"
    assert len(outbox) == 1
    assert outbox[0]["delivery_status"] == "PENDING"
    assert outbox[0]["event_type"] == "incident.created"


def test_updated_alert_escalates_same_incident(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path, severity="WARNING")
    synchronize_incidents(db_path)

    insert_alert_event(
        db_path,
        event_type="UPDATED",
        severity="CRITICAL",
        message="CPU usage escalated to critical",
        created_at="2026-11-24T10:05:00+00:00",
    )
    result = synchronize_incidents(db_path)

    incidents = read_rows(db_path, "incidents")
    events = read_rows(db_path, "incident_events")
    outbox = read_rows(db_path, "agent_outbox")

    assert result["incidents_updated"] == 1
    assert len(incidents) == 1
    assert incidents[0]["severity"] == "CRITICAL"
    assert events[-1]["event_type"] == "SEVERITY_ESCALATED"
    assert outbox[-1]["event_type"] == "incident.severity_escalated"
    assert outbox[-1]["aggregate_id"] == incidents[0]["incident_id"]


def test_resolved_alert_resolves_incident_and_emits_event(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path)
    synchronize_incidents(db_path)

    insert_alert_event(
        db_path,
        event_type="RESOLVED",
        severity="CRITICAL",
        message="CPU usage returned to normal",
        created_at="2026-11-24T10:10:00+00:00",
    )
    result = synchronize_incidents(db_path)

    incidents = read_rows(db_path, "incidents")
    events = read_rows(db_path, "incident_events")
    outbox = read_rows(db_path, "agent_outbox")

    assert result["incidents_resolved"] == 1
    assert incidents[0]["status"] == "RESOLVED"
    assert incidents[0]["resolved_at"] == "2026-11-24T10:10:00+00:00"
    assert events[-1]["event_type"] == "RESOLVED"
    assert outbox[-1]["event_type"] == "incident.resolved"


def test_same_alert_episode_is_not_projected_twice(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path)

    first = synchronize_incidents(db_path)
    second = synchronize_incidents(db_path)

    assert first["processed_alert_events"] == 1
    assert second["processed_alert_events"] == 0
    assert len(read_rows(db_path, "incidents")) == 1
    assert len(read_rows(db_path, "incident_events")) == 1
    assert len(read_rows(db_path, "agent_outbox")) == 1


def test_new_raise_after_resolution_creates_new_incident_episode(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)

    insert_alert_event(db_path, created_at="2026-11-24T10:00:00+00:00")
    synchronize_incidents(db_path)

    insert_alert_event(
        db_path,
        event_type="RESOLVED",
        created_at="2026-11-24T10:05:00+00:00",
    )
    synchronize_incidents(db_path)

    insert_alert_event(
        db_path,
        event_type="RAISED",
        created_at="2026-11-24T10:10:00+00:00",
    )
    synchronize_incidents(db_path)

    incidents = read_rows(db_path, "incidents")
    assert len(incidents) == 2
    assert [row["status"] for row in incidents] == ["RESOLVED", "OPEN"]
    assert incidents[0]["incident_id"] != incidents[1]["incident_id"]


def test_orphan_resolution_is_recorded_without_creating_incident(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(
        db_path,
        event_type="RESOLVED",
    )

    result = synchronize_incidents(db_path)

    assert result["orphan_events"] == 1
    assert read_rows(db_path, "incidents") == []
    event = read_rows(db_path, "incident_events")[0]
    assert event["event_type"] == "ORPHAN_RESOLUTION"
    assert event["incident_id"] is None
    assert read_rows(db_path, "agent_outbox") == []


def test_outbox_dispatch_marks_event_delivered(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path)
    synchronize_incidents(db_path)

    sent = []

    result = dispatch_pending_events(
        sender=sent.append,
        db_path=db_path,
    )

    assert result == {
        "claimed": 1,
        "delivered": 1,
        "retried": 0,
        "dead_lettered": 0,
        "stale_claims": 0,
    }
    assert len(sent) == 1
    assert sent[0]["event_type"] == "incident.created"
    assert read_rows(db_path, "agent_outbox")[0]["delivery_status"] == "DELIVERED"


def test_outbox_failure_schedules_retry(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path)
    synchronize_incidents(db_path)

    def failing_sender(_event):
        raise ConnectionError("Agent Service is unavailable")

    result = dispatch_pending_events(
        sender=failing_sender,
        db_path=db_path,
        max_attempts=3,
    )

    row = read_rows(db_path, "agent_outbox")[0]
    assert result["retried"] == 1
    assert row["delivery_status"] == "PENDING"
    assert row["attempt_count"] == 1
    assert "Agent Service is unavailable" in row["last_error"]


def test_outbox_lease_prevents_old_worker_acknowledgement(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path)
    synchronize_incidents(db_path)

    start = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
    first_claim = claim_pending_events(
        db_path,
        now=start,
        lease_seconds=30,
    )
    assert len(first_claim) == 1

    before_expiry = claim_pending_events(
        db_path,
        now=start + timedelta(seconds=20),
        lease_seconds=30,
    )
    assert before_expiry == []

    after_expiry = claim_pending_events(
        db_path,
        now=start + timedelta(seconds=31),
        lease_seconds=30,
    )
    assert len(after_expiry) == 1
    assert after_expiry[0]["claim_token"] != first_claim[0]["claim_token"]

    stale_ack = mark_event_delivered(
        first_claim[0]["event_id"],
        first_claim[0]["claim_token"],
        db_path,
        now=start + timedelta(seconds=32),
    )
    assert stale_ack is False

    current_ack = mark_event_delivered(
        after_expiry[0]["event_id"],
        after_expiry[0]["claim_token"],
        db_path,
        now=start + timedelta(seconds=33),
    )
    assert current_ack is True
    assert read_rows(db_path, "agent_outbox")[0]["delivery_status"] == "DELIVERED"


def test_outbox_moves_to_dead_letter_after_max_attempts(tmp_path):
    db_path = tmp_path / "monitoring.db"
    initialize_database(db_path)
    insert_alert_event(db_path)
    synchronize_incidents(db_path)

    claim = claim_pending_events(db_path)[0]
    status = mark_event_failed(
        claim["event_id"],
        claim["claim_token"],
        "permanent test failure",
        db_path,
        max_attempts=1,
    )

    assert status == "DEAD_LETTER"
    row = read_rows(db_path, "agent_outbox")[0]
    assert row["delivery_status"] == "DEAD_LETTER"
    assert row["attempt_count"] == 1
