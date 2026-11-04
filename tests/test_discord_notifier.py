import requests
import pytest

from alerts.discord_notifier import (
    build_discord_embed,
    build_discord_payload,
    send_discord_alerts,
    DiscordNotificationError,
)


def create_cpu_event(event_type="RAISED"):

    return {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
        "check_name": "CPU_USAGE",
        "event_type": event_type,
        "severity": "CRITICAL",
        "message": "CPU usage reached critical threshold",
        "observed_value": "95",
        "threshold": "90",
        "metric_unit": "%",
        "created_at": "2026-11-04T10:00:00+00:00",
    }


class MockResponse:

    status_code = 200

    def raise_for_status(self):
        pass


def test_build_critical_cpu_embed():

    event = create_cpu_event()

    embed = build_discord_embed(event)

    assert embed["title"] == "CRITICAL - CPU_USAGE"
    assert embed["color"] == 0xE74C3C

    fields = {
        field["name"]: field["value"]
        for field in embed["fields"]
    }

    assert fields["Device"] == "Router-01"
    assert fields["Observed value"] == "95 %"
    assert fields["Threshold"] == "90 %"


def test_build_recovery_embed():

    event = create_cpu_event("RESOLVED")

    embed = build_discord_embed(event)

    assert embed["title"] == "RECOVERED - CPU_USAGE"
    assert embed["color"] == 0x2ECC71


def test_build_discord_payload():

    payload = build_discord_payload([
        create_cpu_event()
    ])

    assert len(payload["embeds"]) == 1
    assert payload["allowed_mentions"]["parse"] == []


def test_send_discord_success(monkeypatch):

    requests_sent = []

    def mock_post(url, **kwargs):

        requests_sent.append({
            "url": url,
            "kwargs": kwargs,
        })

        return MockResponse()

    monkeypatch.setattr(
        "alerts.discord_notifier.requests.post",
        mock_post,
    )

    result = send_discord_alerts(
        events=[create_cpu_event()],
        webhook_url=(
            "https://discord.com/api/webhooks/123/test-token"
        ),
    )

    assert result["sent"] is True
    assert result["event_count"] == 1
    assert result["message_count"] == 1

    assert len(requests_sent) == 1

    payload = requests_sent[0]["kwargs"]["json"]

    assert payload["embeds"][0]["title"] == (
        "CRITICAL - CPU_USAGE"
    )


def test_discord_batch_sending(monkeypatch):

    calls = []

    def mock_post(url, **kwargs):
        calls.append(kwargs)
        return MockResponse()

    monkeypatch.setattr(
        "alerts.discord_notifier.requests.post",
        mock_post,
    )

    events = [
        create_cpu_event()
        for _ in range(11)
    ]

    result = send_discord_alerts(
        events=events,
        webhook_url=(
            "https://discord.com/api/webhooks/123/test-token"
        ),
    )

    assert result["event_count"] == 11
    assert result["message_count"] == 2

    assert len(calls[0]["json"]["embeds"]) == 10
    assert len(calls[1]["json"]["embeds"]) == 1


def test_discord_timeout(monkeypatch):

    def mock_post(*args, **kwargs):

        raise requests.exceptions.Timeout()

    monkeypatch.setattr(
        "alerts.discord_notifier.requests.post",
        mock_post,
    )

    with pytest.raises(
        DiscordNotificationError,
        match="timed out",
    ):

        send_discord_alerts(
            events=[create_cpu_event()],
            webhook_url=(
                "https://discord.com/api/webhooks/123/test-token"
            ),
        )


def test_invalid_webhook_url():

    with pytest.raises(ValueError):

        send_discord_alerts(
            events=[create_cpu_event()],
            webhook_url="http://example.com/webhook",
        )


def test_empty_events():

    result = send_discord_alerts(
        events=[],
        webhook_url="",
    )

    assert result["sent"] is True
    assert result["message_count"] == 0
