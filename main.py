import argparse
import logging
import os

from pathlib import Path

import yaml
from alerts.discord_notifier import (
    send_discord_alerts,
    DiscordNotificationError,
)
from dotenv import load_dotenv

from collectors.ping_collector import collect_ping_results
from collectors.api_collector import create_api_client, NMSAPIError
from collectors.ssh_collector import collect_ssh_data

from parsers.cisco_parser import (
    parse_ip_interface_brief,
    parse_ip_route,
)

from processors.data_normalizer import normalize_inventory
from monitors.health_check import evaluate_inventory_health

from alerts.alert_manager import (
    build_alert_summary,
    print_alert_summary,
)

from alerts.webhook_notifier import (
    send_webhook_notification,
    WebhookNotificationError,
)

from reports.report_generator import generate_reports

from storage.state_tracker import process_monitoring_run
from storage.database import get_connection

from logging_config import setup_logging


PROJECT_ROOT = Path(__file__).resolve().parent


def load_yaml(filename):
    path = PROJECT_ROOT / filename

    with path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid configuration: {filename}")

    return data


def collect_ping(devices, settings, logger):

    ping_devices = [
        device for device in devices
        if device.get("enabled")
        and "ping" in device.get("collectors", [])
    ]

    ping_settings = settings.get("ping", {})
    monitoring = settings.get("monitoring", {})

    logger.info("Starting Ping Collector")

    try:
        results = collect_ping_results(
            devices=ping_devices,
            count=ping_settings.get("count", 5),
            timeout_seconds=ping_settings.get(
                "timeout_seconds", 1
            ),
            max_workers=monitoring.get("max_workers", 5),
        )

    except Exception as exc:
        logger.exception("Ping collection failed")

        results = [
            {
                "device_id": device["id"],
                "hostname": device["hostname"],
                "ip": device["ip"],
                "status": "ERROR",
                "latency_ms": None,
                "packet_loss_percent": None,
                "packets_transmitted": 0,
                "packets_received": 0,
                "error": str(exc),
            }
            for device in ping_devices
        ]

    for result in results:
        logger.info(
            "PING | %s | %s | %s",
            result["hostname"],
            result["ip"],
            result["status"],
        )

    return results


def collect_api_data(logger):

    client = None

    try:
        client = create_api_client()

        health = client.health_check()

        logger.info(
            "NMS API status: %s",
            health.get("status"),
        )

        devices = client.get_devices()

        logger.info(
            "API collected %s devices",
            len(devices),
        )

        return devices

    except Exception:
        logger.exception("NMS API collection failed")
        return []

    finally:
        if client is not None:
            client.close()


def collect_ssh(devices, settings, logger):

    ssh_devices = [
        device for device in devices
        if device.get("enabled")
        and device.get("platform") == "linux"
        and "ssh" in device.get("collectors", [])
    ]

    timeout = settings.get(
        "ssh", {}
    ).get("timeout_seconds", 8)

    results = []

    for device in ssh_devices:

        try:
            result = collect_ssh_data(
                device=device,
                timeout=timeout,
            )

            actual_hostname = result.get(
                "commands", {}
            ).get("hostname")

            if actual_hostname:
                result["hostname"] = actual_hostname

            results.append(result)

            logger.info(
                "SSH | %s | %s",
                device["hostname"],
                result["status"],
            )

        except Exception:
            logger.exception(
                "SSH collection failed for %s",
                device["hostname"],
            )

    for device in devices:
        if (
            device.get("enabled")
            and device.get("platform") == "cisco"
            and "ssh" in device.get("collectors", [])
        ):
            logger.info(
                "SSH skipped for %s: simulated Cisco environment",
                device["hostname"],
            )

    return results


def collect_cisco_fixtures(devices, logger):

    interfaces_by_device = {}
    routes_by_device = {}

    cisco_devices = [
        device for device in devices
        if device.get("enabled")
        and device.get("platform") == "cisco"
    ]

    if not cisco_devices:
        return interfaces_by_device, routes_by_device

    fixture_dir = PROJECT_ROOT / "fixtures"

    interface_file = (
        fixture_dir / "cisco_show_ip_interface_brief.txt"
    )

    route_file = (
        fixture_dir / "cisco_show_ip_route.txt"
    )

    for device in cisco_devices:

        device_id = device["id"]

        try:
            interface_output = interface_file.read_text(
                encoding="utf-8"
            )

            route_output = route_file.read_text(
                encoding="utf-8"
            )

            interfaces_by_device[device_id] = (
                parse_ip_interface_brief(interface_output)
            )

            routes_by_device[device_id] = (
                parse_ip_route(route_output)
            )

            logger.info(
                "Cisco fixtures parsed for %s",
                device["hostname"],
            )

            logger.warning(
                "Cisco data is simulated fixture data"
            )

            # The same fixture must not be assigned
            # to multiple devices as real telemetry.

            break

        except Exception:
            logger.exception(
                "Cisco parsing failed for %s",
                device["hostname"],
            )

    return interfaces_by_device, routes_by_device


def notify_new_alerts(run_id, db_path, settings, logger):
    """Send new SQLite alert events to Discord."""

    alert_settings = settings.get("alerts", {})

    if not alert_settings.get("discord_enabled", False):
        logger.info("Discord notification disabled")
        return

    webhook_url = os.getenv("ALERT_WEBHOOK_URL")

    if not webhook_url:
        logger.warning("Discord webhook URL is missing")
        return

    try:
        with get_connection(db_path) as connection:
            rows = connection.execute(
                """
                SELECT
                    ae.device_id,
                    dcs.hostname,
                    dcs.ip_address,
                    ae.check_name,
                    ae.event_type,
                    ae.severity,
                    ae.message,
                    ae.created_at,
                    ae.observed_value,
                    ae.threshold,
                    ae.metric_unit
                FROM alert_events ae
                LEFT JOIN device_current_state dcs
                    ON ae.device_id = dcs.device_id
                WHERE ae.run_id = ?
                ORDER BY ae.event_id
                """,
                (run_id,),
            ).fetchall()

        events = [dict(row) for row in rows]

        if not events:
            logger.info("No new Discord alert events")
            return

        result = send_discord_alerts(
            events=events,
            webhook_url=webhook_url,
            timeout=alert_settings.get(
                "discord_timeout_seconds", 10
            ),
        )

        logger.info(
            "Discord notification successful: %s events, %s messages",
            result["event_count"],
            result["message_count"],
        )

    except DiscordNotificationError as exc:
        logger.error("Discord notification failed: %s", exc)

    except Exception:
        logger.exception("Unexpected Discord notification error")
def run_monitoring():

    os.chdir(PROJECT_ROOT)

    load_dotenv(PROJECT_ROOT / ".env")

    logger = setup_logging()

    logger.info("=" * 55)
    logger.info("NETWORK AUTOMATION STARTED")
    logger.info("=" * 55)

    try:
        devices_config = load_yaml("config/devices.yaml")
        settings = load_yaml("config/settings.yaml")

        devices = [
            device for device in devices_config.get("devices", [])
            if device.get("enabled")
        ]

        if not devices:
            raise ValueError("No enabled devices found")

        logger.info("Loaded %s devices", len(devices))

        # 1. Ping collection
        ping_results = collect_ping(
            devices, settings, logger
        )

        # 2. REST API collection
        api_devices = collect_api_data(logger)

        # 3. SSH collection
        ssh_results = collect_ssh(
            devices, settings, logger
        )

        # 4. Cisco CLI parsing
        interfaces, routes = collect_cisco_fixtures(
            devices, logger
        )

        # 5. Data normalization
        normalized_devices = normalize_inventory(
            ping_results=ping_results,
            api_devices=api_devices,
            ssh_results=ssh_results,
            interfaces_by_device=interfaces,
            routes_by_device=routes,
        )

        logger.info(
            "Normalized %s devices",
            len(normalized_devices),
        )

        # 6. Health check
        health_results = evaluate_inventory_health(
            normalized_devices,
            settings,
        )

        # 7. SQLite state tracking
        db_path = (
            PROJECT_ROOT
            / "data"
            / "network_monitoring.db"
        )

        state_result = process_monitoring_run(
            normalized_devices=normalized_devices,
            health_results=health_results,
            db_path=db_path,
        )

        logger.info(
            "Monitoring run ID: %s",
            state_result["run_id"],
        )

        logger.info(
            "New state events: %s",
            state_result["state_event_count"],
        )

        logger.info(
            "New alert events: %s",
            state_result["alert_event_count"],
        )

        # 8. Alert summary
        alert_summary = build_alert_summary(
            health_results,
            deduplicate=settings.get(
                "alerts", {}
            ).get("deduplicate", True),
        )

        print_alert_summary(alert_summary)

        # 9. Report generation
        report_files = generate_reports(
            health_results=health_results,
            alert_summary=alert_summary,
            output_dir=str(
                PROJECT_ROOT / "data" / "output"
            ),
        )

        logger.info(
            "JSON report: %s",
            report_files["json_path"],
        )

        logger.info(
            "CSV report: %s",
            report_files["csv_path"],
        )

        # 10. Optional webhook
        notify_new_alerts(
            run_id=state_result["run_id"],
            db_path=db_path,
            settings=settings,
            logger=logger,
        )

        logger.info("NETWORK AUTOMATION COMPLETED")

        return 0

    except Exception:
        logger.exception("Monitoring pipeline failed")
        return 1


def main():

    parser = argparse.ArgumentParser(
        description="Network Operations Automation Tool"
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute one monitoring cycle",
    )

    args = parser.parse_args()

    if not args.once:
        parser.print_help()
        return 0

    return run_monitoring()


if __name__ == "__main__":
    raise SystemExit(main())
