import csv
import json

from reports.report_generator import generate_reports


def test_generate_json_and_csv_reports(tmp_path):

    health_results = [
        {
            "device_id": "router-01",
            "hostname": "Router-01",
            "overall_status": "WARNING",
            "alert_count": 1,
        }
    ]

    alert_summary = {
        "overall_status": "WARNING",
        "device_count": 1,
        "alert_count": 1,
        "severity_counts": {
            "WARNING": 1,
            "CRITICAL": 0,
        },
        "alerts": [
            {
                "device_id": "router-01",
                "hostname": "Router-01",
                "ip_address": "192.0.2.10",
                "check": "CPU_USAGE",
                "severity": "WARNING",
                "message": "CPU usage is high",
                "observed_value": 75,
                "threshold": 70,
            }
        ],
    }

    result = generate_reports(
        health_results,
        alert_summary,
        output_dir=str(tmp_path),
    )

    json_path = result["json_path"]
    csv_path = result["csv_path"]

    # Check JSON
    with open(json_path, encoding="utf-8") as file:
        data = json.load(file)

    assert data["health_results"][0]["device_id"] == "router-01"

    # Check CSV
    with open(
        csv_path,
        encoding="utf-8-sig",
    ) as file:

        rows = list(csv.DictReader(file))

    assert len(rows) == 1
    assert rows[0]["severity"] == "WARNING"
    assert rows[0]["check"] == "CPU_USAGE"


def test_report_generation_without_alerts(tmp_path):

    result = generate_reports(
        health_results=[],
        alert_summary={"alerts": []},
        output_dir=str(tmp_path),
    )

    with open(
        result["csv_path"],
        encoding="utf-8-sig",
    ) as file:

        rows = list(csv.DictReader(file))

    assert rows == []


def test_output_directory_created(tmp_path):

    output_dir = tmp_path / "reports" / "daily"

    result = generate_reports(
        health_results=[],
        alert_summary={"alerts": []},
        output_dir=str(output_dir),
    )

    assert output_dir.exists()
    assert output_dir in (
        output_dir / result["json_path"].split("/")[-1]
    ).parents
