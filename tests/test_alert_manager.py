import pytest

from alerts.alert_manager import (
    build_alert_summary,
    deduplicate_alerts,
)


def create_alert(severity="WARNING"):
    return {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
        "check": "CPU_USAGE",
        "severity": severity,
        "message": "CPU usage reached threshold",
        "observed_value": 75,
        "threshold": 70,
    }


def test_deduplicate_alerts():

    alerts = [
        create_alert(),
        create_alert(),
        create_alert("CRITICAL"),
    ]

    result = deduplicate_alerts(alerts)

    assert len(result) == 2


def test_build_alert_summary():

    health_results = [
        {
            "device_id": "router-01",
            "hostname": "Router-01",
            "overall_status": "CRITICAL",
            "alerts": [
                create_alert("CRITICAL"),
            ],
        },
        {
            "device_id": "switch-01",
            "hostname": "Switch-01",
            "overall_status": "WARNING",
            "alerts": [
                {
                    **create_alert(),
                    "device_id": "switch-01",
                    "hostname": "Switch-01",
                }
            ],
        },
    ]

    summary = build_alert_summary(health_results)

    assert summary["device_count"] == 2
    assert summary["alert_count"] == 2
    assert summary["overall_status"] == "CRITICAL"
    assert summary["severity_counts"]["WARNING"] == 1
    assert summary["severity_counts"]["CRITICAL"] == 1


def test_empty_alert_summary():

    summary = build_alert_summary([])

    assert summary["device_count"] == 0
    assert summary["alert_count"] == 0
    assert summary["overall_status"] == "HEALTHY"


def test_deduplication_can_be_disabled():

    health_results = [
        {
            "device_id": "router-01",
            "hostname": "Router-01",
            "overall_status": "WARNING",
            "alerts": [
                create_alert(),
                create_alert(),
            ],
        }
    ]

    summary = build_alert_summary(
        health_results,
        deduplicate=False,
    )

    assert summary["alert_count"] == 2
