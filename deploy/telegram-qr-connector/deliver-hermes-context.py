#!/home/roomhacker/.hermes/hermes-agent/venv/bin/python3
"""Record a detached agent result in Hermes context, then deliver it."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

HERMES_ROOT = Path("/home/roomhacker/.hermes/hermes-agent")
HERMES_BIN = HERMES_ROOT / "venv/bin/hermes"


def _session_db_factory():
    sys.path.insert(0, str(HERMES_ROOT))
    from hermes_state import SessionDB

    return SessionDB()


def record_and_send(
    *,
    session_id: str,
    event_id: str,
    report_path: Path,
    target: str,
    db_factory: Callable = _session_db_factory,
    run: Callable = subprocess.run,
) -> None:
    report = report_path.read_text(encoding="utf-8").strip()
    if not report:
        raise ValueError("report is empty")

    db = db_factory()
    try:
        db.append_delegation_delivery(
            session_id,
            report,
            {
                "delegation_id": event_id,
                "delivery_notice": "userio_agent_deliver",
                "producer": "universal-userio",
            },
        )
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()

    run(
        [str(HERMES_BIN), "send", "--to", target, "--file", str(report_path), "--quiet"],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--to", default="telegram")
    args = parser.parse_args()
    record_and_send(
        session_id=args.session_id,
        event_id=args.event_id,
        report_path=args.file,
        target=args.to,
    )


if __name__ == "__main__":
    main()
