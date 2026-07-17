"""Command line helper to cache GitHub tokens."""

from __future__ import annotations

import argparse
import re
import sys
from urllib import request
from urllib.error import HTTPError, URLError

from .review_token import CONFIG_FILE, _build_project_token_cache, _fetch_token_from_gh_cli, _load_cache, _save_cache


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
        "--gh",
        action="store_true",
        default=False,
        help=(
            "Fetch the GitHub token from the ``gh`` CLI (``gh auth token``). "
            "Cannot be combined with --token."
        ),
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
        help=(
            "Display token permissions/scopes and exit. Requires --token, "
            "or --list to query permissions for each cached token."
        ),
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
    with request.urlopen(req, timeout=10) as response:
        oauth_scopes = _sanitize_permission_header(response.headers.get("X-OAuth-Scopes", ""))
        accepted_scopes = _sanitize_permission_header(
            response.headers.get("X-Accepted-OAuth-Scopes", "")
        )
        accepted_permissions = _sanitize_permission_header(
            response.headers.get("X-Accepted-GitHub-Permissions", "")
        )
    print("github-token: token permission details")
    sys.stdout.write(f"oauth_scopes_count: {_count_permission_entries(oauth_scopes)}\n")
    sys.stdout.write(
        f"accepted_oauth_scopes_count: {_count_permission_entries(accepted_scopes)}\n"
    )
    sys.stdout.write(f"accepted_github_permissions: {accepted_permissions or '(none)'}\n")
    return 0


def _sanitize_permission_header(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_,:.\- ]+", "", value).strip()
    return cleaned[:200]


def _count_permission_entries(value: str) -> int:
    return sum(1 for item in value.split(",") if item.strip())


def _show_permissions_for_cached_tokens() -> int:
    cache = _load_cache()
    entries: list[tuple[str, str]] = []
    token = cache.get("token")
    if isinstance(token, str) and token:
        entries.append(("classic", token))
    project_tokens = cache.get("project_tokens")
    if isinstance(project_tokens, dict):
        for key, value in sorted(project_tokens.items()):
            if isinstance(value, str) and value:
                entries.append((key, value))
    if not entries:
        print(f"github-token: no cached tokens in {CONFIG_FILE}.")
        return 0
    exit_code = 0
    for label, tok in entries:
        print(f"--- permissions for {label} ---")
        try:
            rc = _show_token_permissions(tok)
            if rc != 0:
                exit_code = rc
        except (HTTPError, URLError, OSError) as e:
            print(f"github-token: failed to query token permissions: {e}", file=sys.stderr)
            exit_code = 1
    return exit_code


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.gh and "--token" in argv:
        parser.error("--gh and --token are mutually exclusive.")
    if args.gh:
        args.token = _fetch_token_from_gh_cli()

    # --show-permissions requires --token, unless --list is used (in which
    # case permissions are queried for each cached token).
    if args.show_permissions and not args.token and not args.list:
        parser.error("--token or --gh is required with --show-permissions.")

    # --list cannot be combined with token-saving options.
    # Providing --token alongside --list is only valid when --show-permissions
    # is also given (the token is used for the permissions query, not saved).
    if args.list:
        if args.owner or args.repo or args.classic:
            parser.error("--list cannot be combined with token-saving options.")
        if args.token and not args.show_permissions:
            parser.error("--list cannot be combined with token-saving options.")

    # A token is required except when only listing.
    if not args.list and not args.token:
        parser.error("--token or --gh is required unless --list is specified.")

    # Token-saving: runs when not listing and the caller explicitly asked to
    # save (via --owner/--repo/--classic) or did not request --show-permissions
    # (i.e. the default "just save this token" usage).
    if not args.list and args.token:
        has_save_flags = bool(args.owner or args.repo or args.classic)
        should_save = has_save_flags or not args.show_permissions
        if should_save:
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
            else:
                _save_cache({"token": args.token})
                print(f"github-token: saved classic token in {CONFIG_FILE}.")
            if not args.show_permissions:
                return 0

    # List cached tokens (may also be combined with --show-permissions).
    if args.list:
        _list_tokens(args.verbose)
        if not args.show_permissions:
            return 0
        # When --show-permissions is combined with --list and no explicit
        # --token is provided, query permissions for each cached token.
        if not args.token:
            return _show_permissions_for_cached_tokens()

    # Show permissions for the provided token.
    if args.show_permissions:
        try:
            return _show_token_permissions(args.token)
        except (HTTPError, URLError, OSError) as e:
            print(f"github-token: failed to query token permissions: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
