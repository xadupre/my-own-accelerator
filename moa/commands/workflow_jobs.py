"""Workflow jobs command line with queued, running, duration, waiting, and fail-rate reports."""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from urllib import parse
from urllib.error import HTTPError

import pandas

from .pr_stats import _print_progress, _xlsx_sanitize_rows
from .pr_stats_graphs import save_bar_graph, save_graphs_html_report, save_job_duration_line_graph
from .review_pr import _fetch_json
from .review_token import (
    _extract_owner_repo,
    _fetch_token_from_gh_cli,
    _resolve_cached_token,
    _resolve_token_origin,
)
from .review_token import (
    _load_cache as _load_token_cache,
)
from .since_utils import parse_relative_since

DEFAULT_OUTPUT_DIR = "dump_pr_stats"
_FAIL_RATE_STATUSES = ("failure", "cancelled", "skipped", "success")
_DEFAULT_SINCE_DAYS = 60
_WORKFLOW_RUNS_DAY_CACHE_VERSION = 2
_DURATION_OUTLIER_MULTIPLIER = 3


def _default_since() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=_DEFAULT_SINCE_DAYS)).strftime("%Y-%m-%d")


def _print_403_retry_warning(verbose: bool, run_id: int | None = None) -> None:
    if not verbose:
        return
    details = f" for run_id={run_id}" if run_id is not None else ""
    print(
        "workflow-jobs: warning: received HTTP 403 with token; "
        f"retrying without token{details}.",
        file=sys.stderr,
    )


def _print_verbose_step(verbose: bool, message: str) -> None:
    if not verbose:
        return
    print(f"workflow-jobs: {message}", file=sys.stderr, flush=True)


def _parse_since_datetime(value: str | None) -> datetime:
    since_value = (value or _default_since()).strip()
    if since_value.isdigit():
        return datetime.now(timezone.utc) - timedelta(days=int(since_value))
    relative_dt = parse_relative_since(since_value)
    if relative_dt is not None:
        return relative_dt
    parsed = datetime.fromisoformat(since_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_optional_github_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _date_range_text(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> str:
    dates = [
        parsed
        for row in rows
        for key in keys
        if (parsed := _parse_optional_github_datetime(row.get(key))) is not None
    ]
    if not dates:
        return ""
    return f", min(date)={min(dates).isoformat()}, max(date)={max(dates).isoformat()}"


def _load_rows_cache(
    path: pathlib.Path,
    meta: dict[str, Any],
    verbose: bool = False,
) -> list[dict[str, Any]] | None:
    """Load cached rows when the file's ``meta`` dictionary exactly matches the query metadata."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("meta") != meta:
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    _print_verbose_step(verbose, f"using cache {path}")
    return rows


def _save_rows_cache(
    path: pathlib.Path,
    meta: dict[str, Any],
    rows: list[dict[str, Any]],
    verbose: bool = False,
) -> None:
    """Write query ``meta`` and fetched rows to disk, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _print_verbose_step(verbose, f"writing cache {path}...")
    path.write_text(json.dumps({"meta": meta, "rows": rows}, indent=2), encoding="utf-8")


def _repo_cache_slug(owner: str, repo: str) -> str:
    """Create a filesystem-safe owner/repo identifier for cache filenames."""
    return f"{_safe_name(owner)}_{_safe_name(repo)}"


def _cache_day_text(day: datetime | str) -> str:
    if isinstance(day, str):
        return day
    return day.astimezone(timezone.utc).date().isoformat()


def _github_datetime_text(value: datetime) -> str:
    """Format a datetime for GitHub API filters using UTC with a trailing ``Z``."""
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _workflow_jobs_cache_dir(output_dir: str, owner: str, repo: str) -> pathlib.Path:
    return pathlib.Path(output_dir) / "workflow_jobs_cache" / _repo_cache_slug(owner, repo)


def _workflow_run_cache_day(run: dict[str, Any]) -> str | None:
    for key in ("created_at", "run_started_at", "updated_at"):
        parsed = _parse_optional_github_datetime(run.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc).date().isoformat()
    return None


def _workflow_runs_day_meta(
    owner: str, repo: str, status: str | None, day: str
) -> dict[str, Any]:
    return {
        "kind": "workflow_runs_day",
        "version": _WORKFLOW_RUNS_DAY_CACHE_VERSION,
        "owner": owner,
        "repo": repo,
        "status": status,
        "day": day,
    }


def _workflow_runs_cache_path(
    output_dir: str,
    owner: str,
    repo: str,
    status: str | None,
    day: datetime | str,
) -> pathlib.Path:
    """Build a workflow-runs daily cache path scoped to owner/repo, status, and day."""
    status_slug = _safe_name(status or "all")
    stamp = _cache_day_text(day).replace("-", "")
    return _workflow_jobs_cache_dir(output_dir, owner, repo) / f"runs_{status_slug}_{stamp}.json"


def _workflow_runs_cache_paths(
    output_dir: str,
    owner: str,
    repo: str,
    status: str | None,
    stop_before: datetime,
    now: datetime | None = None,
) -> list[pathlib.Path]:
    """Build the daily workflow-runs cache paths for the full requested date range."""
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    start = stop_before.astimezone(timezone.utc).date()
    if start > today:
        return []
    return [
        _workflow_runs_cache_path(
            output_dir,
            owner,
            repo,
            status,
            (start + timedelta(days=offset)).isoformat(),
        )
        for offset in range((today - start).days + 1)
    ]


def _workflow_runs_cache_path_day(path: pathlib.Path) -> str:
    """Extract ``YYYY-MM-DD`` from a cache path like ``runs_completed_YYYYMMDD.json``."""
    stamp = path.stem.rsplit("_", 1)[-1]
    if len(stamp) != 8 or not stamp.isdigit():
        raise ValueError(f"Unexpected workflow-runs cache path: {path}")
    datetime.strptime(stamp, "%Y%m%d")
    day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
    return day


def _cache_day_start(day: str) -> datetime:
    """Convert a cached ``YYYY-MM-DD`` UTC day to a midnight UTC datetime."""
    return datetime.fromisoformat(f"{day}T00:00:00+00:00")


def _cache_day_end(day: str) -> datetime:
    """Return the exclusive UTC end for a cached ``YYYY-MM-DD`` day."""
    return _cache_day_start(day) + timedelta(days=1)


def _github_datetime_range_text(start: datetime, end: datetime) -> str:
    """Format an inclusive GitHub API range from ``start`` to ``end``."""
    return f"{_github_datetime_text(start)}..{_github_datetime_text(end)}"


def _merge_cached_run_rows(
    rows: list[dict[str, Any]], cached_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Preserve cached per-run job data when refreshing workflow-run rows."""
    cached_by_id = {
        run_id: cached_run
        for cached_run in cached_rows
        if isinstance((run_id := cached_run.get("id")), int)
    }
    merged: list[dict[str, Any]] = []
    for run in rows:
        run_id = run.get("id")
        if not isinstance(run_id, int):
            merged.append(run)
            continue
        cached_run = cached_by_id.get(run_id)
        if cached_run is not None and "jobs" in cached_run and "jobs" not in run:
            merged.append({**run, "jobs": cached_run["jobs"]})
            continue
        merged.append(run)
    return merged


def _fetch_workflow_runs_by_day(
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    status: str | None = None,
    verbose: bool = False,
    stop_before: datetime | None = None,
    cache_dir: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if stop_before is None or cache_dir is None:
        return []
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    start = stop_before.astimezone(timezone.utc).date()
    if start > today:
        return []
    rows: list[dict[str, Any]] = []
    current_day = today.isoformat()
    for offset in range((today - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        path = _workflow_runs_cache_path(cache_dir, owner, repo, status, day)
        cached_rows = _load_workflow_runs_day_rows(path, owner, repo, day, verbose)
        if cached_rows is not None and day != current_day:
            rows.extend(cached_rows)
            continue
        if day == current_day:
            _print_verbose_step(verbose, f"refreshing workflow runs cache for current day {day}")
        else:
            _print_verbose_step(verbose, f"fetching workflow runs cache for day {day}")
        fetched_rows = _fetch_workflow_runs(
            owner,
            repo,
            token=token,
            api_url=api_url,
            status=status,
            verbose=verbose,
            stop_before=_cache_day_start(day),
            stop_after=_cache_day_end(day),
        )
        if cached_rows is not None:
            fetched_rows = _merge_cached_run_rows(fetched_rows, cached_rows)
        _save_rows_cache(
            path,
            _workflow_runs_day_meta(owner, repo, status, day),
            fetched_rows,
            verbose,
        )
        rows.extend(fetched_rows)
    return rows


def _run_jobs_cache_path(output_dir: str, owner: str, repo: str, run_id: int) -> pathlib.Path:
    """Build a per-run jobs cache path scoped to owner/repo and run identifier."""
    return _workflow_jobs_cache_dir(output_dir, owner, repo) / f"run_{run_id}_jobs.json"


def _workflow_runs_cache_day_candidates(
    output_dir: str, owner: str, repo: str, day: str
) -> list[pathlib.Path]:
    """Return all daily run-cache files for a given day, regardless of cached status."""
    stamp = _cache_day_text(day).replace("-", "")
    cache_dir = _workflow_jobs_cache_dir(output_dir, owner, repo)
    if not cache_dir.exists():
        return []
    return sorted(cache_dir.glob(f"runs_*_{stamp}.json"))


def _load_workflow_runs_day_rows(
    path: pathlib.Path, owner: str, repo: str, day: str, verbose: bool = False
) -> list[dict[str, Any]] | None:
    """Load cached workflow-run rows for one daily cache file.

    The file must match the expected owner, repository, and UTC day metadata.
    Returns the validated row list when the cache file is usable, otherwise
    returns ``None``.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    if (
        meta.get("kind") != "workflow_runs_day"
        or meta.get("version") != _WORKFLOW_RUNS_DAY_CACHE_VERSION
        or meta.get("owner") != owner
        or meta.get("repo") != repo
        or meta.get("day") != day
    ):
        return None
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return None
    _print_verbose_step(verbose, f"using cache {path}")
    return [row for row in rows if isinstance(row, dict)]


def _fetch_workflow_runs(
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    status: str | None = None,
    verbose: bool = False,
    stop_before: datetime | None = None,
    stop_after: datetime | None = None,
    cache_path: pathlib.Path | None = None,
    cache_dir: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if cache_dir is not None and stop_before is not None and stop_after is None:
        return _fetch_workflow_runs_by_day(
            owner,
            repo,
            token=token,
            api_url=api_url,
            status=status,
            verbose=verbose,
            stop_before=stop_before,
            cache_dir=cache_dir,
            now=now,
        )
    meta = {
        "kind": "workflow_runs",
        "owner": owner,
        "repo": repo,
        "status": status,
        "stop_before": stop_before.isoformat() if stop_before is not None else None,
        "stop_after": stop_after.isoformat() if stop_after is not None else None,
    }
    cached_rows = _load_rows_cache(cache_path, meta, verbose) if cache_path is not None else None
    if cached_rows is not None:
        return cached_rows
    fetch_stop_before = stop_before
    rows: list[dict[str, Any]] = []

    page = 1
    while True:
        _print_verbose_step(verbose, f"fetching workflow runs page {page}")
        query: dict[str, Any] = {"per_page": 100, "page": page}
        if status:
            query["status"] = status
        if fetch_stop_before is not None and stop_after is not None:
            query["created"] = _github_datetime_range_text(
                fetch_stop_before, stop_after - timedelta(seconds=1)
            )
        elif fetch_stop_before is not None:
            query["created"] = f">={_github_datetime_text(fetch_stop_before)}"
        elif stop_after is not None:
            query["created"] = f"<={_github_datetime_text(stop_after)}"
        url = f"{api_url.rstrip('/')}/repos/{owner}/{repo}/actions/runs?{parse.urlencode(query)}"
        try:
            payload = _fetch_json(url, token)
        except HTTPError as e:
            if e.code != 403:
                raise
            _print_403_retry_warning(verbose)
            payload = _fetch_json(url, None)
        if not isinstance(payload, dict):
            break
        runs = payload.get("workflow_runs", [])
        if not isinstance(runs, list) or not runs:
            break
        current_runs = [run for run in runs if isinstance(run, dict)]
        older_dates: list[datetime] = []
        if fetch_stop_before is not None:
            filtered_runs: list[dict[str, Any]] = []
            for run in current_runs:
                # Keep a client-side bound as a fallback in case the API ignores the
                # `created>=...` filter or returns rows without a usable timestamp.
                # `--since` is an inclusive lower bound, so rows exactly at
                # `stop_before` remain in the result set.
                created_at = _parse_optional_github_datetime(run.get("created_at"))
                if created_at is not None and created_at < fetch_stop_before:
                    older_dates.append(created_at)
                    continue
                if created_at is not None and stop_after is not None and created_at >= stop_after:
                    continue
                filtered_runs.append(run)
            current_runs = filtered_runs
        rows.extend(current_runs)
        if cache_path is not None:
            _save_rows_cache(cache_path, meta, rows, verbose)
        _print_verbose_step(
            verbose,
            f"fetched {len(current_runs)} workflow run(s) from page {page}"
            f"{_date_range_text(current_runs, ('created_at', 'run_started_at', 'updated_at'))}",
        )
        if fetch_stop_before is not None and stop_after is None:
            dates = [
                parsed
                for run in current_runs
                if (parsed := _parse_optional_github_datetime(run.get("created_at"))) is not None
            ]
            stop_date = None
            if older_dates:
                stop_date = min(older_dates)
            elif dates:
                stop_date = min(dates)
            should_stop = bool(older_dates) or (
                stop_date is not None and stop_date < fetch_stop_before
            )
            if should_stop and stop_date is not None:
                _print_verbose_step(
                    verbose,
                    "stopping workflow runs fetch on page "
                    f"{page} because min(date)={stop_date.isoformat()} "
                    f"is older than since={fetch_stop_before.isoformat()}",
                )
                break
        if len(runs) < 100:
            break
        page += 1
    if cache_path is not None:
        _save_rows_cache(cache_path, meta, rows, verbose)
    return rows


def _fetch_run_jobs(
    owner: str,
    repo: str,
    run_id: int,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
    cache_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    meta = {"kind": "run_jobs", "owner": owner, "repo": repo, "run_id": run_id}
    if (
        cache_path is not None
        and (cached_rows := _load_rows_cache(cache_path, meta, verbose)) is not None
    ):
        return cached_rows
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        _print_verbose_step(verbose, f"fetching jobs page {page} for run_id={run_id}")
        url = (
            f"{api_url.rstrip('/')}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?"
            f"{parse.urlencode({'per_page': 100, 'page': page})}"
        )
        try:
            payload = _fetch_json(url, token)
        except HTTPError as e:
            if e.code != 403:
                raise
            _print_403_retry_warning(verbose, run_id=run_id)
            payload = _fetch_json(url, None)
        if not isinstance(payload, dict):
            break
        jobs = payload.get("jobs", [])
        if not isinstance(jobs, list) or not jobs:
            break
        current_jobs = [job for job in jobs if isinstance(job, dict)]
        rows.extend(current_jobs)
        if cache_path is not None:
            _save_rows_cache(cache_path, meta, rows, verbose)
        _print_verbose_step(
            verbose,
            f"fetched {len(current_jobs)} job(s) from page {page} for run_id={run_id}"
            f"{_date_range_text(current_jobs, ('started_at', 'completed_at', 'created_at'))}",
        )
        if len(jobs) < 100:
            break
        page += 1
    if cache_path is not None:
        _save_rows_cache(cache_path, meta, rows, verbose)
    return rows


def _load_cached_run_jobs_from_day(
    output_dir: str,
    owner: str,
    repo: str,
    run: dict[str, Any],
    verbose: bool = False,
) -> list[dict[str, Any]] | None:
    day = _workflow_run_cache_day(run)
    run_id = run.get("id")
    if day is None or not isinstance(run_id, int):
        return None
    for path in _workflow_runs_cache_day_candidates(output_dir, owner, repo, day):
        rows = _load_workflow_runs_day_rows(path, owner, repo, day, verbose)
        if rows is None:
            continue
        for cached_run in rows:
            if cached_run.get("id") != run_id:
                continue
            jobs = cached_run.get("jobs")
            if isinstance(jobs, list) and all(isinstance(job, dict) for job in jobs):
                return jobs
    return None


def _save_cached_run_jobs_to_day(
    output_dir: str,
    owner: str,
    repo: str,
    run: dict[str, Any],
    jobs: list[dict[str, Any]],
    verbose: bool = False,
    status: str | None = None,
) -> None:
    day = _workflow_run_cache_day(run)
    run_id = run.get("id")
    if day is None or not isinstance(run_id, int):
        return
    path = _workflow_runs_cache_path(output_dir, owner, repo, status, day)
    meta = _workflow_runs_day_meta(owner, repo, status, day)
    day_rows = _load_rows_cache(path, meta, False) or []
    updated = False
    for index, cached_run in enumerate(day_rows):
        if cached_run.get("id") != run_id:
            continue
        day_rows[index] = {**cached_run, "jobs": jobs}
        updated = True
        break
    if not updated:
        day_rows.append({**run, "jobs": jobs})
    _save_rows_cache(path, meta, day_rows, verbose)


def _workflow_run_name(run: dict[str, Any]) -> str:
    return str(run.get("name", "")).strip() or str(run.get("display_title", "")).strip()


def _build_duration_row_from_run(
    run: dict[str, Any], since: datetime, verbose: bool = False
) -> dict[str, Any] | None:
    if str(run.get("conclusion", "")).strip().lower() != "success":
        return None
    created = _parse_optional_github_datetime(run.get("created_at"))
    started = _parse_optional_github_datetime(run.get("run_started_at")) or created
    completed = _parse_optional_github_datetime(
        run.get("updated_at")
    ) or _parse_optional_github_datetime(run.get("completed_at"))
    if created is None or started is None or completed is None:
        return None
    if completed < started:
        if verbose:
            print(
                "workflow-jobs: warning: skipping workflow run with updated_at earlier than "
                f"run_started_at (run_id={run.get('id')!r}, name={run.get('name', '')!r}).",
                file=sys.stderr,
            )
        return None
    if completed < since:
        return None
    run_id = run.get("id")
    if not isinstance(run_id, int):
        return None
    pull_requests = run.get("pull_requests")
    pr = "-"
    if isinstance(pull_requests, list) and pull_requests:
        first_pr = pull_requests[0]
        if isinstance(first_pr, dict):
            number = first_pr.get("number")
            if number is not None:
                pr = str(number)
    row = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "name": _workflow_run_name(run),
        "pr": pr,
        "duration": int((completed - started).total_seconds()),
    }
    url = str(run.get("html_url", "")).strip()
    if url:
        row["url"] = url
    return row


def _build_waiting_row_from_run(
    run: dict[str, Any], since: datetime, verbose: bool = False
) -> dict[str, Any] | None:
    created = _parse_optional_github_datetime(run.get("created_at"))
    started = _parse_optional_github_datetime(run.get("run_started_at"))
    if created is None or started is None:
        return None
    if started < created:
        if verbose:
            print(
                "workflow-jobs: warning: skipping workflow run with run_started_at earlier than "
                f"created_at (run_id={run.get('id')!r}, name={run.get('name', '')!r}).",
                file=sys.stderr,
            )
        return None
    if started < since:
        return None
    run_id = run.get("id")
    if not isinstance(run_id, int):
        return None
    pull_requests = run.get("pull_requests")
    pr = "-"
    if isinstance(pull_requests, list) and pull_requests:
        first_pr = pull_requests[0]
        if isinstance(first_pr, dict):
            number = first_pr.get("number")
            if number is not None:
                pr = str(number)
    row = {
        "run_id": run_id,
        "created_at": created.isoformat(),
        "started_at": started.isoformat(),
        "name": _workflow_run_name(run),
        "pr": pr,
        "waiting_seconds": int((started - created).total_seconds()),
    }
    url = str(run.get("html_url", "")).strip()
    if url:
        row["url"] = url
    return row


def _build_fail_rate_row_from_run(
    run: dict[str, Any], since: datetime
) -> tuple[str, str, str] | None:
    status = str(run.get("conclusion", "")).strip().lower()
    if status not in _FAIL_RATE_STATUSES:
        return None
    completed = _parse_optional_github_datetime(
        run.get("updated_at")
    ) or _parse_optional_github_datetime(run.get("completed_at"))
    if completed is None or completed < since:
        return None
    return completed.date().isoformat(), _workflow_run_name(run), status


def _build_queued_rows(runs: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for run in runs:
        if str(run.get("status", "")).strip().lower() != "queued":
            continue
        rows.append(
            {
                "name": str(run.get("name", "")).strip(),
                "workflow": str(run.get("display_title", "")).strip(),
                "created_at": str(run.get("created_at", "")).strip(),
                "url": str(run.get("html_url", "")).strip(),
            }
        )
    return sorted(rows, key=lambda r: (r["name"].lower(), r["created_at"], r["url"]))


def _build_running_rows(
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if now is None:
        now = datetime.now(timezone.utc)
    if runs:
        _print_verbose_step(verbose, f"collecting running jobs from {len(runs)} run(s)...")
    rows: list[dict[str, Any]] = []
    for index, run in enumerate(runs, 1):
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        _print_verbose_step(
            verbose, f"fetching jobs for run {index}/{len(runs)} (run_id={run_id})..."
        )
        workflow = str(run.get("display_title", "")).strip()
        for job in _fetch_run_jobs(
            owner, repo, run_id, token=token, api_url=api_url, verbose=verbose
        ):
            if str(job.get("status", "")).strip().lower() != "in_progress":
                continue
            started_at = str(job.get("started_at", "")).strip()
            if not started_at:
                continue
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            rows.append(
                {
                    "name": str(job.get("name", "")).strip(),
                    "workflow": workflow,
                    "started_at": started.isoformat(),
                    "duration_seconds": max(0, int((now - started).total_seconds())),
                    "url": str(job.get("html_url", "")).strip(),
                }
            )
        if verbose:
            _print_progress(index, len(runs))
    return sorted(rows, key=lambda r: (str(r["name"]).lower(), str(r["started_at"])))


def _build_fixed_width_table(rows: list[dict[str, str]], headers: list[str]) -> str:
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(row.get(header, "")))

    def _format(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[header]) for cell, header in zip(cells, headers))

    lines = [_format(headers), _format(["-" * widths[header] for header in headers])]
    for row in rows:
        lines.append(_format([row.get(header, "") for header in headers]))
    return "\n".join(lines)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", name.strip())
    return cleaned.strip("._") or "job"


def _write_csv(path: pathlib.Path, rows: list[dict[str, Any]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_xlsx(
    path: pathlib.Path,
    rows: list[dict[str, Any]],
    headers: list[str],
    sheet_name: str,
) -> None:
    with pandas.ExcelWriter(path, engine="openpyxl") as writer:
        pandas.DataFrame(_xlsx_sanitize_rows(rows, headers), columns=headers).to_excel(
            writer, index=False, sheet_name=sheet_name
        )


def _write_tabular_dump(
    rows: list[dict[str, Any]],
    headers: list[str],
    output_dir: str,
    prefix: str,
    dump: str | None,
    sheet_name: str,
) -> pathlib.Path | None:
    if dump is None:
        return None
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if dump == "xlsx":
        path = out / f"{prefix}.xlsx"
        _write_xlsx(path, rows, headers, sheet_name)
        return path
    path = out / f"{prefix}.csv"
    _write_csv(path, rows, headers)
    return path


def _duration_seconds_value(row: dict[str, Any]) -> float | None:
    raw_duration = row.get("duration_seconds")
    if raw_duration is None:
        raw_duration = row.get("duration")
    if raw_duration is None:
        return None
    return float(raw_duration)


def _waiting_seconds_value(row: dict[str, Any]) -> float | None:
    raw_duration = row.get("waiting_seconds")
    if raw_duration is None:
        return None
    return float(raw_duration)


def _split_time_outliers(
    rows: list[dict[str, Any]],
    value_getter: Any,
    *,
    ignore_non_positive_for_median: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plotted: list[dict[str, Any]] = []
    outliers: list[dict[str, Any]] = []
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(str(row.get("name", "")), []).append(row)
    for series in by_name.values():
        durations = [duration for row in series if (duration := value_getter(row)) is not None]
        if ignore_non_positive_for_median:
            durations = [duration for duration in durations if duration > 0]
        if not durations:
            plotted.extend(series)
            continue
        series_median = median(durations)
        if series_median <= 0:
            plotted.extend(series)
            continue
        cutoff = series_median * _DURATION_OUTLIER_MULTIPLIER
        for row in series:
            duration = value_getter(row)
            if duration is not None and duration >= cutoff:
                outliers.append(row)
            else:
                plotted.append(row)
    return plotted, outliers


def _split_duration_outliers(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _split_time_outliers(rows, _duration_seconds_value)


def _split_waiting_outliers(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _split_time_outliers(
        rows,
        _waiting_seconds_value,
        ignore_non_positive_for_median=True,
    )


def _build_average_per_hour_graph_inputs(
    rows: list[dict[str, Any]],
    value_getter: Any,
    *,
    time_keys: tuple[str, ...],
) -> dict[str, tuple[dict[str, float], dict[str, str]]]:
    grouped: dict[str, dict[int, list[float]]] = {"weekday": {}, "weekend": {}}
    for row in rows:
        timestamp = None
        for key in time_keys:
            timestamp = _parse_optional_github_datetime(row.get(key))
            if timestamp is not None:
                break
        value = value_getter(row)
        if timestamp is None or value is None:
            continue
        day_kind = "weekend" if timestamp.weekday() >= 5 else "weekday"
        grouped.setdefault(day_kind, {}).setdefault(timestamp.hour, []).append(
            float(value) / 60.0
        )

    graphs: dict[str, tuple[dict[str, float], dict[str, str]]] = {}
    for day_kind in ("weekday", "weekend"):
        values: dict[str, float] = {}
        labels: dict[str, str] = {}
        by_hour = grouped[day_kind]
        for hour in range(24):
            key = f"{hour:02d}h"
            samples = by_hour.get(hour, [])
            values[key] = round(sum(samples) / len(samples), 2) if samples else 0.0
            labels[key] = f"n={len(samples)}"
        graphs[day_kind] = (values, labels)
    return graphs


def _save_average_per_hour_graphs(
    graph_dir: pathlib.Path,
    prefix: str,
    title_prefix: str,
    rows: list[dict[str, Any]],
    value_getter: Any,
    *,
    time_keys: tuple[str, ...],
    y_axis_label: str,
) -> list[tuple[str, pathlib.Path]]:
    graphs: list[tuple[str, pathlib.Path]] = []
    graph_inputs = _build_average_per_hour_graph_inputs(rows, value_getter, time_keys=time_keys)
    for day_kind, day_label in (("weekday", "Weekdays"), ("weekend", "Weekends")):
        svg = graph_dir / f"{prefix}_{day_kind}_hourly_average.svg"
        values, labels = graph_inputs[day_kind]
        title = f"{title_prefix} by hour ({day_label})"
        save_bar_graph(
            svg,
            values,
            title,
            x_axis_label="Hour of day (UTC)",
            y_axis_label=y_axis_label,
            bar_labels=labels,
        )
        graphs.append((title, svg))
    return graphs


def _build_duration_rows(
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    since: datetime,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
    cache_dir: str | None = None,
) -> list[dict[str, Any]]:
    if runs:
        _print_verbose_step(verbose, f"collecting duration history from {len(runs)} run(s)...")
    rows: list[dict[str, Any]] = []
    for index, run in enumerate(runs, 1):
        run_row = _build_duration_row_from_run(run, since, verbose)
        if run_row is not None:
            rows.append(run_row)
        if verbose:
            _print_progress(index, len(runs))
    return sorted(
        rows, key=lambda r: (str(r.get("created_at", "")), str(r.get("name", "")).lower())
    )


def _build_waiting_rows(
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    since: datetime,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
    cache_dir: str | None = None,
) -> list[dict[str, Any]]:
    if runs:
        _print_verbose_step(verbose, f"collecting waiting history from {len(runs)} run(s)...")
    rows: list[dict[str, Any]] = []
    for index, run in enumerate(runs, 1):
        run_row = _build_waiting_row_from_run(run, since, verbose)
        if run_row is not None:
            rows.append(run_row)
        if verbose:
            _print_progress(index, len(runs))
    return sorted(
        rows, key=lambda r: (str(r.get("created_at", "")), str(r.get("name", "")).lower())
    )


def _build_fail_rate_rows(
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    since: datetime,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
    cache_dir: str | None = None,
) -> list[dict[str, int | str]]:
    if runs:
        _print_verbose_step(verbose, f"collecting fail-rate history from {len(runs)} run(s)...")
    entries: list[tuple[str, str, str]] = []
    for index, run in enumerate(runs, 1):
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        run_row = _build_fail_rate_row_from_run(run, since)
        if run_row is not None:
            entries.append(run_row)
            if verbose:
                _print_progress(index, len(runs))
            continue
        _print_verbose_step(
            verbose, f"fetching jobs for run {index}/{len(runs)} (run_id={run_id})..."
        )
        jobs = (
            _load_cached_run_jobs_from_day(cache_dir, owner, repo, run, verbose)
            if cache_dir is not None
            else None
        )
        if jobs is None:
            jobs = _fetch_run_jobs(
                owner,
                repo,
                run_id,
                token=token,
                api_url=api_url,
                verbose=verbose,
            )
            if cache_dir is not None:
                _save_cached_run_jobs_to_day(
                    cache_dir, owner, repo, run, jobs, verbose, status="completed"
                )
        run["jobs"] = jobs
        for job in jobs:
            status = str(job.get("conclusion", "")).strip().lower()
            completed = _parse_optional_github_datetime(job.get("completed_at"))
            if completed is None or completed < since or status not in _FAIL_RATE_STATUSES:
                continue
            job_name = str(job.get("name", "")).strip() or _workflow_run_name(run)
            entries.append((completed.date().isoformat(), job_name, status))
        if verbose:
            _print_progress(index, len(runs))
    by_day: dict[str, dict[str, int]] = {}
    for day, _, status in entries:
        stats = by_day.setdefault(day, {k: 0 for k in _FAIL_RATE_STATUSES})
        stats[status] += 1
    return [{"date": day, **by_day[day]} for day in sorted(by_day)]


def _build_fail_rate_job_rows(
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    since: datetime,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
    cache_dir: str | None = None,
) -> list[dict[str, int | float | str]]:
    by_job_day: dict[tuple[str, str], dict[str, int | float | str]] = {}
    for run in runs:
        run_row = _build_fail_rate_row_from_run(run, since)
        if run_row is not None:
            day, job_name, status = run_row
            stats = by_job_day.setdefault(
                (day, job_name),
                {
                    "date": day,
                    "name": job_name,
                    "failure": 0,
                    "cancelled": 0,
                    "total": 0,
                    "fail_cancel_rate": 0.0,
                },
            )
            stats["total"] = int(stats["total"]) + 1
            if status in ("failure", "cancelled"):
                stats[status] = int(stats[status]) + 1
            continue
        jobs = run.get("jobs")
        if not isinstance(jobs, list):
            run_id = run.get("id")
            if not isinstance(run_id, int):
                continue
            jobs = (
                _load_cached_run_jobs_from_day(cache_dir, owner, repo, run, verbose)
                if cache_dir is not None
                else None
            )
            if jobs is None:
                jobs = _fetch_run_jobs(
                    owner,
                    repo,
                    run_id,
                    token=token,
                    api_url=api_url,
                    verbose=verbose,
                )
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            status = str(job.get("conclusion", "")).strip().lower()
            completed = _parse_optional_github_datetime(job.get("completed_at"))
            if completed is None or completed < since or status not in _FAIL_RATE_STATUSES:
                continue
            job_name = str(job.get("name", "")).strip() or _workflow_run_name(run)
            day = completed.date().isoformat()
            stats = by_job_day.setdefault(
                (day, job_name),
                {
                    "date": day,
                    "name": job_name,
                    "failure": 0,
                    "cancelled": 0,
                    "total": 0,
                    "fail_cancel_rate": 0.0,
                },
            )
            stats["total"] = int(stats["total"]) + 1
            if status in ("failure", "cancelled"):
                stats[status] = int(stats[status]) + 1
    job_rows = []
    for day, job_name in sorted(by_job_day, key=lambda key: (key[0], key[1].lower(), key[1])):
        stats = by_job_day[(day, job_name)]
        total = int(stats["total"])
        failures = int(stats["failure"])
        cancelled = int(stats["cancelled"])
        stats["fail_cancel_rate"] = (
            round((failures + cancelled) * 100.0 / total, 2) if total else 0.0
        )
        job_rows.append(stats)
    return job_rows


def _write_duration_outputs(
    rows: list[dict[str, Any]],
    owner: str,
    repo: str,
    output_dir: str,
    dump: str | None = None,
    verbose: bool = False,
) -> list[pathlib.Path]:
    out = pathlib.Path(output_dir)
    graph_dir = out / f"graphs_{repo}"
    out.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"workflow_jobs_duration_{repo}.csv"
    outlier_csv_path = out / f"workflow_jobs_duration_outliers_{repo}.csv"
    headers = ["run_id", "created_at", "name", "pr", "duration", "url"]
    outlier_headers = headers
    _print_verbose_step(verbose, f"writing {csv_path}...")
    _write_csv(csv_path, rows, headers)
    paths = [csv_path]
    plotted_rows, outlier_rows = _split_duration_outliers(rows)
    if outlier_rows:
        _print_verbose_step(verbose, f"writing {outlier_csv_path}...")
        _write_csv(outlier_csv_path, outlier_rows, outlier_headers)
        paths.append(outlier_csv_path)
    if dump == "xlsx":
        xlsx_path = out / f"workflow_jobs_duration_{repo}.xlsx"
        _print_verbose_step(verbose, f"writing {xlsx_path}...")
        _write_xlsx(xlsx_path, rows, headers, "Durations")
        paths.append(xlsx_path)
        if outlier_rows:
            outlier_xlsx_path = out / f"workflow_jobs_duration_outliers_{repo}.xlsx"
            _print_verbose_step(verbose, f"writing {outlier_xlsx_path}...")
            _write_xlsx(outlier_xlsx_path, outlier_rows, outlier_headers, "Outliers")
            paths.append(outlier_xlsx_path)
    job_series: dict[str, list[dict[str, Any]]] = {}
    for row in plotted_rows:
        job_series.setdefault(str(row["name"]), []).append(row)
    graphs: list[tuple[str, pathlib.Path]] = []
    graph_names = sorted(job_series)
    hourly_graphs = _save_average_per_hour_graphs(
        graph_dir,
        "workflow_jobs_duration",
        "Avg workflow duration",
        plotted_rows,
        _duration_seconds_value,
        time_keys=("created_at",),
        y_axis_label="Average duration (minutes)",
    )
    graph_count = len(graph_names) + len(hourly_graphs)
    if graph_count:
        _print_verbose_step(verbose, f"generating {graph_count} graph(s)...")
    for index, job_name in enumerate(graph_names, 1):
        svg = graph_dir / f"workflow_jobs_duration_{_safe_name(job_name)}.svg"
        save_job_duration_line_graph(svg, job_series[job_name], f"Workflow duration: {job_name}")
        graphs.append((f"Workflow duration: {job_name}", svg))
        if verbose:
            _print_progress(index, graph_count)
    for index, hourly_graph in enumerate(hourly_graphs, len(graph_names) + 1):
        graphs.append(hourly_graph)
        if verbose:
            _print_progress(index, graph_count)
    html_path = graph_dir / f"workflow_jobs_duration_{repo}.html"
    _print_verbose_step(verbose, f"writing {html_path}...")
    save_graphs_html_report(html_path, f"{owner}/{repo}", graphs)
    return [*paths, *(path for _, path in graphs), html_path]


def _write_waiting_outputs(
    rows: list[dict[str, Any]],
    owner: str,
    repo: str,
    output_dir: str,
    dump: str | None = None,
    verbose: bool = False,
) -> list[pathlib.Path]:
    out = pathlib.Path(output_dir)
    graph_dir = out / f"graphs_{repo}"
    out.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"workflow_jobs_waiting_{repo}.csv"
    outlier_csv_path = out / f"workflow_jobs_waiting_outliers_{repo}.csv"
    headers = ["run_id", "created_at", "started_at", "name", "pr", "waiting_seconds"]
    outlier_headers = [*headers, "url"]
    _print_verbose_step(verbose, f"writing {csv_path}...")
    _write_csv(csv_path, rows, headers)
    paths = [csv_path]
    plotted_rows, outlier_rows = _split_waiting_outliers(rows)
    if outlier_rows:
        _print_verbose_step(verbose, f"writing {outlier_csv_path}...")
        _write_csv(outlier_csv_path, outlier_rows, outlier_headers)
        paths.append(outlier_csv_path)
    if dump == "xlsx":
        xlsx_path = out / f"workflow_jobs_waiting_{repo}.xlsx"
        _print_verbose_step(verbose, f"writing {xlsx_path}...")
        _write_xlsx(xlsx_path, rows, headers, "Waiting")
        paths.append(xlsx_path)
        if outlier_rows:
            outlier_xlsx_path = out / f"workflow_jobs_waiting_outliers_{repo}.xlsx"
            _print_verbose_step(verbose, f"writing {outlier_xlsx_path}...")
            _write_xlsx(outlier_xlsx_path, outlier_rows, outlier_headers, "Outliers")
            paths.append(outlier_xlsx_path)
    job_series: dict[str, list[dict[str, Any]]] = {}
    for row in plotted_rows:
        job_series.setdefault(str(row["name"]), []).append(row)
    graphs: list[tuple[str, pathlib.Path]] = []
    graph_names = sorted(job_series)
    hourly_graphs = _save_average_per_hour_graphs(
        graph_dir,
        "workflow_jobs_waiting",
        "Avg workflow waiting time",
        plotted_rows,
        _waiting_seconds_value,
        time_keys=("created_at", "started_at"),
        y_axis_label="Average waiting time (minutes)",
    )
    graph_count = len(graph_names) + len(hourly_graphs)
    if graph_count:
        _print_verbose_step(verbose, f"generating {graph_count} graph(s)...")
    for index, job_name in enumerate(graph_names, 1):
        svg = graph_dir / f"workflow_jobs_waiting_{_safe_name(job_name)}.svg"
        save_job_duration_line_graph(
            svg,
            job_series[job_name],
            f"Workflow waiting time: {job_name}",
            x_axis_label="Creation date",
            y_axis_label="Waiting time (minutes)",
            seconds_keys=("waiting_seconds",),
        )
        graphs.append((f"Workflow waiting time: {job_name}", svg))
        if verbose:
            _print_progress(index, graph_count)
    for index, hourly_graph in enumerate(hourly_graphs, len(graph_names) + 1):
        graphs.append(hourly_graph)
        if verbose:
            _print_progress(index, graph_count)
    html_path = graph_dir / f"workflow_jobs_waiting_{repo}.html"
    _print_verbose_step(verbose, f"writing {html_path}...")
    save_graphs_html_report(html_path, f"{owner}/{repo}", graphs)
    return [*paths, *(path for _, path in graphs), html_path]


def _write_fail_rate_outputs(
    rows: list[dict[str, int | str]],
    job_rows: list[dict[str, int | float | str]],
    owner: str,
    output_dir: str,
    repo: str,
    dump: str | None = None,
    verbose: bool = False,
) -> list[pathlib.Path]:
    out = pathlib.Path(output_dir)
    graph_dir = out / f"graphs_{repo}"
    out.mkdir(parents=True, exist_ok=True)
    graph_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"workflow_jobs_fail_rate_{repo}.csv"
    job_csv_path = out / f"workflow_jobs_fail_rate_by_job_{repo}.csv"
    headers = ["date", *_FAIL_RATE_STATUSES]
    job_headers = ["date", "name", "failure", "cancelled", "total", "fail_cancel_rate"]
    _print_verbose_step(verbose, f"writing {csv_path}...")
    _write_csv(csv_path, rows, headers)
    _print_verbose_step(verbose, f"writing {job_csv_path}...")
    _write_csv(job_csv_path, job_rows, job_headers)
    paths = [csv_path, job_csv_path]
    if dump == "xlsx":
        xlsx_path = out / f"workflow_jobs_fail_rate_{repo}.xlsx"
        job_xlsx_path = out / f"workflow_jobs_fail_rate_by_job_{repo}.xlsx"
        _print_verbose_step(verbose, f"writing {xlsx_path}...")
        _write_xlsx(xlsx_path, rows, headers, "Fail rate")
        _print_verbose_step(verbose, f"writing {job_xlsx_path}...")
        _write_xlsx(job_xlsx_path, job_rows, job_headers, "Per job")
        paths.extend([xlsx_path, job_xlsx_path])
    graphs: list[tuple[str, pathlib.Path]] = []
    by_job: dict[str, list[dict[str, Any]]] = {}
    for row in job_rows:
        by_job.setdefault(str(row.get("name", "")), []).append(row)
    graph_names = sorted(
        job_name
        for job_name, series in by_job.items()
        if any(
            int(row.get("failure", 0)) > 0 or int(row.get("cancelled", 0)) > 0 for row in series
        )
    )
    if graph_names:
        _print_verbose_step(verbose, f"generating {len(graph_names)} graph(s)...")
    for index, job_name in enumerate(graph_names, 1):
        svg = graph_dir / f"workflow_jobs_fail_rate_{_safe_name(job_name)}.svg"
        series = by_job[job_name]
        values = {str(row["date"]): float(row["fail_cancel_rate"]) for row in series}
        labels = {
            str(row["date"]): (
                f"f={int(row['failure'])}, c={int(row['cancelled'])}, n={int(row['total'])}"
            )
            for row in series
        }
        save_bar_graph(
            svg,
            values,
            f"Workflow fail/cancel rate: {job_name}",
            x_axis_label="Date",
            y_axis_label="Fail/cancel rate (%)",
            bar_labels=labels,
        )
        graphs.append((f"Workflow fail/cancel rate: {job_name}", svg))
        if verbose:
            _print_progress(index, len(graph_names))
    html_path = graph_dir / f"workflow_jobs_fail_rate_{repo}.html"
    _print_verbose_step(verbose, f"writing {html_path}...")
    save_graphs_html_report(html_path, f"{owner}/{repo}", graphs)
    return [*paths, *(path for _, path in graphs), html_path]


def _build_parser(token_default: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workflow-jobs",
        description=(
            "Collect queued jobs, running jobs, successful workflow-run durations, "
            "successful workflow-run waiting times, or fail-rate history."
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
        help="Since date, relative offset, or integer day count (default: 60 days ago).",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output folder.")
    parser.add_argument(
        "--dump",
        choices=["csv", "xlsx"],
        default=None,
        help="Also dump the selected tabular report to CSV or XLSX in --output-dir.",
    )
    parser.add_argument(
        "--gh",
        action="store_true",
        default=False,
        help="Fetch the token from `gh auth token`. Cannot be combined with --token.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--queued", action="store_true", help="List queued jobs sorted by name.")
    group.add_argument(
        "--running",
        action="store_true",
        help="List running jobs with their current duration.",
    )
    group.add_argument(
        "--duration",
        action="store_true",
        help="Collect historical durations of successful jobs and generate graphs.",
    )
    group.add_argument(
        "--waiting",
        action="store_true",
        help="Collect historical waiting times before successful jobs start and generate graphs.",
    )
    group.add_argument(
        "--fail-rate",
        action="store_true",
        help="Collect historical counts for failure/cancelled/skipped/success conclusions.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", default=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    cache = _load_token_cache()
    owner, repo = _extract_owner_repo(argv)
    token_default = os.environ.get("GITHUB_TOKEN") or _resolve_cached_token(cache, owner, repo)
    parser = _build_parser(token_default=token_default)
    args = parser.parse_args(argv)
    if args.gh and "--token" in argv:
        parser.error("--gh and --token are mutually exclusive.")
    if args.gh:
        if os.environ.get("GITHUB_TOKEN"):
            raise RuntimeError(
                "--gh cannot be used when GITHUB_TOKEN is set; unset GITHUB_TOKEN "
                "and run `gh auth login` first."
            )
        args.token = _fetch_token_from_gh_cli()
    if args.verbose:
        token_origin, token_type = _resolve_token_origin(
            argv, args.token, os.environ.get("GITHUB_TOKEN"), cache, args.owner, args.repo
        )
        print(f"workflow-jobs: token source={token_origin}, type={token_type}.", file=sys.stderr)
    since = (
        _parse_since_datetime(args.since)
        if args.duration or args.waiting or args.fail_rate
        else None
    )
    status = (
        "queued"
        if args.queued
        else (
            "in_progress"
            if args.running
            else "completed" if args.duration or args.waiting or args.fail_rate else None
        )
    )
    runs = _fetch_workflow_runs(
        args.owner,
        args.repo,
        token=args.token,
        api_url=args.api_url,
        status=status,
        verbose=args.verbose,
        stop_before=since,
        cache_dir=args.output_dir if args.duration or args.waiting or args.fail_rate else None,
    )
    if args.queued:
        rows = _build_queued_rows(runs)
        headers = ["name", "workflow", "created_at", "url"]
        print(_build_fixed_width_table(rows, headers))
        path = _write_tabular_dump(
            rows,
            headers,
            args.output_dir,
            f"workflow_jobs_queued_{args.repo}",
            args.dump,
            "Queued jobs",
        )
        if path is not None:
            print(path)
        return 0
    if args.running:
        rows = _build_running_rows(
            args.owner,
            args.repo,
            runs,
            token=args.token,
            api_url=args.api_url,
            verbose=args.verbose,
        )
        headers = ["name", "workflow", "started_at", "duration_seconds", "url"]
        print(
            _build_fixed_width_table(
                [{k: str(v) for k, v in row.items()} for row in rows], headers
            )
        )
        path = _write_tabular_dump(
            rows,
            headers,
            args.output_dir,
            f"workflow_jobs_running_{args.repo}",
            args.dump,
            "Running jobs",
        )
        if path is not None:
            print(path)
        return 0
    if args.duration:
        assert since is not None, "`since` must be set when --duration is used."
        paths = _write_duration_outputs(
            _build_duration_rows(
                args.owner,
                args.repo,
                runs,
                since,
                token=args.token,
                api_url=args.api_url,
                verbose=args.verbose,
                cache_dir=args.output_dir,
            ),
            args.owner,
            args.repo,
            args.output_dir,
            dump=args.dump,
            verbose=args.verbose,
        )
        for path in paths:
            print(path)
        return 0
    if args.waiting:
        assert since is not None, "`since` must be set when --waiting is used."
        paths = _write_waiting_outputs(
            _build_waiting_rows(
                args.owner,
                args.repo,
                runs,
                since,
                token=args.token,
                api_url=args.api_url,
                verbose=args.verbose,
                cache_dir=args.output_dir,
            ),
            args.owner,
            args.repo,
            args.output_dir,
            dump=args.dump,
            verbose=args.verbose,
        )
        for path in paths:
            print(path)
        return 0
    assert since is not None, "`since` must be set when --fail-rate is used."
    fail_rows = _build_fail_rate_rows(
        args.owner,
        args.repo,
        runs,
        since,
        token=args.token,
        api_url=args.api_url,
        verbose=args.verbose,
        cache_dir=args.output_dir,
    )
    headers = ["date", *_FAIL_RATE_STATUSES]
    print(
        _build_fixed_width_table(
            [{k: str(v) for k, v in row.items()} for row in fail_rows],
            headers,
        )
    )
    fail_job_rows = _build_fail_rate_job_rows(
        args.owner,
        args.repo,
        runs,
        since,
        token=args.token,
        api_url=args.api_url,
        verbose=args.verbose,
        cache_dir=args.output_dir,
    )
    for path in _write_fail_rate_outputs(
        fail_rows,
        fail_job_rows,
        args.owner,
        args.output_dir,
        args.repo,
        dump=args.dump,
        verbose=args.verbose,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
