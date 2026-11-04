import csv
import json

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CSV_FIELDS = [
    "device_id",
    "hostname",
    "ip_address",
    "check",
    "severity",
    "message",
    "observed_value",
    "threshold",
]


def generate_reports(
    health_results: list[dict],
    alert_summary: dict,
    output_dir: str = "data/output",
) -> dict[str, str]:
    """
    Generate JSON health report and CSV alert report.
    """

    output_path = Path(output_dir)
    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%S%fZ")

    json_file = output_path / (
        f"health_report_{timestamp}.json"
    )

    csv_file = output_path / (
        f"alerts_{timestamp}.csv"
    )

    # Generate JSON report
    report_data = {
        "health_results": health_results,
        "alert_summary": alert_summary,
    }

    with json_file.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            report_data,
            file,
            indent=4,
            ensure_ascii=False,
        )

    # Generate CSV alert report
    with csv_file.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_FIELDS,
        )

        writer.writeheader()

        for alert in alert_summary.get("alerts", []):

            row = {
                field: alert.get(field)
                for field in CSV_FIELDS
            }

            for field in ("observed_value", "threshold"):
                if isinstance(row[field], (dict, list)):
                    row[field] = json.dumps(
                        row[field],
                        ensure_ascii=False,
                    )

            writer.writerow(row)

    return {
        "json_path": str(json_file),
        "csv_path": str(csv_file),
    }
