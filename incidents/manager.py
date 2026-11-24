import json
import uuid
from typing import Any

from storage.database import (
    DEFAULT_DB_PATH,
    get_connection,
    initialize_database,
)


SEVERITY_PRIORITY = {
    "HEALTHY": 0,
    "INFO": 0,
    "WARNING": 1,
    "CRITICAL": 2,
}


def _new_incident_id() -> str:
    """Return a readable, collision-resistant Incident identifier."""
    return f"INC-{uuid.uuid4().hex[:10].upper()}"


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _append_incident_event(
    connection,
    *,
    incident_id: str | None,
    source_alert_event_id: int,
    alert_key: str,
    event_type: str,
    previous_severity: str | None,
    current_severity: str | None,
    message: str,
    payload: dict[str, Any],
    created_at: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO incident_events (
            incident_id,
            source_alert_event_id,
            alert_key,
            event_type,
            previous_severity,
            current_severity,
            message,
            payload_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            incident_id,
            source_alert_event_id,
            alert_key,
            event_type,
            previous_severity,
            current_severity,
            message,
            _json_dump(payload),
            created_at,
        ),
    )
    return int(cursor.lastrowid)


def _enqueue_incident_event(
    connection,
    *,
    incident_event_id: int,
    event_type: str,
    incident_id: str,
    payload: dict[str, Any],
    created_at: str,
) -> str:
    event_id = _new_event_id()
    envelope = {
        "event_id": event_id,
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": created_at,
        "incident": payload,
    }

    connection.execute(
        """
        INSERT INTO agent_outbox (
            event_id,
            incident_event_id,
            event_type,
            aggregate_type,
            aggregate_id,
            schema_version,
            payload_json,
            delivery_status,
            attempt_count,
            next_attempt_at,
            created_at
        )
        VALUES (?, ?, ?, 'INCIDENT', ?, 1, ?, 'PENDING', 0, ?, ?)
        """,
        (
            event_id,
            incident_event_id,
            event_type,
            incident_id,
            _json_dump(envelope),
            created_at,
            created_at,
        ),
    )
    return event_id


def synchronize_incidents(
    db_path=DEFAULT_DB_PATH,
    batch_size: int = 500,
) -> dict[str, int]:
    """
    Project persisted alert_events into Incident lifecycle records.

    Each source alert event is processed at most once. The Incident update,
    Incident Event, and Outbox event are committed in one SQLite transaction.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    initialize_database(db_path)

    counts = {
        "processed_alert_events": 0,
        "incidents_created": 0,
        "incidents_updated": 0,
        "incidents_resolved": 0,
        "outbox_events_created": 0,
        "orphan_events": 0,
    }

    with get_connection(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")

        pending = connection.execute(
            """
            SELECT ae.*
            FROM alert_events AS ae
            LEFT JOIN incident_events AS ie
                ON ie.source_alert_event_id = ae.event_id
            WHERE ie.source_alert_event_id IS NULL
            ORDER BY ae.event_id
            LIMIT ?
            """,
            (batch_size,),
        ).fetchall()

        for source in pending:
            source_id = int(source["event_id"])
            alert_key = source["alert_key"]
            device_id = source["device_id"]
            check_name = source["check_name"]
            source_type = str(source["event_type"]).upper()
            severity = str(source["severity"]).upper()
            message = source["message"]
            occurred_at = source["created_at"]

            current = connection.execute(
                """
                SELECT *
                FROM incidents
                WHERE alert_key = ?
                  AND status = 'OPEN'
                """,
                (alert_key,),
            ).fetchone()

            incident_id: str | None = None
            incident_event_type: str
            outbox_type: str | None = None
            previous_severity: str | None = None
            event_message = message
            reconciled = False

            if source_type == "RAISED":
                if current is None:
                    incident_id = _new_incident_id()
                    title = f"{check_name} on {device_id}"

                    connection.execute(
                        """
                        INSERT INTO incidents (
                            incident_id,
                            alert_key,
                            device_id,
                            check_name,
                            severity,
                            status,
                            title,
                            summary,
                            first_seen_at,
                            last_seen_at,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            incident_id,
                            alert_key,
                            device_id,
                            check_name,
                            severity,
                            title,
                            message,
                            occurred_at,
                            occurred_at,
                            occurred_at,
                            occurred_at,
                        ),
                    )
                    incident_event_type = "CREATED"
                    outbox_type = "incident.created"
                    counts["incidents_created"] += 1
                else:
                    incident_id = current["incident_id"]
                    previous_severity = current["severity"]
                    incident_event_type = "UPDATED"
                    outbox_type = "incident.updated"
                    connection.execute(
                        """
                        UPDATE incidents
                        SET severity = ?,
                            summary = ?,
                            last_seen_at = ?,
                            updated_at = ?
                        WHERE incident_id = ?
                        """,
                        (
                            severity,
                            message,
                            occurred_at,
                            occurred_at,
                            incident_id,
                        ),
                    )
                    counts["incidents_updated"] += 1

            elif source_type == "UPDATED":
                if current is None:
                    # Reconcile an existing alert if its original RAISED
                    # event was not present in the retained event history.
                    incident_id = _new_incident_id()
                    title = f"{check_name} on {device_id}"
                    reconciled = True
                    incident_event_type = "CREATED"
                    outbox_type = "incident.created"

                    connection.execute(
                        """
                        INSERT INTO incidents (
                            incident_id,
                            alert_key,
                            device_id,
                            check_name,
                            severity,
                            status,
                            title,
                            summary,
                            first_seen_at,
                            last_seen_at,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            incident_id,
                            alert_key,
                            device_id,
                            check_name,
                            severity,
                            title,
                            message,
                            occurred_at,
                            occurred_at,
                            occurred_at,
                            occurred_at,
                        ),
                    )
                    counts["incidents_created"] += 1
                else:
                    incident_id = current["incident_id"]
                    previous_severity = current["severity"]
                    previous_rank = SEVERITY_PRIORITY.get(previous_severity, 0)
                    current_rank = SEVERITY_PRIORITY.get(severity, 0)

                    if current_rank > previous_rank:
                        incident_event_type = "SEVERITY_ESCALATED"
                        outbox_type = "incident.severity_escalated"
                    else:
                        incident_event_type = "UPDATED"
                        outbox_type = "incident.updated"

                    connection.execute(
                        """
                        UPDATE incidents
                        SET severity = ?,
                            summary = ?,
                            last_seen_at = ?,
                            updated_at = ?
                        WHERE incident_id = ?
                        """,
                        (
                            severity,
                            message,
                            occurred_at,
                            occurred_at,
                            incident_id,
                        ),
                    )
                    counts["incidents_updated"] += 1

            elif source_type == "RESOLVED":
                if current is None:
                    incident_event_type = "ORPHAN_RESOLUTION"
                    event_message = (
                        "Resolution event has no matching open Incident"
                    )
                    counts["orphan_events"] += 1
                else:
                    incident_id = current["incident_id"]
                    previous_severity = current["severity"]
                    incident_event_type = "RESOLVED"
                    outbox_type = "incident.resolved"

                    connection.execute(
                        """
                        UPDATE incidents
                        SET status = 'RESOLVED',
                            severity = ?,
                            summary = ?,
                            last_seen_at = ?,
                            resolved_at = ?,
                            updated_at = ?
                        WHERE incident_id = ?
                        """,
                        (
                            severity,
                            message,
                            occurred_at,
                            occurred_at,
                            occurred_at,
                            incident_id,
                        ),
                    )
                    counts["incidents_resolved"] += 1

            else:
                # Preserve traceability for an unexpected source event
                # without creating an unsupported Incident transition.
                incident_id = current["incident_id"] if current else None
                previous_severity = current["severity"] if current else None
                incident_event_type = "IGNORED_SOURCE_EVENT"
                event_message = f"Unsupported alert event type: {source_type}"
                counts["orphan_events"] += 1

            incident_payload = {
                "incident_id": incident_id,
                "alert_key": alert_key,
                "device_id": device_id,
                "check_name": check_name,
                "severity": severity,
                "status": (
                    "RESOLVED"
                    if source_type == "RESOLVED" and incident_id
                    else "OPEN" if incident_id else None
                ),
                "title": f"{check_name} on {device_id}",
                "summary": message,
                "source_alert_event_id": source_id,
                "source_event_type": source_type,
                "observed_value": source["observed_value"],
                "threshold": source["threshold"],
                "metric_unit": source["metric_unit"],
                "reconciled": reconciled,
            }

            incident_event_id = _append_incident_event(
                connection,
                incident_id=incident_id,
                source_alert_event_id=source_id,
                alert_key=alert_key,
                event_type=incident_event_type,
                previous_severity=previous_severity,
                current_severity=severity,
                message=event_message,
                payload=incident_payload,
                created_at=occurred_at,
            )

            if incident_id and outbox_type:
                _enqueue_incident_event(
                    connection,
                    incident_event_id=incident_event_id,
                    event_type=outbox_type,
                    incident_id=incident_id,
                    payload=incident_payload,
                    created_at=occurred_at,
                )
                counts["outbox_events_created"] += 1

            counts["processed_alert_events"] += 1

    return counts
