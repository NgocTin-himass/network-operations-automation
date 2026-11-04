from pathlib import Path

import pytest

from parsers.cisco_parser import (
    parse_ip_interface_brief,
    parse_ip_route,
    parse_cisco_output,
    CiscoParserError,
)


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_fixture(filename):
    return (FIXTURE_DIR / filename).read_text(
        encoding="utf-8"
    )


def test_parse_interface_brief():

    output = load_fixture(
        "cisco_show_ip_interface_brief.txt"
    )

    result = parse_ip_interface_brief(output)

    assert len(result) == 4

    interfaces = {
        item["interface"]: item
        for item in result
    }

    assert interfaces["GigabitEthernet0/0"]["status"] == "up"

    assert (
        interfaces["GigabitEthernet0/1"]["status"]
        == "administratively down"
    )

    assert interfaces["GigabitEthernet0/2"]["proto"] == "down"


def test_parse_ip_route():

    output = load_fixture(
        "cisco_show_ip_route.txt"
    )

    result = parse_ip_route(output)

    networks = {
        item["network"]
        for item in result
    }

    assert "0.0.0.0" in networks
    assert "192.168.1.0" in networks
    assert "10.10.0.0" in networks


def test_invalid_command():

    with pytest.raises(CiscoParserError):
        parse_cisco_output(
            command="show version invalid",
            raw_output="Some output",
        )


def test_empty_output():

    with pytest.raises(CiscoParserError):
        parse_ip_interface_brief("")


def test_invalid_interface_output():

    with pytest.raises(CiscoParserError):
        parse_ip_interface_brief(
            "This is not a valid Cisco interface table"
        )
