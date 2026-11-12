#!/usr/bin/env python3
"""Portable launcher for the mixed-agents MCP bridge.

Checks Python and platform requirements before importing the server so that
unsupported hosts fail with a clear stderr message instead of an import trace.
"""

from __future__ import annotations

import sys

MIN_PYTHON = (3, 11)
SUPPORTED_PLATFORMS = frozenset({"darwin", "linux"})


def fail(message: str) -> None:
    print(f"mixed-agents: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    if sys.version_info < MIN_PYTHON:
        fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, "
            f"found {sys.version_info.major}.{sys.version_info.minor}"
        )
    if sys.platform not in SUPPORTED_PLATFORMS:
        fail(f"macOS or Linux required, found {sys.platform}")

    # Make sibling bridge modules importable when launch.py is loaded from an
    # unrelated working directory (importlib, relocated package, etc.).
    from pathlib import Path  # noqa: PLC0415
    bridge_dir = Path(__file__).resolve().parent
    if str(bridge_dir) not in sys.path:
        sys.path.insert(0, str(bridge_dir))

    # Import only after dependency checks so unsupported hosts get a clear error.
    import server  # noqa: PLC0415

    return server.main()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
