"""Command line helper to cache GitHub tokens."""

from __future__ import annotations

import argparse
import sys
from urllib import request
from urllib.error import HTTPError, URLError

from .review_token import CONFIG_FILE, _build_project_token_cache, _load_cache, _save_cache


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-token",
        description=(
            "Cache a GitHub token either for all projects (classic) "
            "or for one specific owner/repository pair."
        ),
    )
    parser.add_argument("--token", metavar="TOKEN", help="GitHub token to cache.")
    parser.add_argument("--owner", metavar="OWNER", help="Repository owner for a project token.")
    parser.add_argument("--repo", metavar="REPO", help="Repository name for a project token.")
    parser.add_argument(
        "--classic",
        action="store_true",
        default=False,
        help="Store as the classic fallback token used for all projects.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        default=False,
        help="List cached tokens and exit.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Show token type details when listing cached tokens.",
    )
    parser.add_argument(
        "--show-permissions",
        action="store_true",
        default=False,
        help="Display token permissions/scopes and exit (requires --token).",
    )
    return parser


def _list_tokens(verbose: bool) -> int:
    cache = _load_cache()
    has_output = False
    token = cache.get("token")
    if isinstance(token, str) and token:
        has_output = True
        if verbose:
            print(f"classic ({CONFIG_FILE}): {token} [type=classic]")
        else:
            print(f"classic: {token}")
    project_tokens = cache.get("project_tokens")
    if isinstance(project_tokens, dict):
        for key, value in sorted(project_tokens.items()):
            if isinstance(value, str) and value:
                has_output = True
                if verbose:
                    print(f"{key} ({CONFIG_FILE}): {value} [type=fine-grained]")
                else:
                    print(f"{key}: {value}")
    if not has_output:
        print(f"github-token: no cached tokens in {CONFIG_FILE}.")
    return 0


def _show_token_permissions(token: str) -> int:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "moa/github-token",
    }
    req = request.Request("https://api.github.com/user", headers=headers)
    with request.urlopen(req) as response:
        oauth_scopes = response.headers.get("X-OAuth-Scopes", "")
        accepted_scopes = response.headers.get("X-Accepted-OAuth-Scopes", "")
        accepted_permissions = response.headers.get("X-Accepted-GitHub-Permissions", "")
    print("github-token: token permission details")
    print(f"oauth_scopes: {oauth_scopes or '(none)'}")
    print(f"accepted_oauth_scopes: {accepted_scopes or '(none)'}")
    print(f"accepted_github_permissions: {accepted_permissions or '(none)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.show_permissions:
        if args.list or args.owner or args.repo or args.classic:
            parser.error(
                "--show-permissions cannot be combined with --list or token-saving options."
            )
        if not args.token:
            parser.error("--token is required with --show-permissions.")
        try:
            return _show_token_permissions(args.token)
        except (HTTPError, URLError, OSError, ValueError) as e:
            print(f"github-token: failed to query token permissions: {e}", file=sys.stderr)
            return 1
    if args.list:
        if args.token or args.owner or args.repo or args.classic:
            parser.error("--list cannot be combined with token-saving options.")
        return _list_tokens(args.verbose)
    if not args.token:
        parser.error("--token is required unless --list is specified.")
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
