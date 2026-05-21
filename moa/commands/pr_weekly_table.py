"""Weekly pull request summary command line."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from typing import Any, TypeVar
from urllib import parse, request
from urllib.error import HTTPError, URLError

from .copilot_models import DEFAULT_MODEL, _send_chat_request
from .pr_stats import _fetch_paginated
from .review_token import (
    _extract_owner_repo,
    _resolve_cached_token,
    _resolve_token_origin,
)
from .review_token import (
    _load_cache as _load_token_cache,
)
from .since_utils import parse_relative_since

DEFAULT_CACHE_DIR = "dump_pr_stats"
COPILOT_SUMMARY_UNAVAILABLE_PREFIX = "Copilot summary unavailable ("
VALID_HELP_NEEDED_VALUES = {"", "yes", "no"}

_FAILING_STATUS_STATES = {"error", "failure"}
_T = TypeVar("_T")


def _retry_incomplete_read(function: Callable[[], _T], retries: int = 3) -> _T:
    if retries < 1:
        raise ValueError("retries must be >= 1")
    for attempt in range(1, retries + 1):
        try:
            return function()
        except IncompleteRead:
            if attempt >= retries:
                raise
    raise AssertionError(
        "Unreachable code: retry loop must either return successfully or raise IncompleteRead"
    )


def _fetch_json(url: str, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "moa/pr-weekly-table",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers)

    def _load() -> Any:
        with request.urlopen(req) as response:
            return json.load(response)

    return _retry_incomplete_read(_load)


def _fetch_paginated_with_retries(url: str, token: str | None = None) -> list[Any]:
    def _fetch() -> list[Any]:
        return _fetch_paginated(url, token)

    return _retry_incomplete_read(_fetch)


def _default_since() -> str:
    """Return an ISO date string for one week ago (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")


def _parse_since_datetime(value: str | None, now: datetime | None = None) -> datetime:
    since_value = (value or _default_since()).strip()
    relative_dt = parse_relative_since(since_value, now=now)
    if relative_dt is not None:
        return relative_dt
    try:
        parsed = datetime.fromisoformat(since_value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            "Invalid --since value "
            f"{since_value!r}; expected YYYY-MM-DD, ISO datetime, "
            "or relative values like '-1 day', '-3d', '+2 weeks', or '3 hours'."
        ) from e
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _load_cache(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    rows = payload.get("rows", {})
    if not isinstance(rows, dict):
        return {}
    cleaned_rows: dict[str, dict[str, Any]] = {}
    for k, v in rows.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        row = dict(v)
        raw_summary = row.get("copilot_summary", "")
        raw_help_needed = row.get("help_needed", "")
        summary = raw_summary.strip() if isinstance(raw_summary, str) else ""
        help_needed = raw_help_needed.strip().lower() if isinstance(raw_help_needed, str) else ""
        if (raw_summary != "" and not isinstance(raw_summary, str)) or (
            summary.startswith(COPILOT_SUMMARY_UNAVAILABLE_PREFIX)
            or not isinstance(raw_help_needed, str)
            or help_needed not in VALID_HELP_NEEDED_VALUES
        ):
            row.pop("copilot_summary", None)
            row.pop("help_needed", None)
        cleaned_rows[k] = row
    return cleaned_rows


def _save_cache(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    payload = {"rows": {str(row["number"]): row for row in rows}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _collect_reviewers(
    owner: str,
    repo: str,
    pull_number: int,
    requested_reviewers: list[dict[str, Any]],
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> str:
    reviewers = {
        str(item.get("login", "")).strip()
        for item in requested_reviewers
        if isinstance(item, dict) and item.get("login")
    }
    reviews = _fetch_paginated_with_retries(
        f"{api_url.rstrip('/')}/repos/{owner}/{repo}/pulls/{pull_number}/reviews", token
    )
    for review in reviews:
        if not isinstance(review, dict):
            continue
        user = review.get("user")
        if not isinstance(user, dict):
            continue
        login = str(user.get("login", "")).strip()
        if login:
            reviewers.add(login)
    return ", ".join(sorted(reviewers))


def _needs_ci_approval(
    owner: str,
    repo: str,
    head_sha: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> bool:
    if not head_sha:
        return False
    url = (
        f"{api_url.rstrip('/')}/repos/{owner}/{repo}/actions/runs?"
        f"{parse.urlencode({'event': 'pull_request', 'head_sha': head_sha, 'per_page': 20})}"
    )
    payload = _fetch_json(url, token)
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    if not isinstance(runs, list):
        return False
    return any(
        isinstance(run, dict)
        and (run.get("conclusion") == "action_required" or run.get("status") == "action_required")
        for run in runs
    )


def _collect_required_contexts(
    owner: str,
    repo: str,
    branch: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> list[str]:
    if not branch:
        return []
    url = (
        f"{api_url.rstrip('/')}/repos/{owner}/{repo}/branches/{branch}"
        "/protection/required_status_checks"
    )
    try:
        payload = _fetch_json(url, token)
    except HTTPError as e:
        if e.code in {403, 404}:
            return []
        raise
    contexts = payload.get("contexts", []) if isinstance(payload, dict) else []
    if not isinstance(contexts, list):
        return []
    return [str(ctx) for ctx in contexts if isinstance(ctx, str) and ctx]


def _collect_ci_status(
    owner: str,
    repo: str,
    head_sha: str,
    required_contexts: list[str],
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> str:
    if not head_sha:
        return "unknown"
    url = f"{api_url.rstrip('/')}/repos/{owner}/{repo}/commits/{head_sha}/status"
    payload = _fetch_json(url, token)
    if not isinstance(payload, dict):
        return "unknown"
    statuses = payload.get("statuses", [])
    if not isinstance(statuses, list):
        statuses = []
    failing_contexts: list[str] = []
    context_to_state: dict[str, str] = {}
    has_pending = False
    for status in statuses:
        if not isinstance(status, dict):
            continue
        context = str(status.get("context", "")).strip()
        state = str(status.get("state", "")).strip().lower()
        if context and context not in context_to_state:
            context_to_state[context] = state
        if state in _FAILING_STATUS_STATES and context:
            failing_contexts.append(context)
        if state in {"pending"}:
            has_pending = True
    if failing_contexts:
        if required_contexts:
            for context in required_contexts:
                state = context_to_state.get(context, "")
                if state in _FAILING_STATUS_STATES:
                    return f"failing: {context}"
        return f"failing: {failing_contexts[0]}"
    if has_pending:
        return "pending"
    if str(payload.get("state", "")).lower() == "success":
        return "green"
    return "green" if statuses else "unknown"


def _call_copilot_summary(
    row: dict[str, Any],
    token: str,
    model: str | None = DEFAULT_MODEL,
    on_model_used: Callable[[str], None] | None = None,
    verbose: int = 0,
) -> tuple[str, str]:
    messages = [
        {
            "role": "system",
            "content": (
                "You summarize pull requests for an engineering weekly report. "
                "Reply with strict JSON only: "
                '{"summary":"...", "help_needed": true/false}.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Title: {row.get('title', '')}\n"
                f"Author: {row.get('author', '')}\n"
                f"Created: {row.get('created_at', '')}\n"
                f"Updated: {row.get('updated_at', '')}\n"
                f"Reviewers: {row.get('reviewers', '')}\n"
                f"CI: {row.get('ci_status', '')}\n"
                f"Needs CI approval: {row.get('needs_ci_approval', '')}\n"
            ),
        },
    ]
    raw = _send_chat_request(
        messages,
        token,
        model=model,
        command_name="pr-weekly-table",
        on_model_used=on_model_used,
        verbose=verbose,
    )
    summary = raw.strip()
    help_needed = "unknown"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return summary, help_needed
    if isinstance(payload, dict):
        value = payload.get("summary")
        if isinstance(value, str) and value.strip():
            summary = value.strip()
        help_value = payload.get("help_needed")
        if isinstance(help_value, bool):
            help_needed = "yes" if help_value else "no"
    return summary, help_needed


def _format_copilot_error_details(error: Exception) -> str:
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}: {error.reason}"
    if isinstance(error, URLError):
        return f"URL error: {error.reason}"
    message = str(error)
    if not message:
        return type(error).__name__
    return message


def _build_copilot_warning(row: dict[str, Any], error: Exception) -> str:
    number = str(row.get("number", "")).strip()
    title = str(row.get("title", "")).strip()
    link = str(row.get("link", "")).strip()
    if number:
        target = f"PR #{number}"
    elif title:
        target = f"PR {title!r}"
    elif link:
        target = link
    else:
        target = "a PR"
    return (
        "pr-weekly-table: warning: unable to generate Copilot summary for "
        f"{target}: {_format_copilot_error_details(error)}."
    )


def build_weekly_pr_summary_rows(
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    since: str | None = None,
    cached_rows: dict[str, dict[str, Any]] | None = None,
    copilot: bool = False,
    model: str | None = DEFAULT_MODEL,
    warnings: list[str] | None = None,
    on_model_used: Callable[[str], None] | None = None,
    verbose: int = 0,
) -> list[dict[str, Any]]:
    since_dt = _parse_since_datetime(since)
    pulls_url = (
        f"{api_url.rstrip('/')}/repos/{owner}/{repo}/pulls"
        "?state=all&sort=created&direction=desc"
    )
    pulls = _fetch_paginated_with_retries(pulls_url, token)
    cache = cached_rows or {}
    required_contexts_cache: dict[str, list[str]] = {}
    rows: list[dict[str, Any]] = []
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        created_at = str(pr.get("created_at", "")).strip()
        if not created_at:
            continue
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        if created_dt < since_dt:
            continue
        number = int(pr.get("number", 0))
        updated_at = str(pr.get("updated_at", ""))
        head_sha = str((pr.get("head") or {}).get("sha", ""))
        cache_key = str(number)
        cached = cache.get(cache_key)
        if cached:
            has_copilot_cache = bool(cached.get("copilot_summary")) and bool(
                cached.get("help_needed")
            )
            if (
                str(cached.get("updated_at", "")) == updated_at
                and str(cached.get("head_sha", "")) == head_sha
                and (not copilot or has_copilot_cache)
            ):
                rows.append(cached)
                continue
        base_branch = str((pr.get("base") or {}).get("ref", ""))
        if base_branch not in required_contexts_cache:
            required_contexts_cache[base_branch] = _collect_required_contexts(
                owner=owner,
                repo=repo,
                branch=base_branch,
                token=token,
                api_url=api_url,
            )
        row: dict[str, Any] = {
            "number": number,
            "title": str(pr.get("title", "")),
            "author": str((pr.get("user") or {}).get("login", "")),
            "created_at": created_at,
            "updated_at": updated_at,
            "link": str(pr.get("html_url", "")),
            "head_sha": head_sha,
            "needs_ci_approval": (
                "yes" if _needs_ci_approval(owner, repo, head_sha, token, api_url) else "no"
            ),
            "ci_status": _collect_ci_status(
                owner=owner,
                repo=repo,
                head_sha=head_sha,
                required_contexts=required_contexts_cache.get(base_branch, []),
                token=token,
                api_url=api_url,
            ),
            "reviewers": _collect_reviewers(
                owner=owner,
                repo=repo,
                pull_number=number,
                requested_reviewers=(
                    pr.get("requested_reviewers", [])
                    if isinstance(pr.get("requested_reviewers", []), list)
                    else []
                ),
                token=token,
                api_url=api_url,
            ),
        }
        if copilot:
            try:
                summary, help_needed = _call_copilot_summary(
                    row,
                    token=token or "",
                    model=model,
                    on_model_used=on_model_used,
                    verbose=verbose,
                )
            except (HTTPError, URLError, OSError, ValueError, IncompleteRead) as e:
                if warnings is not None:
                    warnings.append(_build_copilot_warning(row, e))
            else:
                row["copilot_summary"] = summary
                row["help_needed"] = help_needed
        rows.append(row)
    return rows


def _escape_markdown(value: Any) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def _format_pr_link(row: dict[str, Any]) -> str:
    link = str(row.get("link", "")).strip()
    number = row.get("number")
    number_text = "" if number is None else str(number).strip()
    if link and number_text:
        return f"[#{_escape_markdown(number_text)}]({link})"
    return _escape_markdown(link)


def build_weekly_pr_markdown_table(rows: list[dict[str, Any]], copilot: bool = False) -> str:
    headers = [
        "Title",
        "Author",
        "Created",
        "Last update",
        "#",
        "Needs CI approval",
        "CI status",
        "Reviewers",
    ]
    if copilot:
        headers.extend(["Copilot summary", "Help needed"])
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        values = [
            _escape_markdown(row.get("title", "")),
            _escape_markdown(row.get("author", "")),
            _escape_markdown(row.get("created_at", "")),
            _escape_markdown(row.get("updated_at", "")),
            _format_pr_link(row),
            _escape_markdown(row.get("needs_ci_approval", "")),
            _escape_markdown(row.get("ci_status", "")),
            _escape_markdown(row.get("reviewers", "")),
        ]
        if copilot:
            values.extend(
                [
                    _escape_markdown(row.get("copilot_summary", "")),
                    _escape_markdown(row.get("help_needed", "")),
                ]
            )
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _default_cache_path(repo: str) -> pathlib.Path:
    return pathlib.Path(DEFAULT_CACHE_DIR) / f"pr_weekly_{repo}_cache.json"


def _default_output_path(repo: str) -> pathlib.Path:
    return pathlib.Path(DEFAULT_CACHE_DIR) / f"pr_weekly_{repo}.md"


def _build_parser(token_default: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-weekly-table",
        description=(
            "Build a Markdown table summarizing pull requests created over the past week."
        ),
    )
    parser.add_argument("owner", help="GitHub repository owner")
    parser.add_argument("repo", help="GitHub repository name")
    parser.add_argument("--token", default=token_default, help="GitHub personal access token")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GITHUB_API_URL") or "https://api.github.com",
        help="GitHub API base URL",
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "Only include PRs created on/after this date "
            "(YYYY-MM-DD, ISO datetime, or relative values like '-1 day' or '-3d')."
        ),
    )
    parser.add_argument(
        "--cache-file",
        default=None,
        help="Optional cache file path (default: dump_pr_stats/pr_weekly_<repo>_cache.json).",
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="Optional Markdown output path (default: dump_pr_stats/pr_weekly_<repo>.md).",
    )
    parser.add_argument(
        "--copilot",
        action="store_true",
        default=False,
        help="Include Copilot summary/help-needed columns (requires --token or GITHUB_TOKEN).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            "AI model used for --copilot. "
            "When omitted, Copilot chooses the model automatically "
            "and falls back to openai/gpt-4.1 if needed. "
            "Any model available on the GitHub Models API is accepted "
            "(for example: openai/gpt-4o-mini, openai/gpt-4.1, anthropic/claude-3.5-sonnet)."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Print progress information to stderr.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    token_cache = _load_token_cache()
    owner, repo = _extract_owner_repo(argv)
    token_default = os.environ.get("GITHUB_TOKEN") or _resolve_cached_token(
        token_cache, owner, repo, include_classic="--copilot" not in argv
    )
    parser = _build_parser(token_default=token_default)
    args = parser.parse_args(argv)
    cache_path = (
        pathlib.Path(args.cache_file) if args.cache_file else _default_cache_path(args.repo)
    )
    output_path = (
        pathlib.Path(args.output_file) if args.output_file else _default_output_path(args.repo)
    )
    if args.verbose:
        token_origin, token_type = _resolve_token_origin(
            argv,
            args.token,
            os.environ.get("GITHUB_TOKEN"),
            token_cache,
            args.owner,
            args.repo,
        )
        print(
            f"pr-weekly-table: token source={token_origin}, type={token_type}.",
            file=sys.stderr,
        )
        print(
            f"pr-weekly-table: cache file={cache_path}",
            file=sys.stderr,
        )
        print(
            f"pr-weekly-table: output file={output_path}",
            file=sys.stderr,
        )
        print(
            f"pr-weekly-table: collecting pull request data for {args.owner}/{args.repo}...",
            file=sys.stderr,
        )
    reported_copilot_models: set[str] = set()

    def report_copilot_model(model_name: str) -> None:
        if not args.verbose or model_name in reported_copilot_models:
            return
        reported_copilot_models.add(model_name)
        print(f"pr-weekly-table: copilot model={model_name}.", file=sys.stderr)

    if args.copilot and not args.token:
        print(
            "Unable to build weekly PR table (ValueError)\n"
            "A repository token is required for --copilot. "
            "Provide --token/GITHUB_TOKEN or cache a fine-grained token for "
            f"{args.owner}/{args.repo} with github-token --owner/--repo.",
            file=sys.stderr,
        )
        return 1
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cached_rows = _load_cache(cache_path)
    warnings: list[str] = []
    try:
        rows = build_weekly_pr_summary_rows(
            owner=args.owner,
            repo=args.repo,
            token=args.token,
            api_url=args.api_url,
            since=args.since,
            cached_rows=cached_rows,
            copilot=args.copilot,
            model=args.model,
            warnings=warnings,
            on_model_used=report_copilot_model if args.copilot else None,
            verbose=args.verbose,
        )
    except (HTTPError, URLError, OSError, ValueError, IncompleteRead) as e:
        print(f"Unable to build weekly PR table ({type(e).__name__})\n{e}", file=sys.stderr)
        return 1
    _save_cache(cache_path, rows)
    for warning in warnings:
        print(warning, file=sys.stderr)
    output_path.write_text(
        build_weekly_pr_markdown_table(rows, copilot=args.copilot),
        encoding="utf-8",
    )
    if args.verbose:
        print("pr-weekly-table: done.", file=sys.stderr)
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
