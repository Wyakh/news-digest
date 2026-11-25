"""Load the shared, loopback-only launcher settings without application imports."""

import json
from pathlib import Path


def load_launch_settings(project_root=None):
    """Return ``(host, port, base_url)`` from optional config/launch.json.

    A missing file (or missing ``port`` member) uses the public port 5000.
    All file and validation failures intentionally omit configuration contents.
    """
    root = Path(__file__).parent if project_root is None else Path(project_root)
    launch_path = root / "config" / "launch.json"
    try:
        text = launch_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        settings = {}
    except (OSError, UnicodeError):
        raise ValueError(
            "Cannot read config/launch.json; check its permissions and UTF-8 encoding."
        ) from None
    else:
        try:
            settings = json.loads(text)
        except (ValueError, RecursionError):
            raise ValueError(
                "Invalid config/launch.json; provide a JSON object with an integer port from 1 to 65535."
            ) from None

    if not isinstance(settings, dict):
        raise ValueError("Invalid config/launch.json; the top-level JSON value must be an object.")
    port = settings.get("port", 5000)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid config/launch.json port; use an integer from 1 to 65535 (not a boolean).")
    host = "127.0.0.1"
    return host, port, "http://{}:{}".format(host, port)
