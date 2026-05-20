"""Command line helper to cache GitHub tokens."""

from __future__ import annotations

import argparse
import sys

from .review_token import CONFIG_FILE, _build_project_token_cache, _load_cache, _save_cache


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-token",
        description=(
            "Cache a GitHub token either for all projects (classic) "
            "or for one specific owner/repository pair."
        ),
    )
    parser.add_argument("--token", required=True, metavar="TOKEN", help="GitHub token to cache.")
    parser.add_argument("--owner", metavar="OWNER", help="Repository owner for a project token.")
    parser.add_argument("--repo", metavar="REPO", help="Repository name for a project token.")
    parser.add_argument(
        "--classic",
        action="store_true",
        default=False,
        help="Store as the classic fallback token used for all projects.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = _build_parser()
    args = parser.parse_args(argv)
    if (args.owner is None) != (args.repo is None):
        parser.error("--owner and --repo must be provided together.")
    if args.classic and args.owner:
        parser.error("--classic cannot be combined with --owner/--repo.")

    if args.owner and args.repo:
        cache = _load_cache()
        _save_cache(
            {
                "project_tokens": _build_project_token_cache(
                    cache, args.owner, args.repo, args.token
                )
            }
        )
        print(f"github-token: saved token for {args.owner}/{args.repo} in {CONFIG_FILE}.")
        return 0

    _save_cache({"token": args.token})
    print(f"github-token: saved classic token in {CONFIG_FILE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
