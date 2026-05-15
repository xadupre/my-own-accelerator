"""Package entrypoint."""

import argparse
import sys

from .commands.review_local import main as main_local
from .commands.review_pr import main as main_pr


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m moa")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("review-pr", "review-local"),
        help="Command to run (defaults to review-pr when omitted).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Routes to the supported command-line implementations."""
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in {"-h", "--help"}:
        _build_parser().parse_args(argv)
        return 0
    if argv and argv[0] == "review-local":
        return main_local(argv[1:])
    if argv and argv[0] == "review-pr":
        return main_pr(argv[1:])
    return main_pr(argv)


sys.exit(main())
