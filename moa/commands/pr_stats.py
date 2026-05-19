"""Pull request activity report command line."""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import os
import pathlib
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from html import escape
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

import pandas

from .review_pr import _fetch_json

PAGE_SIZE = 100
COPILOT_COMMAND_RE = re.compile(r"(?:^|\s)(?:@copilot|/copilot)\b", re.IGNORECASE)
DARK_THEME_SVG_CSS = """<style>
@media (prefers-color-scheme: dark){
.bg { fill: #0d1117; }
.label { fill: #e6edf3; }
.axis { stroke: #8b949e; }
.bar { fill: #79c0ff; }
}
</style>"""
DEFAULT_OUTPUT_DIR = "dump_pr_stats"
SVG_LABEL_CHAR_WIDTH = 7
# Keep GraphQL batch sizes moderate to avoid oversized query payloads.
PR_COMMENT_BATCH_SIZE = 20
GRAPHQL_PAGE_SIZE = 100


def _print_progress(current: int, total: int, file: Any = None) -> None:
    """Print a simple ASCII progress bar to *file* (defaults to sys.stderr)."""
    if file is None:
        file = sys.stderr
    bar_width = 30
    filled = int(bar_width * current / total) if total else bar_width
    bar = "=" * filled + "-" * (bar_width - filled)
    end = "\n" if current >= total else "\r"
    print(f"  [{bar}] {current}/{total}", end=end, file=file, flush=True)


def _fetch_paginated(url: str, token: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        page_url = f"{url}{sep}{parse.urlencode({'per_page': PAGE_SIZE, 'page': page})}"
        data = _fetch_json(page_url, token)
        if not isinstance(data, list) or not data:
            break
        if any(not isinstance(item, dict) for item in data):
            raise ValueError("Unexpected paginated payload returned by API.")
        rows.extend(data)
        if len(data) < PAGE_SIZE:
            break
        page += 1
    return rows


def _count_comments(comment_bodies: list[str]) -> tuple[int, int]:
    manual = 0
    copilot = 0
    for body in comment_bodies:
        if COPILOT_COMMAND_RE.search(body or ""):
            copilot += 1
        else:
            manual += 1
    return manual, copilot


def _parse_iso_datetime(value: str) -> datetime:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Datetime value cannot be empty.")
    if "T" not in cleaned:
        cleaned = f"{cleaned}T00:00:00Z"
    return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))


def _default_prefix(repo: str) -> str:
    safe_repo = _safe_repo_name(repo)
    return f"pr_activity_{safe_repo}"


def _safe_repo_name(repo: str) -> str:
    safe_repo = re.sub(r"[^A-Za-z0-9_-]+", "_", repo).strip("_")
    if not safe_repo:
        safe_repo = f"repo_{hashlib.sha256(repo.encode('utf-8')).hexdigest()[:8]}"
    return safe_repo


def _default_since() -> str:
    """Return an ISO date string for 6 months ago (YYYY-MM-DD).

    The day is clamped to the last valid day of the target month to handle
    months shorter than the current one (e.g. March 31 → September 30).
    """
    today = datetime.now(timezone.utc)
    year, month = today.year, today.month - 6
    if month <= 0:
        month += 12
        year -= 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day, tzinfo=timezone.utc).strftime("%Y-%m-%d")


def _load_cache(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    entries = payload.get("rows", {})
    if not isinstance(entries, dict):
        return {}
    return {
        str(pr_number): row
        for pr_number, row in entries.items()
        if isinstance(pr_number, str) and isinstance(row, dict)
    }


def _save_cache(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    payload = {"rows": {str(row["number"]): row for row in rows}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _collect_pr_comment_stats(
    owner: str,
    repo: str,
    pull_number: int,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> tuple[int, int]:
    base = f"{api_url.rstrip('/')}/repos/{owner}/{repo}"
    issue_comments = _fetch_paginated(f"{base}/issues/{pull_number}/comments", token)
    review_comments = _fetch_paginated(f"{base}/pulls/{pull_number}/comments", token)
    reviews = _fetch_paginated(f"{base}/pulls/{pull_number}/reviews", token)
    bodies = [
        str(comment.get("body", ""))
        for comment in [*issue_comments, *review_comments, *reviews]
        if isinstance(comment, dict)
    ]
    return _count_comments(bodies)


def _graphql_api_url(api_url: str) -> str:
    base = api_url.rstrip("/")
    if base.endswith("/graphql"):
        return base
    if base.endswith("/api/v3"):
        return f"{base[:-7]}/api/graphql"
    return f"{base}/graphql"


def _fetch_graphql_json(
    query: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "moa/pr-stats",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps({"query": query}).encode("utf-8")
    req = request.Request(_graphql_api_url(api_url), data=payload, headers=headers, method="POST")
    with request.urlopen(req) as response:
        return json.load(response)


def _build_pr_comments_batch_query(owner: str, repo: str, pull_numbers: list[int]) -> str:
    parts = []
    owner_json = json.dumps(owner)
    repo_json = json.dumps(repo)
    for number in pull_numbers:
        alias = f"pr_{number}"
        parts.append(f"""
      {alias}: pullRequest(number: {number}) {{
        comments(first: {GRAPHQL_PAGE_SIZE}) {{
          totalCount
          nodes {{ body }}
        }}
        reviews(first: {GRAPHQL_PAGE_SIZE}) {{
          totalCount
          nodes {{ body }}
        }}
        reviewThreads(first: {GRAPHQL_PAGE_SIZE}) {{
          totalCount
          nodes {{
            comments(first: {GRAPHQL_PAGE_SIZE}) {{
              totalCount
              nodes {{ body }}
            }}
          }}
        }}
      }}""")
    return (
        "query {"
        f" repository(owner: {owner_json}, name: {repo_json}) {{" + "".join(parts) + " } }"
    )


def _collect_pr_comment_stats_batch(
    owner: str,
    repo: str,
    pull_numbers: list[int],
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> dict[int, tuple[int, int]]:
    if not pull_numbers:
        return {}
    results: dict[int, tuple[int, int]] = {}
    for start in range(0, len(pull_numbers), PR_COMMENT_BATCH_SIZE):
        chunk = pull_numbers[start : start + PR_COMMENT_BATCH_SIZE]
        query = _build_pr_comments_batch_query(owner, repo, chunk)
        fallback = set(chunk)
        try:
            payload = _fetch_graphql_json(query, token=token, api_url=api_url)
            data = payload.get("data") if isinstance(payload, dict) else None
            repository = data.get("repository") if isinstance(data, dict) else None
            if isinstance(repository, dict):
                for number in chunk:
                    alias = f"pr_{number}"
                    pr_data = repository.get(alias)
                    if not isinstance(pr_data, dict):
                        continue
                    bodies: list[str] = []
                    truncated = False
                    comments = pr_data.get("comments")
                    if isinstance(comments, dict):
                        nodes = comments.get("nodes")
                        if isinstance(nodes, list):
                            bodies.extend(
                                str(node.get("body", ""))
                                for node in nodes
                                if isinstance(node, dict)
                            )
                            truncated = truncated or int(comments.get("totalCount", 0)) > len(
                                nodes
                            )
                    reviews = pr_data.get("reviews")
                    if isinstance(reviews, dict):
                        nodes = reviews.get("nodes")
                        if isinstance(nodes, list):
                            bodies.extend(
                                str(node.get("body", ""))
                                for node in nodes
                                if isinstance(node, dict)
                            )
                            truncated = truncated or int(reviews.get("totalCount", 0)) > len(
                                nodes
                            )
                    review_threads = pr_data.get("reviewThreads")
                    if isinstance(review_threads, dict):
                        threads = review_threads.get("nodes")
                        if isinstance(threads, list):
                            truncated = truncated or int(
                                review_threads.get("totalCount", 0)
                            ) > len(threads)
                            for thread in threads:
                                if not isinstance(thread, dict):
                                    continue
                                thread_comments = thread.get("comments")
                                if not isinstance(thread_comments, dict):
                                    continue
                                nodes = thread_comments.get("nodes")
                                if isinstance(nodes, list):
                                    bodies.extend(
                                        str(node.get("body", ""))
                                        for node in nodes
                                        if isinstance(node, dict)
                                    )
                                    truncated = truncated or int(
                                        thread_comments.get("totalCount", 0)
                                    ) > len(nodes)
                    if not truncated:
                        results[number] = _count_comments(bodies)
                        fallback.discard(number)
        except (OSError, ValueError, HTTPError, URLError):
            # Any GraphQL failure falls back to the existing per-PR REST queries below.
            pass
        for number in sorted(fallback):
            try:
                results[number] = _collect_pr_comment_stats(
                    owner=owner,
                    repo=repo,
                    pull_number=number,
                    token=token,
                    api_url=api_url,
                )
            except HTTPError:
                # Keep this PR unresolved here; row-building handles the warning/skip behavior.
                continue
    return results


def _fetch_workflow_run_jobs(
    owner: str,
    repo: str,
    run_id: int,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    base = f"{api_url.rstrip('/')}/repos/{owner}/{repo}"
    while True:
        jobs_url = f"{base}/actions/runs/{run_id}/jobs?" + parse.urlencode(
            {"per_page": PAGE_SIZE, "page": page}
        )
        data = _fetch_json(jobs_url, token)
        if not isinstance(data, dict):
            break
        jobs = data.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            break
        if any(not isinstance(item, dict) for item in jobs):
            raise ValueError("Unexpected workflow jobs payload returned by API.")
        rows.extend(jobs)
        if len(jobs) < PAGE_SIZE:
            break
        page += 1
    return rows


def _fetch_workflow_runs_by_head_sha(
    owner: str,
    repo: str,
    head_sha: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    base = f"{api_url.rstrip('/')}/repos/{owner}/{repo}"
    while True:
        runs_url = f"{base}/actions/runs?" + parse.urlencode(
            {
                "event": "pull_request",
                "head_sha": head_sha,
                "per_page": PAGE_SIZE,
                "page": page,
            }
        )
        data = _fetch_json(runs_url, token)
        if not isinstance(data, dict):
            break
        workflow_runs = data.get("workflow_runs")
        if not isinstance(workflow_runs, list) or not workflow_runs:
            break
        if any(not isinstance(item, dict) for item in workflow_runs):
            raise ValueError("Unexpected workflow runs payload returned by API.")
        rows.extend(workflow_runs)
        if len(workflow_runs) < PAGE_SIZE:
            break
        page += 1
    return rows


def _collect_pr_job_info(
    owner: str,
    repo: str,
    pull_number: int,
    head_sha: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> tuple[int, list[dict[str, Any]]]:
    """Return (total_duration_seconds, successful_job_durations).

    total_duration_seconds counts all jobs regardless of conclusion.
    successful_job_durations is a list of {job_name, completed_at, duration_seconds}
    restricted to jobs whose conclusion is "success".
    """
    if not head_sha:
        return 0, []
    runs = _fetch_workflow_runs_by_head_sha(owner, repo, head_sha, token, api_url)
    total_seconds = 0
    successful_jobs: list[dict[str, Any]] = []
    for run in runs:
        pull_requests = run.get("pull_requests")
        if isinstance(pull_requests, list) and pull_requests:
            if not any(
                isinstance(pr, dict) and int(pr.get("number", 0)) == pull_number
                for pr in pull_requests
            ):
                continue
        run_id = int(run.get("id", 0))
        if run_id <= 0:
            continue
        for job in _fetch_workflow_run_jobs(owner, repo, run_id, token, api_url):
            started_at = str(job.get("started_at", ""))
            completed_at = str(job.get("completed_at", ""))
            if not started_at or not completed_at:
                continue
            started_dt = _parse_iso_datetime(started_at)
            completed_dt = _parse_iso_datetime(completed_at)
            if completed_dt >= started_dt:
                duration = int((completed_dt - started_dt).total_seconds())
                total_seconds += duration
                if job.get("conclusion") == "success":
                    successful_jobs.append(
                        {
                            "job_name": str(job.get("name", "")),
                            "completed_at": completed_at,
                            "duration_seconds": duration,
                        }
                    )
    return total_seconds, successful_jobs


def _collect_pr_job_duration_hours(
    owner: str,
    repo: str,
    pull_number: int,
    head_sha: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> float:
    total_seconds, _ = _collect_pr_job_info(owner, repo, pull_number, head_sha, token, api_url)
    return round(total_seconds / 3600, 2)


def _collect_pr_job_duration_seconds(
    owner: str,
    repo: str,
    pull_number: int,
    head_sha: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
) -> int:
    total_seconds, _ = _collect_pr_job_info(owner, repo, pull_number, head_sha, token, api_url)
    return total_seconds


def _build_pr_activity_row(
    pr: dict[str, Any],
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    comment_stats: tuple[int, int] | None = None,
) -> dict[str, Any]:
    number = int(pr.get("number", 0))
    if comment_stats is None:
        manual_comments, copilot_commands = _collect_pr_comment_stats(
            owner=owner,
            repo=repo,
            pull_number=number,
            token=token,
            api_url=api_url,
        )
    else:
        manual_comments, copilot_commands = comment_stats
    total_job_duration_seconds, successful_job_durations = _collect_pr_job_info(
        owner=owner,
        repo=repo,
        pull_number=number,
        head_sha=str((pr.get("head") or {}).get("sha", "")),
        token=token,
        api_url=api_url,
    )
    total_job_duration_hours = round(total_job_duration_seconds / 3600, 2)
    merged_at = pr.get("merged_at")
    return {
        "number": number,
        "author": (pr.get("user") or {}).get("login", ""),
        "title": pr.get("title", ""),
        "created_at": str(pr.get("created_at", "")),
        "merged_at": merged_at or "",
        "closed_at": pr.get("closed_at", ""),
        "status": "merged" if merged_at else "cancelled",
        "manual_comments": manual_comments,
        "copilot_commands": copilot_commands,
        "total_job_duration_hours": total_job_duration_hours,
        "successful_job_durations": successful_job_durations,
        "html_url": pr.get("html_url", ""),
    }


def build_pr_activity_rows(
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    since: str | None = None,
    cached_rows: dict[str, dict[str, Any]] | None = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    since_dt = _parse_iso_datetime(since) if since else None
    cache = cached_rows or {}
    pulls_url = (
        f"{api_url.rstrip('/')}/repos/{owner}/{repo}/pulls"
        "?state=closed&sort=created&direction=desc"
    )
    pulls = _fetch_paginated(pulls_url, token)
    # Pre-filter to the PRs that will actually be processed so we can show
    # an accurate progress bar total.
    filtered: list[dict[str, Any]] = []
    for pr in pulls:
        if pr.get("state") != "closed":
            continue
        created_at = str(pr.get("created_at", ""))
        if since_dt and created_at and _parse_iso_datetime(created_at) < since_dt:
            continue
        filtered.append(pr)
    total = len(filtered)
    rows: list[dict[str, Any] | None] = [None] * total
    completed = 0
    uncached_numbers = []
    for pr in filtered:
        number = int(pr.get("number", 0))
        if not cache.get(str(number)):
            uncached_numbers.append(number)
    comment_stats = _collect_pr_comment_stats_batch(
        owner=owner,
        repo=repo,
        pull_numbers=uncached_numbers,
        token=token,
        api_url=api_url,
    )
    for i, pr in enumerate(filtered):
        number = int(pr.get("number", 0))
        cached = cache.get(str(number))
        if cached:
            rows[i] = cached
            completed += 1
            if verbose:
                _print_progress(completed, total)
            continue
        try:
            rows[i] = _build_pr_activity_row(
                pr,
                owner,
                repo,
                token,
                api_url,
                comment_stats.get(number),
            )
        except HTTPError as e:
            print(
                f"pr-stats: warning: failed to collect stats for PR #{number} "
                f"(HTTPError {e.code}); continuing with partial data.",
                file=sys.stderr,
            )
        completed += 1
        if verbose:
            _print_progress(completed, total)
    return [row for row in rows if row is not None]


def _save_csv(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "number",
        "author",
        "title",
        "created_at",
        "merged_at",
        "closed_at",
        "status",
        "manual_comments",
        "copilot_commands",
        "total_job_duration_hours",
        "html_url",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _xlsx_safe_text(value: str) -> str:
    # Remove control chars invalid in XML 1.0 (except tab/newline/CR) to prevent XLSX corruption.
    return "".join(
        ch
        for ch in value
        if ch in "\t\n\r" or 0x20 <= ord(ch) <= 0xD7FF or 0xE000 <= ord(ch) <= 0xFFFD
    )


def _xlsx_sanitize_rows(rows: list[dict[str, Any]], headers: list[str]) -> list[dict[str, Any]]:
    sanitized_rows = []
    for row in rows:
        sanitized_row: dict[str, Any] = {}
        for key in headers:
            value = row.get(key, "")
            sanitized_row[key] = _xlsx_safe_text(value) if isinstance(value, str) else value
        sanitized_rows.append(sanitized_row)
    return sanitized_rows


def _week_label(value: str) -> str:
    iso_week = _parse_iso_datetime(value).isocalendar()
    return f"{iso_week.year}-W{iso_week.week:02d}"


def _compute_pr_duration_hours(row: dict[str, Any]) -> float | None:
    """Return hours from created_at to merged_at, or None if data is missing."""
    created_at = str(row.get("created_at", ""))
    merged_at = str(row.get("merged_at", ""))
    if not created_at or not merged_at:
        return None
    dt_created = _parse_iso_datetime(created_at)
    dt_merged = _parse_iso_datetime(merged_at)
    return max(0.0, round((dt_merged - dt_created).total_seconds() / 3600, 2))


def _build_avg_duration_per_user_week_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Average PR duration (created_at→merged_at) per author per merge week, merged PRs only."""
    data: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        merged_at = str(row.get("merged_at", ""))
        if not merged_at:
            continue
        author = str(row.get("author", ""))
        week = _week_label(merged_at)
        duration = _compute_pr_duration_hours(row)
        if duration is None:
            continue
        data.setdefault((author, week), []).append(duration)
    result = []
    for key in sorted(data.keys()):
        author, week = key
        durations = data[key]
        result.append(
            {
                "author": author,
                "week": week,
                "pr_count": len(durations),
                "avg_duration_hours": round(sum(durations) / len(durations), 2),
            }
        )
    return result


def _build_avg_duration_per_user_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Average PR duration per author across all merged PRs."""
    data: dict[str, list[float]] = {}
    for row in rows:
        merged_at = str(row.get("merged_at", ""))
        if not merged_at:
            continue
        author = str(row.get("author", ""))
        duration = _compute_pr_duration_hours(row)
        if duration is None:
            continue
        data.setdefault(author, []).append(duration)
    return [
        {"author": author, "avg_duration_hours": round(sum(ds) / len(ds), 2)}
        for author, ds in sorted(data.items())
    ]


def _build_avg_duration_per_week_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Average PR duration per merge week across all merged PRs."""
    data: dict[str, list[float]] = {}
    for row in rows:
        merged_at = str(row.get("merged_at", ""))
        if not merged_at:
            continue
        week = _week_label(merged_at)
        duration = _compute_pr_duration_hours(row)
        if duration is None:
            continue
        data.setdefault(week, []).append(duration)
    return [
        {"week": week, "avg_duration_hours": round(sum(ds) / len(ds), 2)}
        for week, ds in sorted(data.items())
    ]


def _build_job_duration_sheet_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract successful job duration rows from PR rows, sorted by job_name then completed_at."""
    job_rows: list[dict[str, Any]] = []
    for pr_row in rows:
        pr_number = pr_row.get("number", 0)
        for job in pr_row.get("successful_job_durations", []):
            job_rows.append(
                {
                    "job_name": str(job.get("job_name", "")),
                    "completed_at": str(job.get("completed_at", "")),
                    "duration_seconds": int(job.get("duration_seconds", 0)),
                    "pr_number": int(pr_number),
                }
            )
    return sorted(job_rows, key=lambda r: (str(r["job_name"]), str(r["completed_at"])))


def _compute_moving_average(values: list[float], window: int = 10) -> list[float | None]:
    """Return a moving average of *values* using the given *window* size.

    The first ``window - 1`` elements are ``None`` because there are not yet
    enough prior data points to fill the window.
    """
    result: list[float | None] = []
    for i, _ in enumerate(values):
        if i + 1 < window:
            result.append(None)
        else:
            result.append(sum(values[i + 1 - window : i + 1]) / window)
    return result


def _save_job_duration_line_graph(
    path: pathlib.Path,
    series: list[dict[str, Any]],
    title: str,
    moving_avg_window: int = 10,
) -> None:
    """Save a line-graph SVG of job duration over time with a moving average.

    *series* is a list of ``{completed_at, duration_seconds}`` dicts sorted by
    ``completed_at``.  Data points are plotted at evenly-spaced x positions;
    x-axis labels show the date portion of ``completed_at`` at regular intervals.
    """
    width = 800
    height = 420
    left = 70
    right = 20
    top = 40
    bottom = 120
    plot_w = width - left - right
    plot_h = height - top - bottom

    n = len(series)
    if n == 0:
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f"{DARK_THEME_SVG_CSS}"
            f'<rect class="bg" x="0" y="0" width="{width}" height="{height}" fill="#fff"/>'
            f'<text x="{width / 2}" y="28" text-anchor="middle" font-size="18" '
            f'class="label" fill="#111">{escape(title)}</text>'
            f'<text x="{width / 2}" y="{top + plot_h / 2}" text-anchor="middle" '
            'class="label" fill="#888">No data</text>'
            "</svg>"
        )
        path.write_text(svg, encoding="utf-8")
        return

    values = [int(pt.get("duration_seconds", 0)) for pt in series]
    x_labels_raw = [str(pt.get("completed_at", ""))[:10] for pt in series]
    max_val = max(values) if values else 1
    if max_val == 0:
        max_val = 1

    def x_pos(i: int) -> float:
        return left + (i * plot_w / (n - 1) if n > 1 else plot_w / 2)

    def y_pos(v: float) -> float:
        return top + plot_h - (v / max_val) * plot_h

    # Data line
    points_str = " ".join(f"{x_pos(i):.1f},{y_pos(v):.1f}" for i, v in enumerate(values))

    # Moving average line
    avg = _compute_moving_average([float(v) for v in values], moving_avg_window)
    avg_pairs = [(x_pos(i), y_pos(v)) for i, v in enumerate(avg) if v is not None]
    avg_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in avg_pairs)

    # X-axis labels (every max(1, n//8)-th point, rotated 45°)
    step = max(1, n // 8)
    x_label_elems = []
    for i in range(0, n, step):
        x = x_pos(i)
        x_label_elems.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 20}" text-anchor="end" '
            f'transform="rotate(-45 {x:.1f} {top + plot_h + 20})" '
            f'class="label" fill="#111" font-size="11">{escape(x_labels_raw[i])}</text>'
        )

    # Y-axis ticks (5 levels)
    y_tick_elems = []
    for tick_i in range(5):
        tick_val = max_val * tick_i / 4
        y = y_pos(tick_val)
        y_tick_elems.append(
            f'<line x1="{left - 5}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" '
            f'class="axis" stroke="#000"/>'
            f'<text x="{left - 8}" y="{y:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" class="label" fill="#111" '
            f'font-size="11">{int(tick_val)}</text>'
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            f'stroke="#ddd" stroke-dasharray="4,4"/>'
        )

    # Legend
    legend_x = width - right - 130
    legend_y = top + 10
    legend_elems = (
        f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 20}" y2="{legend_y}" '
        f'stroke="#4e79a7" stroke-width="2"/>'
        f'<text x="{legend_x + 25}" y="{legend_y + 4}" font-size="11" '
        f'class="label" fill="#111">duration</text>'
    )
    if avg_str:
        legend_elems += (
            f'<line x1="{legend_x}" y1="{legend_y + 18}" '
            f'x2="{legend_x + 20}" y2="{legend_y + 18}" '
            f'stroke="#e05c5c" stroke-width="2" stroke-dasharray="5,3"/>'
            f'<text x="{legend_x + 25}" y="{legend_y + 22}" font-size="11" '
            f'class="label" fill="#111">avg-{moving_avg_window}</text>'
        )

    dots = "".join(
        f'<circle cx="{x_pos(i):.1f}" cy="{y_pos(v):.1f}" r="3" fill="#4e79a7"/>'
        for i, v in enumerate(values)
    )
    avg_line = (
        f'<polyline points="{avg_str}" fill="none" stroke="#e05c5c" '
        f'stroke-width="2" stroke-dasharray="5,3"/>'
        if avg_str
        else ""
    )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f"{DARK_THEME_SVG_CSS}"
        f'<rect class="bg" x="0" y="0" width="{width}" height="{height}" fill="#fff"/>'
        f'<text x="{width / 2}" y="28" text-anchor="middle" font-size="18" '
        f'class="label" fill="#111">{escape(title)}</text>'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" '
        f'class="axis" stroke="#000"/>'
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" '
        f'class="axis" stroke="#000"/>'
        + "".join(y_tick_elems)
        + f'<polyline points="{points_str}" fill="none" stroke="#4e79a7" stroke-width="2"/>'
        + dots
        + avg_line
        + "".join(x_label_elems)
        + legend_elems
        + "</svg>"
    )
    path.write_text(svg, encoding="utf-8")


def _build_comments_per_pr_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "number": row.get("number", 0),
            "title": row.get("title", ""),
            "created_at": row.get("created_at", ""),
            "manual_comments": row.get("manual_comments", 0),
            "copilot_commands": row.get("copilot_commands", 0),
            "total_comments": int(row.get("manual_comments", 0))
            + int(row.get("copilot_commands", 0)),
        }
        for row in sorted(rows, key=lambda item: int(item.get("number", 0)))
    ]


def _build_pr_comments_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Return a mapping from total-comment count to number of PRs with that count.

    Keys are the number of comments (as strings), values are PR counts, sorted
    by ascending comment count.
    """
    distribution: Counter[int] = Counter()
    for row in rows:
        total = int(row.get("manual_comments", 0)) + int(row.get("copilot_commands", 0))
        distribution[total] += 1
    return {str(count): pr_count for count, pr_count in sorted(distribution.items())}


def _build_prs_per_week_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prs_per_week: Counter[str] = Counter()
    for row in rows:
        created_at = str(row.get("created_at", ""))
        if not created_at:
            continue
        prs_per_week[_week_label(created_at)] += 1
    return [
        {"week": week, "pull_requests": count} for week, count in sorted(prs_per_week.items())
    ]


def _build_comments_per_week_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    comments_per_week: dict[str, dict[str, Any]] = {}
    for row in rows:
        created_at = str(row.get("created_at", ""))
        if not created_at:
            continue
        week = _week_label(created_at)
        weekly = comments_per_week.setdefault(
            week,
            {
                "week": week,
                "manual_comments": 0,
                "copilot_commands": 0,
                "total_comments": 0,
            },
        )
        manual_comments = int(row.get("manual_comments", 0))
        copilot_commands = int(row.get("copilot_commands", 0))
        weekly["manual_comments"] += manual_comments
        weekly["copilot_commands"] += copilot_commands
        weekly["total_comments"] += manual_comments + copilot_commands
    return [comments_per_week[week] for week in sorted(comments_per_week)]


def _save_xlsx(path: pathlib.Path, rows: list[dict[str, Any]]) -> None:
    headers = [
        "number",
        "author",
        "title",
        "created_at",
        "merged_at",
        "closed_at",
        "status",
        "manual_comments",
        "copilot_commands",
        "total_job_duration_hours",
        "html_url",
    ]
    comments_per_pr_headers = [
        "number",
        "title",
        "created_at",
        "manual_comments",
        "copilot_commands",
        "total_comments",
    ]
    comments_per_week_headers = [
        "week",
        "manual_comments",
        "copilot_commands",
        "total_comments",
    ]
    avg_duration_headers = [
        "author",
        "week",
        "pr_count",
        "avg_duration_hours",
    ]
    job_duration_headers = [
        "job_name",
        "completed_at",
        "duration_seconds",
        "pr_number",
    ]
    with pandas.ExcelWriter(path, engine="openpyxl") as writer:
        pandas.DataFrame(_xlsx_sanitize_rows(rows, headers), columns=headers).to_excel(
            writer, index=False, sheet_name="PR activity"
        )
        pandas.DataFrame(
            _xlsx_sanitize_rows(_build_prs_per_week_rows(rows), ["week", "pull_requests"]),
            columns=["week", "pull_requests"],
        ).to_excel(writer, index=False, sheet_name="PRs per week")
        pandas.DataFrame(
            _xlsx_sanitize_rows(_build_comments_per_pr_rows(rows), comments_per_pr_headers),
            columns=comments_per_pr_headers,
        ).to_excel(writer, index=False, sheet_name="Comments per PR")
        pandas.DataFrame(
            _xlsx_sanitize_rows(_build_comments_per_week_rows(rows), comments_per_week_headers),
            columns=comments_per_week_headers,
        ).to_excel(writer, index=False, sheet_name="Comments per week")
        distribution = _build_pr_comments_distribution(rows)
        pandas.DataFrame(
            _xlsx_sanitize_rows(
                [{"total_comments": int(k), "pull_requests": v} for k, v in distribution.items()],
                ["total_comments", "pull_requests"],
            ),
            columns=["total_comments", "pull_requests"],
        ).to_excel(writer, index=False, sheet_name="Comments distribution")
        pandas.DataFrame(
            _xlsx_sanitize_rows(
                _build_avg_duration_per_user_week_rows(rows), avg_duration_headers
            ),
            columns=avg_duration_headers,
        ).to_excel(writer, index=False, sheet_name="Avg PR duration")
        pandas.DataFrame(
            _xlsx_sanitize_rows(_build_job_duration_sheet_rows(rows), job_duration_headers),
            columns=job_duration_headers,
        ).to_excel(writer, index=False, sheet_name="Job durations")


def _save_bar_graph(path: pathlib.Path, values: dict[str, int], title: str) -> None:
    if not values:
        values = {"none": 0}
    max_value = max(values.values()) if values else 0
    max_label_len = max(map(len, values), default=0)
    bar_width = 80
    gap = 40
    left = max(60, 20 + max_label_len * SVG_LABEL_CHAR_WIDTH)
    baseline = 300
    width = max(600, left + len(values) * (bar_width + gap) + 20)
    scale = 200 / max_value if max_value else 0
    bars = []
    labels = []
    for i, (label, value) in enumerate(values.items()):
        x = left + i * (bar_width + gap)
        height = int(value * scale) if max_value else 0
        y = baseline - height
        bars.append(
            f'<rect x="{x}" y="{y}" width="{bar_width}" height="{height}" '
            'class="bar" fill="#4e79a7"/>'
        )
        labels.append(
            f'<text x="{x + bar_width / 2}" y="{baseline + 20}" text-anchor="end" '
            f'transform="rotate(-30 {x + bar_width / 2} {baseline + 20})" '
            'class="label" fill="#111">'
            f"{escape(label)}</text>"
        )
        labels.append(
            f'<text x="{x + bar_width / 2}" y="{y - 6}" '
            'text-anchor="middle" class="label" fill="#111">'
            f"{value}</text>"
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="360">'
        f"{DARK_THEME_SVG_CSS}"
        f'<rect class="bg" x="0" y="0" width="{width}" height="360" fill="#fff"/>'
        f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18" '
        f'class="label" fill="#111">{escape(title)}</text>'
        f'<line x1="{left - 20}" y1="{baseline}" '
        f'x2="{width - 20}" y2="{baseline}" class="axis" stroke="#000"/>'
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )
    path.write_text(svg, encoding="utf-8")


def save_pr_activity_report(
    owner: str,
    repo: str,
    output_dir: str,
    prefix: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    since: str | None = None,
    cache_file: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    graph_dir = out / f"graphs_{_safe_repo_name(repo)}"
    graph_dir.mkdir(parents=True, exist_ok=True)
    cache_path = pathlib.Path(cache_file) if cache_file else out / f"{prefix}_cache.json"
    cached_rows = _load_cache(cache_path)
    rows = build_pr_activity_rows(
        owner=owner,
        repo=repo,
        token=token,
        api_url=api_url,
        since=since,
        cached_rows=cached_rows,
        verbose=verbose,
    )
    csv_path = out / f"{prefix}.csv"
    xlsx_path = out / f"{prefix}.xlsx"
    status_svg_path = graph_dir / f"{prefix}_status.svg"
    comments_svg_path = graph_dir / f"{prefix}_comments.svg"
    prs_per_week_svg_path = graph_dir / f"{prefix}_prs_per_week.svg"
    comments_per_pr_svg_path = graph_dir / f"{prefix}_comments_per_pr.svg"
    comments_per_week_svg_path = graph_dir / f"{prefix}_comments_per_week.svg"
    avg_duration_per_user_svg_path = graph_dir / f"{prefix}_avg_duration_per_user.svg"
    avg_duration_per_week_svg_path = graph_dir / f"{prefix}_avg_duration_per_week.svg"
    _save_cache(cache_path, rows)
    _save_csv(csv_path, rows)
    _save_xlsx(xlsx_path, rows)
    status_counts = Counter(row["status"] for row in rows)
    _save_bar_graph(status_svg_path, dict(status_counts), "Pull requests by status")
    total_manual = sum(int(row["manual_comments"]) for row in rows)
    total_copilot = sum(int(row["copilot_commands"]) for row in rows)
    _save_bar_graph(
        comments_svg_path,
        {"manual_comments": total_manual, "copilot_commands": total_copilot},
        "Manual comments vs Copilot commands",
    )
    _save_bar_graph(
        prs_per_week_svg_path,
        {row["week"]: int(row["pull_requests"]) for row in _build_prs_per_week_rows(rows)},
        "Pull requests per week",
    )
    _save_bar_graph(
        comments_per_pr_svg_path,
        _build_pr_comments_distribution(rows),
        "PR count by number of comments",
    )
    _save_bar_graph(
        comments_per_week_svg_path,
        {row["week"]: int(row["total_comments"]) for row in _build_comments_per_week_rows(rows)},
        "Comments per week",
    )
    _save_bar_graph(
        avg_duration_per_user_svg_path,
        {
            row["author"]: row["avg_duration_hours"]
            for row in _build_avg_duration_per_user_rows(rows)
        },
        "Avg PR duration per user (hours)",
    )
    _save_bar_graph(
        avg_duration_per_week_svg_path,
        {
            row["week"]: row["avg_duration_hours"]
            for row in _build_avg_duration_per_week_rows(rows)
        },
        "Avg PR duration per week (hours)",
    )
    # Per-job-name duration line graphs (successful jobs only)
    job_duration_svgs: dict[str, pathlib.Path] = {}
    jobs_by_name: dict[str, list[dict[str, Any]]] = {}
    for jd in sorted(_build_job_duration_sheet_rows(rows), key=lambda r: str(r["completed_at"])):
        jobs_by_name.setdefault(str(jd["job_name"]), []).append(jd)
    for job_name, job_series in jobs_by_name.items():
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", job_name).strip("_")
        if not safe_name:
            safe_name = f"job_{hashlib.sha256(job_name.encode('utf-8')).hexdigest()[:8]}"
        svg_path = graph_dir / f"{prefix}_job_duration_{safe_name}.svg"
        _save_job_duration_line_graph(svg_path, job_series, f"Job duration: {job_name}")
        job_duration_svgs[job_name] = svg_path
    return {
        "csv": csv_path,
        "xlsx": xlsx_path,
        "status_svg": status_svg_path,
        "comments_svg": comments_svg_path,
        "prs_per_week_svg": prs_per_week_svg_path,
        "comments_per_pr_svg": comments_per_pr_svg_path,
        "comments_per_week_svg": comments_per_week_svg_path,
        "avg_duration_per_user_svg": avg_duration_per_user_svg_path,
        "avg_duration_per_week_svg": avg_duration_per_week_svg_path,
        "job_duration_svgs": job_duration_svgs,
        "cache": cache_path,
    }


def _build_parser() -> argparse.ArgumentParser:
    token_default = os.environ.get("GITHUB_TOKEN") or None
    api_url_default = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
    parser = argparse.ArgumentParser(
        description=(
            "Builds a report of completed pull requests with author, "
            "manual comments, Copilot commands, dates, and output files."
        )
    )
    parser.add_argument("owner", help="GitHub repository owner")
    parser.add_argument("repo", help="GitHub repository name")
    parser.add_argument("--token", default=token_default, help="GitHub personal access token")
    parser.add_argument("--api-url", default=api_url_default, help="GitHub API base URL")
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory where output files are written (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Filename prefix for generated files (default: pr_activity_<repo>).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "Only include PRs created on/after this datetime "
            "(YYYY-MM-DD or ISO 8601 datetime). "
            "Default: 6 months ago."
        ),
    )
    parser.add_argument(
        "--cache-file",
        default=None,
        help=(
            "Optional cache file path for PR statistics. "
            "Defaults to <output-dir>/<prefix>_cache.json."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = _build_parser()
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Print progress information to stderr.",
    )
    args = parser.parse_args(argv)
    prefix = args.prefix or _default_prefix(args.repo)
    since = args.since or _default_since()
    if args.verbose:
        print(
            f"pr-stats: collecting pull request data for {args.owner}/{args.repo}...",
            file=sys.stderr,
        )
    try:
        outputs = save_pr_activity_report(
            owner=args.owner,
            repo=args.repo,
            output_dir=args.output_dir,
            prefix=prefix,
            token=args.token,
            api_url=args.api_url,
            since=since,
            cache_file=args.cache_file,
            verbose=args.verbose,
        )
    except (HTTPError, URLError, OSError, ValueError) as e:
        print(
            f"Unable to build pull request activity report ({type(e).__name__}).",
            file=sys.stderr,
        )
        return 1
    if args.verbose:
        print("pr-stats: done.", file=sys.stderr)
    for _, value in outputs.items():
        if isinstance(value, dict):
            for path in value.values():
                print(path)
        else:
            print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
