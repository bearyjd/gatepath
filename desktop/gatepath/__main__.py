"""Entry point for `python -m gatepath`.

Parses argv with argparse FIRST — before any GTK/PyGObject import —
so that `--help` works even when PyGObject is not installed.

GTK imports happen inside run_app() (via app.py) only when we are
actually starting the GUI.
"""

from __future__ import annotations

import argparse
import logging
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gatepath",
        description=(
            "Gatepath — captive portal handler for Linux desktop.\n\n"
            "Monitors NetworkManager for captive portals and opens an isolated\n"
            "WebKit window to complete sign-in safely."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version="gatepath 0.1.0",
    )
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level (default: WARNING).",
    )
    parser.add_argument(
        "--probe-url",
        default=None,
        metavar="URL",
        help="Override the connectivity-check URL used for probing.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Main entry point.  Parses args, configures logging, then starts GUI."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Deferred import — only runs when actually launching the GUI.
    from gatepath.app import run_app  # noqa: PLC0415

    run_app(probe_url=args.probe_url)


if __name__ == "__main__":
    main()
