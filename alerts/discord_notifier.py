import json

from urllib.parse import urlparse
from typing import Any

import requests


class DiscordNotificationError(Exception):
    """Custom exception for Discord notification errors."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# Discord Embed colors
SEVERITY_COLORS = {
    "WARNING": 0xF1C40F,
    "CRITICAL": 0xE74C3C,
    "RECOVERY": 0x2ECC71,
}

MAX_EMBEDS_PER_MESSAGE = 10


def decode_metric(value: Any) -> Any:
    """
    Decode JSON values stored in SQLite TEXT columns.
    """

    if value is None:
        return None

    if not isinstance(value, str):
        return value

    try:
        return json.loads(value)

    except json.JSONDecodeError:
        return value


def format_metric(
    value: Any,
    unit: str | None = None,
) -> str:
    """Format metric values for Discord."""

    value = decode_metric(value)

    if value is None:
        return "N/A"

    if isinstance(value, (dict, list)):
        formatted = json.dumps(
            value,
            ensure_ascii=False,
        )
    else:
        formatted = str(value)

    if unit:
        formatted = f"{formatted} {unit}"

    return formatted


def build_discord_embed(
    event: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert one SQLite alert event into a Discord Embed.
    """

    event_type = str(
        event.get("event_type", "UNKNOWN")
    ).upper()

    severity = str(
        event.get("severity", "WARNING")
    ).upper()

    check_name = event.get(
        "check_name", "UNKNOWN"
    )

    device_id = event.get(
        "device_id", "Unknown"
    )

    hostname = event.get(
        "hostname"
    ) or device_id

    ip_address = event.get(
        "ip_address"
    ) or "N/A"

    observed_value = event.get(
        "observed_value"
    )

    threshold = event.get(
        "threshold"
    )

    metric_unit = event.get(
        "metric_unit"
    )

    is_resolved = event_type == "RESOLVED"

    if is_resolved:
        color = SEVERITY_COLORS["RECOVERY"]
        title = f"RECOVERED - {check_name}"
    else:
        color = SEVERITY_COLORS.get(
            severity,
            0x5865F2,
        )

        title = f"{severity} - {check_name}"

    if is_resolved:
        value_label = "Last recorded value"
    else:
        value_label = "Observed value"

    fields = [
        {
            "name": "Device",
            "value": str(hostname)[:1024],
            "inline": True,
        },
        {
            "name": "IP Address",
            "value": str(ip_address)[:1024],
            "inline": True,
        },
        {
            "name": "Event",
            "value": event_type,
            "inline": True,
        },
    ]

    if observed_value is not None:
        fields.append({
            "name": value_label,
            "value": format_metric(
                observed_value,
                metric_unit,
            )[:1024],
            "inline": True,
        })

    if threshold is not None:
        fields.append({
            "name": "Threshold",
            "value": format_metric(
                threshold,
                metric_unit,
            )[:1024],
            "inline": True,
        })

    fields.append({
        "name": "Message",
        "value": str(
            event.get("message", "No details")
        )[:1024],
        "inline": False,
    })

    embed = {
        "title": title[:256],
        "color": color,
        "fields": fields,
        "footer": {
            "text": (
                "Network Operations Automation"
                f" | Device ID: {device_id}"
            )[:2048],
        },
    }

    timestamp = event.get("created_at")

    if timestamp:
        embed["timestamp"] = timestamp

    return embed


def build_discord_payload(
    events: list[dict],
) -> dict[str, Any]:
    """
    Build a Discord webhook payload.

    One request supports up to 10 embeds.
    """

    if not events:
        raise ValueError(
            "At least one event is required"
        )

    embeds = [
        build_discord_embed(event)
        for event in events
    ]

    if len(embeds) > MAX_EMBEDS_PER_MESSAGE:
        raise ValueError(
            "Maximum 10 embeds per Discord message"
        )

    return {
        "username": "Network Operations Monitor",
        "embeds": embeds,
        "allowed_mentions": {
            "parse": [],
        },
    }


def validate_webhook_url(
    webhook_url: str,
) -> None:
    """
    Validate a Discord Incoming Webhook URL.
    """

    if not webhook_url:
        raise ValueError(
            "Discord webhook URL is required"
        )

    parsed = urlparse(webhook_url)

    if (
        parsed.scheme != "https"
        or parsed.hostname not in (
            "discord.com",
            "discordapp.com",
        )
        or not parsed.path.startswith("/api/webhooks/")
    ):
        raise ValueError(
            "Invalid Discord webhook URL"
        )


def send_discord_alerts(
    events: list[dict],
    webhook_url: str,
    timeout: int = 10,
) -> dict[str, Any]:
    """
    Send alert events to Discord through HTTP POST.

    Automatically splits events into batches of 10.
    """

    if not events:
        return {
            "sent": True,
            "message_count": 0,
            "event_count": 0,
        }

    if timeout < 1:
        raise ValueError(
            "Timeout must be at least 1 second"
        )

    validate_webhook_url(webhook_url)

    # Split events into groups of at most 10.
    batches = [
        events[index:index + MAX_EMBEDS_PER_MESSAGE]
        for index in range(
            0,
            len(events),
            MAX_EMBEDS_PER_MESSAGE,
        )
    ]

    status_codes = []

    for batch in batches:

        payload = build_discord_payload(batch)

        try:
            response = requests.post(
                webhook_url,
                params={"wait": "true"},
                json=payload,
                timeout=timeout,
            )

            response.raise_for_status()

            status_codes.append(
                response.status_code
            )

        except requests.exceptions.Timeout as exc:
            raise DiscordNotificationError(
                "Discord webhook request timed out"
            ) from exc

        except requests.exceptions.ConnectionError as exc:
            raise DiscordNotificationError(
                "Cannot connect to Discord webhook"
            ) from exc

        except requests.exceptions.HTTPError as exc:

            status_code = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            if status_code == 429:
                message = "Discord rate limit reached"
            else:
                message = (
                    f"Discord HTTP error: {status_code}"
                )

            raise DiscordNotificationError(
                message,
                status_code=status_code,
            ) from exc

        except requests.exceptions.RequestException as exc:
            raise DiscordNotificationError(
                f"Discord request failed: {exc}"
            ) from exc

    return {
        "sent": True,
        "message_count": len(status_codes),
        "event_count": len(events),
        "status_codes": status_codes,
    }
