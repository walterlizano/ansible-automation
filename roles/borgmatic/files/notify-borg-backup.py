#!/usr/bin/python3
"""Send a Slack message after a successful Borgmatic backup run."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def configured_backup_details() -> tuple[list[str], list[str], list[str]]:
    """Read non-secret backup metadata from the local Borgmatic configs."""
    config_dir = Path("/etc/borgmatic.d")
    config_files = sorted(config_dir.glob("*.yaml"))
    snippet_files = sorted((config_dir / "snippets").glob("*.yaml"))

    backups = [path.stem for path in config_files if path.stem != "common"]
    paths: set[str] = set()
    destinations: set[str] = set()

    for path in [*config_files, *snippet_files]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue

        paths.update(re.findall(r"^\s+-\s+(/[^\s#]+)\s*$", content, re.MULTILINE))
        destinations.update(
            re.findall(r"^\s+label:\s+([^\s#]+)", content, re.MULTILINE)
        )

    return backups, sorted(destinations), sorted(paths)


def main() -> int:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("Slack notification skipped: no webhook URL is configured.")
        return 0

    server = os.uname().nodename
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    backups, destinations, paths = configured_backup_details()
    backup_text = ", ".join(backups) or "unknown"
    destination_text = ", ".join(destinations) or "unknown"
    paths_text = ", ".join(paths) or "unknown"
    message = (
        "Borg backup completed successfully\n\n"
        f"Source server: {server}\n"
        f"Backup: {backup_text}\n"
        f"Destinations: {destination_text}\n"
        f"Paths: {paths_text}\n"
        f"Timestamp: {timestamp}"
    )

    request = Request(
        webhook_url,
        data=json.dumps({"text": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = response.read().decode("utf-8", errors="replace").strip()
            if response.status != 200 or result != "ok":
                print(
                    "Slack notification failed: Slack returned an unexpected response.",
                    file=sys.stderr,
                )
                return 1
    except (HTTPError, URLError, TimeoutError, OSError):
        # Avoid logging the request URL, which contains the webhook secret.
        print("Slack notification failed: could not post to the configured webhook.", file=sys.stderr)
        return 1

    print("Slack backup notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
