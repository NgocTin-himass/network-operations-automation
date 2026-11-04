import requests
import pytest

from alerts.webhook_notifier import (
    send_webhook_notification,
    WebhookNotificationError,
)


class MockResponse:

    status_code = 200

    def raise_for_status(self):
        pass


def test_webhook_success(monkeypatch):

    def mock_post(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(
        "alerts.webhook_notifier.requests.post",
        mock_post,
    )

    result = send_webhook_notification(
        payload={"message": "Test alert"},
        webhook_url="https://example.com/webhook",
    )

    assert result["sent"] is True
    assert result["status_code"] == 200


def test_webhook_not_configured():

    result = send_webhook_notification(
        payload={"message": "Test"},
        webhook_url=None,
    )

    assert result["sent"] is False


def test_webhook_timeout(monkeypatch):

    def mock_post(*args, **kwargs):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(
        "alerts.webhook_notifier.requests.post",
        mock_post,
    )

    with pytest.raises(
        WebhookNotificationError,
        match="timed out",
    ):
        send_webhook_notification(
            payload={"message": "Test"},
            webhook_url="https://example.com/webhook",
        )
