"""Development entry point.

    python run.py

Equivalent to ``flask run``, but runnable from anywhere: it puts its own
directory on ``sys.path`` and makes it the working directory first, so the
package imports and the ``.env`` lookup both resolve regardless of where the
command was typed.

Production uses ``wsgi:app`` behind gunicorn instead; see the Dockerfile.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    """Start the development server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("FLASK_RUN_HOST", "127.0.0.1"),
        help="Interface to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FLASK_RUN_PORT", "5000")),
        help=(
            "Port to listen on (default: 5000). Worth changing if something "
            "else already holds 5000; remember to update apiServerUrl in the "
            "frontend's environment.ts to match."
        ),
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.environ.get("FLASK_RELOAD", "").lower() in {"1", "true", "yes"},
        help="Restart on source changes.",
    )
    args = parser.parse_args()

    os.chdir(HERE)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)

    from src.api import app

    # The reloader double-imports the module, which would run the first-boot
    # database work twice: harmless, but it doubles every start-up log line.
    app.run(host=args.host, port=args.port, use_reloader=args.reload)


if __name__ == "__main__":
    main()
