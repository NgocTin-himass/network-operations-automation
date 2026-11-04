import json
import requests
import pytest

from collectors.api_collector import (
    NMSAPIClient,
    NMSAPIError,
)


@pytest.fixture
def client():
    api_client = NMSAPIClient(
        base_url="http://127.0.0.1:8000",
        api_token="test-token",
        timeout=3,
    )

    yield api_client
    api_client.close()


def make_response(status_code=200, data=None, raw_content=None):
    """Create a simulated HTTP response."""

    response = requests.Response()
    response.status_code = status_code
    response.url = "http://127.0.0.1:8000/devices"
    response.request = requests.Request(
        "GET", response.url
    ).prepare()

    if raw_content is not None:
        response._content = raw_content
    else:
        response._content = json.dumps(data).encode("utf-8")

    response.headers["Content-Type"] = "application/json"

    return response


def test_get_devices(client, monkeypatch):
    mock_data = {
        "count": 1,
        "devices": [
            {
                "device_id": "router-01",
                "hostname": "Router-01",
                "ip_address": "192.0.2.10",
                "status": "online",
            }
        ],
    }

    monkeypatch.setattr(
        client.session,
        "get",
        lambda *args, **kwargs: make_response(
            data=mock_data
        ),
    )

    devices = client.get_devices()

    assert len(devices) == 1
    assert devices[0]["hostname"] == "Router-01"


def test_api_token_is_configured(client):
    assert (
        client.session.headers["X-API-Token"]
        == "test-token"
    )


def test_get_device(client, monkeypatch):
    mock_device = {
        "device_id": "router-01",
        "hostname": "Router-01",
        "ip_address": "192.0.2.10",
    }

    monkeypatch.setattr(
        client.session,
        "get",
        lambda *args, **kwargs: make_response(
            data=mock_device
        ),
    )

    device = client.get_device("router-01")

    assert device["device_id"] == "router-01"


def test_http_404(client, monkeypatch):
    response = make_response(
        status_code=404,
        data={"detail": "Device not found"},
    )

    monkeypatch.setattr(
        client.session,
        "get",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(NMSAPIError) as exc_info:
        client.get_device("router-99")

    assert exc_info.value.status_code == 404


def test_api_timeout(client, monkeypatch):
    def mock_timeout(*args, **kwargs):
        raise requests.exceptions.Timeout(
            "Request timed out"
        )

    monkeypatch.setattr(
        client.session,
        "get",
        mock_timeout,
    )

    with pytest.raises(NMSAPIError, match="timed out"):
        client.get_devices()


def test_connection_error(client, monkeypatch):
    def mock_connection_error(*args, **kwargs):
        raise requests.exceptions.ConnectionError(
            "Connection refused"
        )

    monkeypatch.setattr(
        client.session,
        "get",
        mock_connection_error,
    )

    with pytest.raises(
        NMSAPIError,
        match="Cannot connect",
    ):
        client.get_devices()


def test_invalid_json(client, monkeypatch):
    response = make_response(
        raw_content=b"invalid-json-response"
    )

    monkeypatch.setattr(
        client.session,
        "get",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(
        NMSAPIError,
        match="invalid JSON",
    ):
        client.get_devices()


def test_invalid_devices_format(client, monkeypatch):
    response = make_response(
        data={"count": 2, "devices": "invalid"}
    )

    monkeypatch.setattr(
        client.session,
        "get",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(
        NMSAPIError,
        match="Invalid devices data",
    ):
        client.get_devices()
