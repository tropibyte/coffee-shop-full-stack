"""Build the submission zip.

Zipping the project folder by hand goes wrong in three ways:

* ``node_modules`` and ``venv`` are tens of thousands of files that nobody
  wants to download and the rubric explicitly asks you to leave out;
* on Windows, ``node_modules`` may be a directory *junction*, and most zip
  tools follow it — producing an archive many times larger than the project;
* ``.env`` and ``terraform.tfstate`` hold live credentials.

This walks the tree, skips all of that, refuses to continue if it finds a
secret, and reports what it produced.

Usage::

    python package_submission.py
    python package_submission.py --output ~/Desktop/coffee-shop.zip
    python package_submission.py --allow-tokens     # keep Postman JWTs

The Postman collection is *meant* to carry JWTs when submitted, so the token
check warns rather than blocks — but it tells you whether they are there and
how long they have left, because a reviewer opening the collection to a wall
of 401s is the most avoidable way to lose marks on this project.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import zipfile
from typing import Iterable, List, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(HERE), "coffee-shop-full-stack-submission.zip"
)

#: Directories never included, at any depth.
EXCLUDED_DIRS: Set[str] = {
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".angular",
    "www",
    "dist",
    "build",
    "htmlcov",
    ".git",
    ".terraform",
    ".idea",
    ".tox",
    ".nox",
    "out-tsc",
    ".sourcemaps",
    "platforms",
    "plugins",
}

#: File suffixes never included.
EXCLUDED_SUFFIXES: Tuple[str, ...] = (
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".tfstate",
    ".tfstate.backup",
    ".pem",
    ".key",
)

#: Exact file names never included.
EXCLUDED_NAMES: Set[str] = {
    ".env",
    ".coverage",
    "coverage.xml",
    "junit.xml",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    "terraform.tfvars",
    "bandit-report.json",
    "pip-audit-report.json",
}

#: Files that must be present, or the archive is not a complete submission.
REQUIRED = (
    "README.md",
    "backend/src/api.py",
    "backend/src/auth/auth.py",
    "backend/src/database/models.py",
    "backend/requirements.txt",
    "backend/udacity-fsnd-udaspicelatte.postman_collection.json",
    "frontend/src/environments/environment.ts",
    "frontend/package.json",
    "docs/AUTH0_SETUP.md",
)


def should_skip_dir(name: str) -> bool:
    """Whether a directory is excluded."""
    return name in EXCLUDED_DIRS or name.endswith(".egg-info")


def should_skip_file(name: str) -> bool:
    """Whether a file is excluded.

    ``.env.example`` is kept: it documents every setting and contains no
    secret. Only a real ``.env`` is dropped.
    """
    if name == ".env.example":
        return False
    if name in EXCLUDED_NAMES:
        return True
    if name.startswith(".env."):
        return True
    return name.endswith(EXCLUDED_SUFFIXES)


def collect(root: str) -> List[Tuple[str, str]]:
    """Return (absolute path, archive path) for every file to include."""
    entries: List[Tuple[str, str]] = []

    for current, dirs, files in os.walk(root):
        # Prune in place so os.walk does not descend into them at all.
        dirs[:] = [
            d
            for d in dirs
            if not should_skip_dir(d)
            # A junction or symlink would otherwise be followed, pulling in
            # whatever it points at -- on Windows that is usually a
            # node_modules tree living outside the project.
            and not os.path.islink(os.path.join(current, d))
            and not _is_reparse_point(os.path.join(current, d))
        ]

        for name in files:
            if should_skip_file(name):
                continue
            absolute = os.path.join(current, name)
            if os.path.islink(absolute):
                continue
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")
            entries.append((absolute, relative))

    return sorted(entries, key=lambda pair: pair[1])


def _is_reparse_point(path: str) -> bool:
    """Whether a Windows directory junction sits at ``path``."""
    if os.name != "nt":
        return False
    try:
        # FILE_ATTRIBUTE_REPARSE_POINT
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes & 0x400)
    except (OSError, AttributeError):
        return False


#: Assignment of a credential-ish name to a value.
_ASSIGNMENT = re.compile(
    r"""(?P<name>AUTH0_M2M_CLIENT_SECRET
               |terraform_client_secret
               |google_client_secret
               |client_secret
               |SECRET_KEY
               |AZURE_[A-Z_]*SECRET)
        # Horizontal whitespace only: \s would span the newline after an
        # empty `KEY=` and match the *next* line's value.
        [ \t]*[=:][ \t]*
        ["']?(?P<value>[^\s"'#,}\]]+)["']?""",
    re.VERBOSE,
)

_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")

#: A value containing any of these is documentation, not a credential.
_PLACEHOLDER_MARKERS = (
    "replace",
    "your-",
    "your_",
    "<",
    "...",
    "example",
    "terraform",
    "changeme",
    "xxx",
    "run:",
    "generatevalue",
    "sync:",
    "fromservice",
)


def _looks_like_a_real_secret(value: str) -> bool:
    """Whether a value is plausibly a live credential rather than a template.

    The whole point of `.env.example`, the Terraform variables file and the
    setup guide is to *talk about* these names, so matching the name alone
    flags six documentation files and nothing else. A value is only
    interesting if it is long, varied, and not obviously a placeholder.
    """
    if len(value) < 16:
        return False

    lowered = value.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return False

    # Shell and template interpolation: ${VAR}, $env:VAR, {{ var }}, %VAR%.
    if value.startswith(("$", "{{", "%", "@")) or "${" in value:
        return False

    # A real Auth0 secret or generated key is a mix of character classes.
    # "local-development-key-not-used-for-anything-sensitive" is not.
    classes = sum(
        (
            any(c.islower() for c in value),
            any(c.isupper() for c in value),
            any(c.isdigit() for c in value),
            any(not c.isalnum() for c in value),
        )
    )
    return classes >= 3


def find_secrets(entries: Iterable[Tuple[str, str]]) -> List[str]:
    """Return a description of every value that looks like a live credential."""
    problems: List[str] = []

    for absolute, relative in entries:
        if relative.endswith(
            (".png", ".jpg", ".jpeg", ".ico", ".zip", ".woff", ".woff2", ".gif")
        ):
            continue
        try:
            with open(absolute, encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            continue

        if _PRIVATE_KEY.search(text):
            problems.append("{0}: contains a private key block".format(relative))

        for match in _ASSIGNMENT.finditer(text):
            value = match.group("value")
            if _looks_like_a_real_secret(value):
                problems.append(
                    "{0}: {1} is set to what looks like a real value "
                    "({2}...)".format(relative, match.group("name"), value[:6])
                )

    return problems


def check_postman_tokens(root: str) -> List[str]:
    """Report on the JWTs in the Postman collection."""
    path = os.path.join(
        root, "backend", "udacity-fsnd-udaspicelatte.postman_collection.json"
    )
    if not os.path.exists(path):
        return ["Postman collection is missing."]

    with open(path, encoding="utf-8") as handle:
        collection = json.load(handle)

    notes: List[str] = []
    for folder in collection.get("item", []):
        bearer = (folder.get("auth") or {}).get("bearer") or []
        token = next(
            (entry.get("value") for entry in bearer if entry.get("key") == "token"),
            "",
        )
        if not token or token.startswith("{{"):
            if folder["name"] in ("barista", "manager", "administrator"):
                notes.append("{0}: NO TOKEN".format(folder["name"]))
            continue

        try:
            segment = token.split(".")[1]
            payload = json.loads(
                base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
            )
            remaining = int(payload.get("exp", 0) - time.time())
        except Exception:  # noqa: BLE001
            notes.append("{0}: token is unreadable".format(folder["name"]))
            continue

        if remaining <= 0:
            notes.append("{0}: token EXPIRED".format(folder["name"]))
        elif remaining < 7200:
            notes.append(
                "{0}: token expires in {1} minutes".format(
                    folder["name"], remaining // 60
                )
            )
        else:
            notes.append(
                "{0}: token valid for {1} hours".format(
                    folder["name"], remaining // 3600
                )
            )

    return notes


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-tokens",
        action="store_true",
        help="Do not warn about JWTs in the Postman collection.",
    )
    args = parser.parse_args()

    print("Collecting files from {0}\n".format(HERE))
    entries = collect(HERE)

    missing = [name for name in REQUIRED if name not in {rel for _, rel in entries}]
    if missing:
        print("These required files are missing:", file=sys.stderr)
        for name in missing:
            print("  - {0}".format(name), file=sys.stderr)
        return 1

    secrets = find_secrets(entries)
    if secrets:
        print("Refusing to package: a secret was found.\n", file=sys.stderr)
        for problem in secrets:
            print("  ! {0}".format(problem), file=sys.stderr)
        return 1

    if not args.allow_tokens:
        print("Postman collection:")
        for note in check_postman_tokens(HERE):
            marker = "!" if ("NO TOKEN" in note or "EXPIRED" in note) else " "
            print("  {0} {1}".format(marker, note))
        print(
            "\n  The rubric asks for working JWTs in the barista and manager\n"
            "  folders. Add them with:\n"
            "      python backend/scripts/set_postman_tokens.py --barista ... --manager ...\n"
        )

    total = 0
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for absolute, relative in entries:
            bundle.write(absolute, os.path.join("coffee-shop-full-stack", relative))
            total += os.path.getsize(absolute)

    size = os.path.getsize(args.output)
    print(
        "Wrote {0}\n"
        "  {1} files, {2:.1f} MB uncompressed, {3:.1f} MB zipped".format(
            args.output, len(entries), total / 1_048_576, size / 1_048_576
        )
    )

    if size > 50 * 1_048_576:
        print(
            "\n  ! That is larger than expected. Check that no dependency\n"
            "    directory slipped in."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
