import json
import uuid

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from storage.database import (
    DEFAULT_DB_PATH,
    get_connection,
    initialize_database,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def claim_pending_events(
    db_path=DEFAULT_DB_PATH,
    *,
    limit: int = 25,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Atomically claim due Outbox events for one dispatcher worker.

    Expired IN_FLIGHT leases are reclaimable. Each claim receives a unique
    token so an older worker cannot acknowledge a newer worker's claim.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be at least 1")

    initialize_database(db_path)
    current_time = now or _utc_now()
    current_iso = _iso(current_time)
    locked_until = _iso(
        current_time + timedelta(seconds=lease_seconds)
    )

    claimed: list[dict[str, Any]] = []

    with get_connection(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")

        rows = connection.execute(
            """
            SELECT event_id
            FROM agent_outbox
            WHERE (
                delivery_status = 'PENDING'
                AND next_attempt_at <= ?
            )
            OR (
                delivery_status = 'IN_FLIGHT'
                AND locked_until IS NOT NULL
                AND locked_until <= ?
            )
            ORDER BY created_at, event_id
            LIMIT ?
            """,
            (current_iso, current_iso, limit),
        ).fetchall()

        for row in rows:
            event_id = row["event_id"]
            claim_token = str(uuid.uuid4())

            connection.execute(
                """
                UPDATE agent_outbox
                SET delivery_status = 'IN_FLIGHT',
                    attempt_count = attempt_count + 1,
                    locked_until = ?,
                    claim_token = ?
                WHERE event_id = ?
                """,
                (locked_until, claim_token, event_id),
            )

            claimed_row = connection.execute(
                """
                SELECT *
                FROM agent_outbox
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()

            item = dict(claimed_row)
            item["claim_token"] = claim_token
            item["payload"] = json.loads(item["payload_json"])
            claimed.append(item)

    return claimed


def mark_event_delivered(
    event_id: str,
    claim_token: str,
    db_path=DEFAULT_DB_PATH,
    *,
    now: datetime | None = None,
) -> bool:
    """Acknowledge an event only for the worker holding its current lease."""
    timestamp = _iso(now or _utc_now())

    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE agent_outbox
            SET delivery_status = 'DELIVERED',
                delivered_at = ?,
                locked_until = NULL,
                claim_token = NULL,
                last_error = NULL
            WHERE event_id = ?
              AND delivery_status = 'IN_FLIGHT'
              AND claim_token = ?
            """,
            (timestamp, event_id, claim_token),
        )
        return cursor.rowcount == 1


def mark_event_failed(
    event_id: str,
    claim_token: str,
    error: str,
    db_path=DEFAULT_DB_PATH,
    *,
    max_attempts: int = 8,
    base_delay_seconds: int = 5,
    max_delay_seconds: int = 900,
    now: datetime | None = None,
) -> str:
    """
    Schedule a retry with capped exponential backoff or dead-letter the event.

    Returns the new delivery status, or STALE_CLAIM if the lease no longer
    belongs to this worker.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    if base_delay_seconds < 1 or max_delay_seconds < 1:
        raise ValueError("Retry delays must be at least 1 second")

    current_time = now or _utc_now()
    timestamp = _iso(current_time)
    safe_error = (error or "Unknown delivery error").strip()[:1000]

    with get_connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT attempt_count
            FROM agent_outbox
            WHERE event_id = ?
              AND delivery_status = 'IN_FLIGHT'
              AND claim_token = ?
            """,
            (event_id, claim_token),
        ).fetchone()

        if row is None:
            return "STALE_CLAIM"

        attempts = int(row["attempt_count"])

        if attempts >= max_attempts:
            status = "DEAD_LETTER"
            next_attempt_at = timestamp
        else:
            status = "PENDING"
            delay = min(
                base_delay_seconds * (2 ** max(attempts - 1, 0)),
                max_delay_seconds,
            )
            next_attempt_at = _iso(
                current_time + timedelta(seconds=delay)
            )

        connection.execute(
            """
            UPDATE agent_outbox
            SET delivery_status = ?,
                next_attempt_at = ?,
                locked_until = NULL,
                claim_token = NULL,
                last_error = ?
            WHERE event_id = ?
              AND delivery_status = 'IN_FLIGHT'
              AND claim_token = ?
            """,
            (
                status,
                next_attempt_at,
                safe_error,
                event_id,
                claim_token,
            ),
        )

    return status


def dispatch_pending_events(
    sender: Callable[[dict[str, Any]], Any],
    db_path=DEFAULT_DB_PATH,
    *,
    limit: int = 25,
    lease_seconds: int = 60,
    max_attempts: int = 8,
) -> dict[str, int]:
    """
    Deliver a claimed batch using an injected sender.

    The sender is responsible for authenticated HTTP delivery to the Agent
    Service. It must use event_id as an idempotency key. This module owns
    durable claiming, retries, and acknowledgements.
    """
    if not callable(sender):
        raise TypeError("sender must be callable")

    events = claim_pending_events(
        db_path,
        limit=limit,
        lease_seconds=lease_seconds,
    )

    result = {
        "claimed": len(events),
        "delivered": 0,
        "retried": 0,
        "dead_lettered": 0,
        "stale_claims": 0,
    }

    for event in events:
        try:
            sender({
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "schema_version": event["schema_version"],
                "aggregate_type": event["aggregate_type"],
                "aggregate_id": event["aggregate_id"],
                "payload": event["payload"],
            })
        except Exception as exc:
            status = mark_event_failed(
                event["event_id"],
                event["claim_token"],
                f"{type(exc).__name__}: {exc}",
                db_path,
                max_attempts=max_attempts,
            )
            if status == "PENDING":
                result["retried"] += 1
            elif status == "DEAD_LETTER":
                result["dead_lettered"] += 1
            else:
                result["stale_claims"] += 1
        else:
            acknowledged = mark_event_delivered(
                event["event_id"],
                event["claim_token"],
                db_path,
            )
            if acknowledged:
                result["delivered"] += 1
            else:
                result["stale_claims"] += 1

    return result
