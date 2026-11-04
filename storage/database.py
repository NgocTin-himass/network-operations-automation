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

    FOREIGN KEY (run_id)
        REFERENCES monitoring_runs(run_id)
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

    FOREIGN KEY (run_id)
        REFERENCES monitoring_runs(run_id)
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

    FOREIGN KEY (run_id)
        REFERENCES monitoring_runs(run_id)
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

    FOREIGN KEY (run_id)
        REFERENCES monitoring_runs(run_id)
);


-- =====================================================
-- DATABASE INDEXES
-- =====================================================

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

"""


# =====================================================
# DATABASE MIGRATION
# =====================================================

def migrate_schema(connection):
    """
    Upgrade an existing database without deleting records.

    New alert columns are added only when missing.
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

                print(
                    f"[MIGRATION] Added "
                    f"{table}.{column}"
                )


# =====================================================
# DATABASE CONNECTION
# =====================================================

@contextmanager
def get_connection(db_path=DEFAULT_DB_PATH):
    """
    Create and manage SQLite database connection.
    """

    db_path = Path(db_path)

    db_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        str(db_path),
        timeout=10,
    )

    connection.row_factory = sqlite3.Row

    # Enable foreign key enforcement
    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    # Improve concurrent read/write behavior
    connection.execute(
        "PRAGMA journal_mode = WAL"
    )

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
    """
    Initialize database and apply schema migrations.
    """

    with get_connection(db_path) as connection:

        # Create missing tables and indexes
        connection.executescript(SCHEMA)

        # Upgrade existing tables
        migrate_schema(connection)

    print("[DATABASE] Initialization completed")
