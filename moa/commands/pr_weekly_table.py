"""Weekly pull request summary command line."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

from .pr_stats import _fetch_paginated
from .review_pr import DEFAULT_MODEL, _send_chat_request
from .review_token import (
    _extract_owner_repo,
    _resolve_cached_token,
)
from .review_token import (
    _load_cache as _load_token_cache,
)

DEFAULT_CACHE_DIR = "dump_pr_stats"

_FAILING_STATUS_STATES = {"error", "failure"}


def _fetch_json(url: str, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "moa/pr-weekly-table",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers)
    with request.urlopen(req) as response:
        return json.load(response)


def _default_since() -> str:
    """Return an ISO date string for one week ago (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")


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
    return {k: v for k, v in rows.items() if isinstance(k, str) and isinstance(v, dict)}


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
    reviews = _fetch_paginated(
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
    model: str = DEFAULT_MODEL,
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
    raw = _send_chat_request(messages, token, model=model, command_name="pr-weekly-table")
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


def build_weekly_pr_summary_rows(
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    since: str | None = None,
    cached_rows: dict[str, dict[str, Any]] | None = None,
    copilot: bool = False,
    model: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    since_value = since or _default_since()
    since_dt = datetime.fromisoformat(since_value.replace("Z", "+00:00"))
    pulls_url = (
        f"{api_url.rstrip('/')}/repos/{owner}/{repo}/pulls"
        "?state=all&sort=created&direction=desc"
    )
    pulls = _fetch_paginated(pulls_url, token)
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
            summary, help_needed = _call_copilot_summary(row, token=token or "", model=model)
            row["copilot_summary"] = summary
            row["help_needed"] = help_needed
        rows.append(row)
    return rows


def _escape_markdown(value: Any) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def build_weekly_pr_markdown_table(rows: list[dict[str, Any]], copilot: bool = False) -> str:
    headers = [
        "Title",
        "Author",
        "Created",
        "Last update",
        "Link",
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
            _escape_markdown(row.get("link", "")),
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
        help="Only include PRs created on/after this date (YYYY-MM-DD or ISO datetime).",
    )
    parser.add_argument(
        "--cache-file",
        default=None,
        help="Optional cache file path (default: dump_pr_stats/pr_weekly_<repo>_cache.json).",
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
        help=f"AI model used for --copilot (default: {DEFAULT_MODEL}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    token_cache = _load_token_cache()
    owner, repo = _extract_owner_repo(argv)
    token_default = os.environ.get("GITHUB_TOKEN") or _resolve_cached_token(
        token_cache, owner, repo
    )
    parser = _build_parser(token_default=token_default)
    args = parser.parse_args(argv)
    if args.copilot and not args.token:
        print(
            "Unable to build weekly PR table (ValueError)\n"
            "A GitHub token (--token or GITHUB_TOKEN env var) is required for --copilot.",
            file=sys.stderr,
        )
        return 1
    cache_path = (
        pathlib.Path(args.cache_file)
        if args.cache_file
        else pathlib.Path(DEFAULT_CACHE_DIR) / f"pr_weekly_{args.repo}_cache.json"
    )
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cached_rows = _load_cache(cache_path)
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
        )
    except (HTTPError, URLError, OSError, ValueError) as e:
        print(f"Unable to build weekly PR table ({type(e).__name__})\n{e}", file=sys.stderr)
        return 1
    _save_cache(cache_path, rows)
    print(build_weekly_pr_markdown_table(rows, copilot=args.copilot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
