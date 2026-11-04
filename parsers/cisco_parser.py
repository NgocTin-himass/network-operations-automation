from typing import Any

from ntc_templates.parse import parse_output


class CiscoParserError(Exception):
    """Custom exception for Cisco CLI parsing errors."""


SUPPORTED_COMMANDS = {
    "show ip interface brief",
    "show ip route",
}


def parse_cisco_output(
    command: str,
    raw_output: str,
) -> list[dict[str, Any]]:
    """
    Parse Cisco IOS CLI output using NTC Templates.
    """

    normalized_command = " ".join(command.lower().split())

    if normalized_command not in SUPPORTED_COMMANDS:
        raise CiscoParserError(
            f"Unsupported Cisco command: {command}"
        )

    if not isinstance(raw_output, str) or not raw_output.strip():
        raise CiscoParserError(
            "CLI output is empty or invalid"
        )

    try:
        parsed_data = parse_output(
            platform="cisco_ios",
            command=normalized_command,
            data=raw_output,
        )

    except Exception as exc:
        raise CiscoParserError(
            f"Failed to parse command: {normalized_command}"
        ) from exc

    if not parsed_data:
        raise CiscoParserError(
            f"No data parsed from: {normalized_command}"
        )

    return parsed_data


def parse_ip_interface_brief(
    raw_output: str,
) -> list[dict[str, Any]]:
    """Parse show ip interface brief."""

    return parse_cisco_output(
        command="show ip interface brief",
        raw_output=raw_output,
    )


def parse_ip_route(
    raw_output: str,
) -> list[dict[str, Any]]:
    """Parse show ip route."""

    return parse_cisco_output(
        command="show ip route",
        raw_output=raw_output,
    )
