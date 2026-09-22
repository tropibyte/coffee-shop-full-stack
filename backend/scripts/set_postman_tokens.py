"""Inject JWTs into the Postman collection before submitting it.

The rubric asks for the exported collection to carry working tokens in each
role folder's Authorization tab. Doing that by hand means opening five
dialogs and pasting three 900-character strings without dropping a character.

This writes them into both places a reviewer might look -- the folder-level
bearer auth *and* the matching collection variable -- and refuses anything
that is not a plausible, unexpired token, so a truncated paste fails here
rather than in front of the reviewer.

Usage::

    python scripts/set_postman_tokens.py --barista "eyJ..." --manager "eyJ..."
    python scripts/set_postman_tokens.py --admin "eyJ..."       # one at a time
    python scripts/set_postman_tokens.py --check                # inspect only
    python scripts/set_postman_tokens.py --clear                # strip them out

Tokens are read from the command line or, with ``--stdin``, from standard
input -- useful when a shell would otherwise record the token in its history.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COLLECTION = os.path.join(
    os.path.dirname(HERE), "udacity-fsnd-udaspicelatte.postman_collection.json"
)

#: Folder name -> collection variable holding that folder's token.
ROLE_FOLDERS = {
    "barista": "barista_token",
    "manager": "manager_token",
    "administrator": "admin_token",
}

#: Permissions each role is expected to carry, used to warn about a mix-up.
EXPECTED_PERMISSIONS = {
    "barista": {"get:drinks-detail"},
    "manager": {"post:drinks", "patch:drinks", "delete:drinks"},
    "administrator": {"get:audit"},
}


class TokenError(ValueError):
    """Raised when a supplied string is not a usable access token."""


def decode_payload(token: str) -> Dict[str, Any]:
    """Decode a JWT payload without verifying it.

    Verification is the API's job. This only needs enough of the claims to
    catch a truncated paste or a token for the wrong role.
    """
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise TokenError(
            "not a JWT: expected three dot-separated segments, got {0}. "
            "A partial copy is the usual cause.".format(len(parts))
        )
    segment = parts[1]
    padding = "=" * (-len(segment) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(segment + padding))
    except Exception as exc:  # noqa: BLE001 - any failure means "unusable"
        raise TokenError("could not decode the token payload: {0}".format(exc)) from exc


def describe(token: str) -> Tuple[str, bool]:
    """Return a human summary of a token and whether it is still valid."""
    payload = decode_payload(token)
    expires_at = payload.get("exp", 0)
    remaining = int(expires_at - time.time())
    permissions = payload.get("permissions", [])

    if remaining <= 0:
        window = "EXPIRED {0} ago".format(_duration(-remaining))
    else:
        window = "valid for {0}".format(_duration(remaining))

    return (
        "{0}  |  {1}  |  {2} permission{3}".format(
            payload.get("sub", "?"),
            window,
            len(permissions),
            "" if len(permissions) == 1 else "s",
        ),
        remaining > 0,
    )


def _duration(seconds: int) -> str:
    """Render a second count as a short human duration."""
    if seconds >= 3600:
        return "{0}h {1}m".format(seconds // 3600, (seconds % 3600) // 60)
    if seconds >= 60:
        return "{0}m".format(seconds // 60)
    return "{0}s".format(seconds)


def validate(role: str, token: str) -> None:
    """Refuse a token that is expired or clearly belongs to another role."""
    payload = decode_payload(token)

    remaining = int(payload.get("exp", 0) - time.time())
    if remaining <= 0:
        raise TokenError(
            "this token expired {0} ago. Sign in again and copy a fresh "
            "one.".format(_duration(-remaining))
        )
    if remaining < 3600:
        print(
            "  ! warning: expires in {0}. A reviewer may open this after it "
            "has lapsed.".format(_duration(remaining))
        )

    granted = set(payload.get("permissions", []))
    if not granted:
        raise TokenError(
            "this token carries no permissions. Enable RBAC and 'Add "
            "Permissions in the Access Token' on the Auth0 API "
            "(docs/AUTH0_SETUP.md step 1a)."
        )

    expected = EXPECTED_PERMISSIONS.get(role, set())
    if expected and not expected & granted:
        raise TokenError(
            "this does not look like a {0} token: it holds none of {1}. "
            "Did the roles get swapped?".format(role, ", ".join(sorted(expected)))
        )

    # A manager token pasted into the barista folder would make the barista
    # folder's 403 assertions fail, which is confusing to debug.
    if role == "barista" and granted & {"post:drinks", "delete:drinks"}:
        raise TokenError(
            "this token can write to the menu, so it is not a barista token. "
            "The barista folder asserts 403 on every write."
        )


def load(path: str) -> Dict[str, Any]:
    """Read the collection."""
    if not os.path.exists(path):
        raise SystemExit(
            "Collection not found: {0}\n"
            "Generate it with: python scripts/build_postman_collection.py".format(path)
        )
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save(path: str, collection: Dict[str, Any]) -> None:
    """Write the collection back."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, indent=2)
        handle.write("\n")


def set_folder_token(collection: Dict[str, Any], folder: str, token: str) -> bool:
    """Write ``token`` into a folder's bearer auth. Returns True on success."""
    for node in collection.get("item", []):
        if node.get("name") != folder:
            continue
        node["auth"] = {
            "type": "bearer",
            "bearer": [{"key": "token", "value": token, "type": "string"}],
        }
        return True
    return False


def set_variable(collection: Dict[str, Any], key: str, value: str) -> None:
    """Write a collection variable, adding it if absent."""
    for variable in collection.setdefault("variable", []):
        if variable.get("key") == key:
            variable["value"] = value
            return
    collection["variable"].append({"key": key, "value": value, "type": "string"})


def report(collection: Dict[str, Any]) -> int:
    """Print what the collection currently holds. Returns an exit code."""
    print("Collection: {0}\n".format(collection["info"]["name"]))
    problems = 0

    for folder, variable in ROLE_FOLDERS.items():
        node = next(
            (n for n in collection.get("item", []) if n.get("name") == folder), None
        )
        if node is None:
            print("  {0:<14} folder missing".format(folder))
            problems += 1
            continue

        bearer = (node.get("auth") or {}).get("bearer") or []
        token = next(
            (entry.get("value") for entry in bearer if entry.get("key") == "token"),
            "",
        )

        if not token or token.startswith("{{"):
            print("  {0:<14} no token set".format(folder))
            problems += 1
            continue

        try:
            summary, ok = describe(token)
        except TokenError as exc:
            print("  {0:<14} unusable: {1}".format(folder, exc))
            problems += 1
            continue

        print("  {0:<14} {1}".format(folder, summary))
        if not ok:
            problems += 1

    print()
    if problems:
        print(
            "{0} folder(s) need attention before submitting.".format(problems)
        )
    else:
        print("All role folders carry a live token.")
    return 1 if problems else 0


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--barista", help="Access token for the Barista role.")
    parser.add_argument("--manager", help="Access token for the Manager role.")
    parser.add_argument("--admin", help="Access token for the Administrator role.")
    parser.add_argument(
        "--stdin",
        choices=sorted(ROLE_FOLDERS),
        help="Read one token from standard input instead of the command line, "
        "so it never reaches your shell history.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what the collection holds and change nothing.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove every token. Run this before committing.",
    )
    args = parser.parse_args()

    collection = load(args.collection)

    if args.check:
        return report(collection)

    if args.clear:
        for folder, variable in ROLE_FOLDERS.items():
            set_folder_token(collection, folder, "{{" + variable + "}}")
            set_variable(collection, variable, "")
        save(args.collection, collection)
        print("Cleared every token from {0}".format(args.collection))
        return 0

    supplied = {
        "barista": args.barista,
        "manager": args.manager,
        "administrator": args.admin,
    }

    if args.stdin:
        supplied[args.stdin] = sys.stdin.read().strip()

    if not any(supplied.values()):
        parser.error(
            "give at least one of --barista / --manager / --admin, "
            "or use --check or --clear"
        )

    changed = 0
    for role, token in supplied.items():
        if not token:
            continue
        token = token.strip()
        # Tolerate a paste that includes the scheme.
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        try:
            validate(role, token)
        except TokenError as exc:
            print("  x {0}: {1}".format(role, exc), file=sys.stderr)
            return 1

        if not set_folder_token(collection, role, token):
            print("  x {0}: no such folder in the collection".format(role),
                  file=sys.stderr)
            return 1
        set_variable(collection, ROLE_FOLDERS[role], token)

        summary, _ = describe(token)
        print("  ok {0:<14} {1}".format(role, summary))
        changed += 1

    save(args.collection, collection)
    print(
        "\nUpdated {0} folder(s) in {1}".format(changed, args.collection)
    )
    print("Re-run with --check before you submit, and --clear before you commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
