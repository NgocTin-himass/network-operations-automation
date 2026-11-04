import os

from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)
from paramiko.ssh_exception import SSHException


PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")


# Read-only commands for Ubuntu Linux
SSH_COMMANDS = {
    "hostname": "hostname",
    "interfaces": "ip -br addr",
    "routes": "ip route",
    "listening_ports": "ss -ltn",
    "uptime": "uptime",
}


def collect_ssh_data(
    device: dict[str, Any],
    username: str | None = None,
    key_file: str | None = None,
    timeout: int = 8,
) -> dict[str, Any]:
    """
    Connect to a network device using SSH
    and collect read-only information.
    """

    result = {
        "device_id": device.get("id"),
        "hostname": device.get("hostname"),
        "ip": device.get("ip"),
        "status": "ERROR",
        "commands": {},
        "error": None,
    }

    host = device.get("ip")
    device_type = device.get("device_type")

    username = username or os.getenv("SSH_USERNAME")
    key_file = key_file or os.getenv("SSH_KEY_FILE")

    if not host or not device_type:
        result["error"] = "Missing host or device_type"
        return result

    if not username:
        result["error"] = "SSH username is missing"
        return result

    if not key_file:
        result["error"] = "SSH private key path is missing"
        return result

    key_path = Path(key_file).expanduser()

    if not key_path.is_file():
        result["error"] = "SSH private key file not found"
        return result

    if timeout < 1:
        result["error"] = "Invalid SSH timeout"
        return result

    connection = None

    try:
        connection = ConnectHandler(
            device_type=device_type,
            host=host,
            username=username,
            key_file=str(key_path),
            use_keys=True,
            allow_agent=False,
            conn_timeout=timeout,
            auth_timeout=timeout,
            banner_timeout=timeout,
        )

        for name, command in SSH_COMMANDS.items():

            output = connection.send_command(
                command,
                read_timeout=timeout,
                strip_prompt=True,
                strip_command=True,
            )

            result["commands"][name] = output.strip()

        result["status"] = "CONNECTED"

    except NetmikoAuthenticationException:
        result["error"] = "SSH authentication failed"

    except NetmikoTimeoutException:
        result["error"] = "SSH connection timed out"

    except SSHException as exc:
        result["error"] = f"SSH protocol error: {exc}"

    except OSError as exc:
        result["error"] = f"Operating system error: {exc}"

    finally:
        if connection is not None:
            connection.disconnect()

    return result
