#!/usr/bin/env python3
"""Manage the channel identities that may open a UserIO user with a code.

An identity is a verified email address or Telegram account. A one-time code
is only ever sent to an address; a verified identity decides which user that
code opens. Binding an identity to an existing user is an owner operation.

Examples:
    python scripts/login_identities.py list
    python scripts/login_identities.py bind email owner@example.com --owner
    python scripts/login_identities.py bind telegram 540308572 --owner
    python scripts/login_identities.py unbind email owner@example.com
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from universal_userio.store import SQLiteUserIOStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.environ.get("USERIO_DB_PATH", ""),
                        help="path to the UserIO database")
    parser.add_argument("action", choices=["list", "bind", "unbind"])
    parser.add_argument("channel", nargs="?", choices=["email", "telegram"])
    parser.add_argument("identifier", nargs="?")
    parser.add_argument("--owner", action="store_true", help="bind to the owner account")
    parser.add_argument("--user-id", default="", help="bind to this user id")
    parser.add_argument("--display-name", default="", help="label stored with the identity")
    args = parser.parse_args()

    if not args.db:
        parser.error("a database path is required (--db or USERIO_DB_PATH)")
    store = SQLiteUserIOStore(args.db)

    if args.action == "list":
        identities = store.login_identities()
        if not identities:
            print("no identities are bound")
            return 0
        for identity in identities:
            print(f"{identity['channel']:9} {identity['identifier']:40} "
                  f"user={identity['username']} role={identity['role']} "
                  f"name={identity['display_name']!r}")
        return 0

    if not args.channel or not args.identifier:
        parser.error("a channel and an identifier are required")
    if args.action == "bind":
        if args.owner:
            user_id = store.owner().user_id
        elif args.user_id:
            user_id = args.user_id
        else:
            parser.error("bind needs --owner or --user-id")
        identity = store.register_login_identity(
            user_id=user_id, channel=args.channel, identifier=args.identifier,
            display_name=args.display_name)
        print(f"bound {args.channel}:{args.identifier} -> {identity['user_id']}")
        return 0

    removed = store.remove_login_identity(channel=args.channel, identifier=args.identifier)
    print("removed" if removed else "nothing was bound")
    return 0


if __name__ == "__main__":
    sys.exit(main())
