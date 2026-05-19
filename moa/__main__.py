"""Package entrypoint."""

import argparse
import sys

from .commands.pr_stats import main as main_pr_stats
from .commands.review_local import main as main_local
from .commands.review_pr import main as main_pr


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moa",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  review-pr     Review a GitHub pull request and print markdown.\n"
            "  review-local  Review local files and print markdown.\n"
            "  pr-stats      Build pull request activity reports (CSV, Excel, graphs)."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("review-pr", "review-local", "pr-stats"),
        help="Command to run (defaults to review-pr when omitted).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Routes to supported command-line implementations.

    When no subcommand is provided, arguments are delegated to ``review-pr``.
    """
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in {"-h", "--help"}:
        _build_parser().parse_args(argv)
    if argv and argv[0] == "review-local":
        return main_local(argv[1:], prog="python -m moa review-local")
    if argv and argv[0] == "review-pr":
        return main_pr(argv[1:], prog="python -m moa review-pr")
    if argv and argv[0] == "pr-stats":
        return main_pr_stats(argv[1:], prog="python -m moa pr-stats")
    return main_pr(argv, prog="python -m moa review-pr")


sys.exit(main())
