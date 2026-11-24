import sqlite3

from contextlib import contextmanager
from pathlib import Path


# =====================================================
# DATABASE CONFIGURATION
# =====================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DB_PATH = (
    PROJECT_ROOT
    / "data"
    / "network_monitoring.db"
)


# =====================================================
# DATABASE SCHEMA
# =====================================================

SCHEMA = """
-- 1. Monitoring Runs
CREATE TABLE IF NOT EXISTS monitoring_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL
);

-- 2. Current Device State
CREATE TABLE IF NOT EXISTS device_current_state (
    device_id TEXT PRIMARY KEY,
    hostname TEXT,
    ip_address TEXT,
    reachability_status TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_checked_at TEXT NOT NULL
);

-- 3. Device State History
CREATE TABLE IF NOT EXISTS device_state_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    observed_reachability_status TEXT NOT NULL,
    effective_reachability_status TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES monitoring_runs(run_id)
);

-- 4. State Events
CREATE TABLE IF NOT EXISTS state_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    current_status TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES monitoring_runs(run_id)
);

-- 5. Active Alerts
CREATE TABLE IF NOT EXISTS active_alerts (
    alert_key TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    observed_value TEXT,
    threshold TEXT,
    metric_unit TEXT
);

-- 6. Alert Events
CREATE TABLE IF NOT EXISTS alert_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    alert_key TEXT NOT NULL,
    device_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    observed_value TEXT,
    threshold TEXT,
    metric_unit TEXT,
    FOREIGN KEY (run_id) REFERENCES monitoring_runs(run_id)
);

-- 7. Device Metrics History
CREATE TABLE IF NOT EXISTS device_metrics_history (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    latency_ms REAL,
    packet_loss_percent REAL,
    cpu_usage_percent REAL,
    memory_usage_percent REAL,
    collected_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES monitoring_runs(run_id)
);

-- Existing indexes
CREATE INDEX IF NOT EXISTS idx_history_device
ON device_state_history(device_id);

CREATE INDEX IF NOT EXISTS idx_state_events_device
ON state_events(device_id);

CREATE INDEX IF NOT EXISTS idx_alert_events_device
ON alert_events(device_id);

CREATE INDEX IF NOT EXISTS idx_metrics_device
ON device_metrics_history(device_id);

CREATE INDEX IF NOT EXISTS idx_metrics_run
ON device_metrics_history(run_id);

CREATE INDEX IF NOT EXISTS idx_metrics_collected
ON device_metrics_history(collected_at);

-- 8. Incidents: one record per alert episode.
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    alert_key TEXT NOT NULL,
    device_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('OPEN', 'RESOLVED', 'CLOSED')
    ),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Only one unresolved Incident may exist for an alert key.
CREATE UNIQUE INDEX IF NOT EXISTS uq_incidents_open_alert_key
ON incidents(alert_key)
WHERE status = 'OPEN';

CREATE INDEX IF NOT EXISTS idx_incidents_device_status
ON incidents(device_id, status);

CREATE INDEX IF NOT EXISTS idx_incidents_created_at
ON incidents(created_at);

-- 9. Incident Events: immutable lifecycle history.
CREATE TABLE IF NOT EXISTS incident_events (
    incident_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT,
    source_alert_event_id INTEGER NOT NULL UNIQUE,
    alert_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    previous_severity TEXT,
    current_severity TEXT,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_id) REFERENCES incidents(incident_id),
    FOREIGN KEY (source_alert_event_id) REFERENCES alert_events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_incident_events_incident
ON incident_events(incident_id, created_at);

-- 10. Transactional Outbox: reliable delivery to the Agent Service.
CREATE TABLE IF NOT EXISTS agent_outbox (
    event_id TEXT PRIMARY KEY,
    incident_event_id INTEGER NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL DEFAULT 'INCIDENT',
    aggregate_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (
            delivery_status IN (
                'PENDING',
                'IN_FLIGHT',
                'DELIVERED',
                'DEAD_LETTER'
            )
        ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    last_error TEXT,
    FOREIGN KEY (incident_event_id)
        REFERENCES incident_events(incident_event_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_outbox_pending
ON agent_outbox(delivery_status, next_attempt_at, created_at);
"""


# =====================================================
# DATABASE MIGRATION
# =====================================================

def migrate_schema(connection):
    """
    Upgrade an existing database without deleting records.

    Existing column migrations are additive. New tables and indexes are
    created by SCHEMA using CREATE TABLE/INDEX IF NOT EXISTS.
    """
    migrations = {
        "active_alerts": {
            "observed_value": "TEXT",
            "threshold": "TEXT",
            "metric_unit": "TEXT",
        },
        "alert_events": {
            "observed_value": "TEXT",
            "threshold": "TEXT",
            "metric_unit": "TEXT",
        },
        "device_metrics_history": {
            "latency_ms": "REAL",
            "packet_loss_percent": "REAL",
            "cpu_usage_percent": "REAL",
            "memory_usage_percent": "REAL",
            "collected_at": "TEXT",
        },
    }

    for table, columns in migrations.items():
        existing_columns = {
            row["name"]
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }

        for column, column_type in columns.items():
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE {table} "
                    f"ADD COLUMN {column} {column_type}"
                )


# =====================================================
# DATABASE CONNECTION
# =====================================================

@contextmanager
def get_connection(db_path=DEFAULT_DB_PATH):
    """Create and manage a SQLite database connection."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# =====================================================
# DATABASE INITIALIZATION
# =====================================================

def initialize_database(db_path=DEFAULT_DB_PATH):
    """Initialize database and apply non-destructive schema migrations."""
    with get_connection(db_path) as connection:
        connection.executescript(SCHEMA)
        migrate_schema(connection)
