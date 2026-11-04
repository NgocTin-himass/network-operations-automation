import requests


class WebhookNotificationError(Exception):
    """Webhook delivery error."""


def send_webhook_notification(
    payload: dict,
    webhook_url: str | None,
    timeout: int = 5,
) -> dict:
    """
    Send alert payload to a configured HTTP webhook.
    """

    if not webhook_url:
        return {
            "sent": False,
            "reason": "Webhook is not configured",
        }

    if timeout < 1:
        raise ValueError("Timeout must be at least 1 second")

    if not webhook_url.startswith(("https://", "http://")):
        raise ValueError("Invalid webhook URL")

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=timeout,
        )

        response.raise_for_status()

        return {
            "sent": True,
            "status_code": response.status_code,
        }

    except requests.exceptions.Timeout as exc:
        raise WebhookNotificationError(
            "Webhook request timed out"
        ) from exc

    except requests.exceptions.ConnectionError as exc:
        raise WebhookNotificationError(
            "Cannot connect to webhook"
        ) from exc

    except requests.exceptions.HTTPError as exc:

        status_code = (
            exc.response.status_code
            if exc.response is not None
            else None
        )

        raise WebhookNotificationError(
            f"Webhook returned HTTP {status_code}"
        ) from exc
