"""Package entrypoint."""

import argparse
import sys

from .commands.github_token import main as main_github_token
from .commands.pr_stats import main as main_pr_stats
from .commands.pr_weekly_table import main as main_pr_weekly_table
from .commands.review_local import main as main_local
from .commands.review_pr import main as main_pr
from .commands.workflow_jobs import main as main_workflow_jobs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m moa",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Commands:\n"
            "  - github-token   : Cache a GitHub token for all projects or one repository.\n"
            "  - review-pr      : Review a GitHub pull request and print markdown.\n"
            "  - review-local   : Review local files and print markdown.\n"
            "  - pr-stats       : Build pull request activity reports (CSV, Excel, graphs).\n"
            "  - pr-weekly-table: Build a weekly PR summary markdown table.\n"
            "  - workflow-jobs  : Report queued jobs, durations, and fail-rate history."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "github-token",
            "review-pr",
            "review-local",
            "pr-stats",
            "pr-weekly-table",
            "workflow-jobs",
        ),
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
        return main_local(argv[1:])
    if argv and argv[0] == "review-pr":
        return main_pr(argv[1:])
    if argv and argv[0] == "github-token":
        return main_github_token(argv[1:])
    if argv and argv[0] == "pr-stats":
        return main_pr_stats(argv[1:])
    if argv and argv[0] == "pr-weekly-table":
        return main_pr_weekly_table(argv[1:])
    if argv and argv[0] == "workflow-jobs":
        return main_workflow_jobs(argv[1:])
    return main_pr(argv)


sys.exit(main())
