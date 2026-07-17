"""Workflow jobs command line with queued, duration, and fail-rate reports."""

from __future__ import annotations

import argparse
import csv
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import parse
from urllib.error import HTTPError

import pandas

from .pr_stats import _print_progress, _xlsx_sanitize_rows
from .pr_stats_graphs import save_graphs_html_report, save_job_duration_line_graph
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


def _default_since() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")


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
    relative_dt = parse_relative_since(since_value)
    if relative_dt is not None:
        return relative_dt
    parsed = datetime.fromisoformat(since_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fetch_workflow_runs(
    owner: str,
    repo: str,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    status: str | None = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        query: dict[str, Any] = {"per_page": 100, "page": page}
        if status:
            query["status"] = status
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
        rows.extend(run for run in runs if isinstance(run, dict))
        if len(runs) < 100:
            break
        page += 1
    return rows


def _fetch_run_jobs(
    owner: str,
    repo: str,
    run_id: int,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
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
        rows.extend(job for job in jobs if isinstance(job, dict))
        if len(jobs) < 100:
            break
        page += 1
    return rows


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


def _build_duration_rows(
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    since: datetime,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
) -> list[dict[str, Any]]:
    if runs:
        _print_verbose_step(verbose, f"collecting duration history from {len(runs)} run(s)...")
    rows: list[dict[str, Any]] = []
    for index, run in enumerate(runs, 1):
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        _print_verbose_step(
            verbose, f"fetching jobs for run {index}/{len(runs)} (run_id={run_id})..."
        )
        for job in _fetch_run_jobs(
            owner, repo, run_id, token=token, api_url=api_url, verbose=verbose
        ):
            if str(job.get("conclusion", "")).strip().lower() != "success":
                continue
            started_at = str(job.get("started_at", "")).strip()
            completed_at = str(job.get("completed_at", "")).strip()
            if not started_at or not completed_at:
                continue
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            if completed < started:
                if verbose:
                    print(
                        "workflow-jobs: warning: skipping job with completed_at earlier than "
                        f"started_at (run_id={run_id}, job={job.get('name', '')!r}).",
                        file=sys.stderr,
                    )
                continue
            if completed < since:
                continue
            rows.append(
                {
                    "job_name": str(job.get("name", "")).strip(),
                    "completed_at": completed.isoformat(),
                    "duration_seconds": int((completed - started).total_seconds()),
                }
            )
        if verbose:
            _print_progress(index, len(runs))
    return sorted(rows, key=lambda r: (str(r["job_name"]).lower(), str(r["completed_at"])))


def _build_fail_rate_rows(
    owner: str,
    repo: str,
    runs: list[dict[str, Any]],
    since: datetime,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    verbose: bool = False,
) -> list[dict[str, int | str]]:
    if runs:
        _print_verbose_step(verbose, f"collecting fail-rate history from {len(runs)} run(s)...")
    by_day: dict[str, dict[str, int]] = {}
    for index, run in enumerate(runs, 1):
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        _print_verbose_step(
            verbose, f"fetching jobs for run {index}/{len(runs)} (run_id={run_id})..."
        )
        for job in _fetch_run_jobs(
            owner, repo, run_id, token=token, api_url=api_url, verbose=verbose
        ):
            status = str(job.get("conclusion", "")).strip().lower()
            completed_at = str(job.get("completed_at", "")).strip()
            if status not in _FAIL_RATE_STATUSES or not completed_at:
                continue
            completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            if completed < since:
                continue
            day = completed.date().isoformat()
            stats = by_day.setdefault(day, {k: 0 for k in _FAIL_RATE_STATUSES})
            stats[status] += 1
        if verbose:
            _print_progress(index, len(runs))
    return [{"date": day, **by_day[day]} for day in sorted(by_day)]


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
    headers = ["job_name", "completed_at", "duration_seconds"]
    _print_verbose_step(verbose, f"writing {csv_path}...")
    _write_csv(csv_path, rows, headers)
    paths = [csv_path]
    if dump == "xlsx":
        xlsx_path = out / f"workflow_jobs_duration_{repo}.xlsx"
        _print_verbose_step(verbose, f"writing {xlsx_path}...")
        _write_xlsx(xlsx_path, rows, headers, "Durations")
        paths.append(xlsx_path)
    job_series: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        job_series.setdefault(str(row["job_name"]), []).append(row)
    graphs: list[tuple[str, pathlib.Path]] = []
    graph_names = sorted(job_series)
    if graph_names:
        _print_verbose_step(verbose, f"generating {len(graph_names)} graph(s)...")
    for index, job_name in enumerate(graph_names, 1):
        svg = graph_dir / f"workflow_jobs_duration_{_safe_name(job_name)}.svg"
        save_job_duration_line_graph(svg, job_series[job_name], f"Job duration: {job_name}")
        graphs.append((f"Job duration: {job_name}", svg))
        if verbose:
            _print_progress(index, len(graph_names))
    html_path = graph_dir / f"workflow_jobs_duration_{repo}.html"
    _print_verbose_step(verbose, f"writing {html_path}...")
    save_graphs_html_report(html_path, f"{owner}/{repo}", graphs)
    return [*paths, *(path for _, path in graphs), html_path]


def _write_fail_rate_outputs(
    rows: list[dict[str, int | str]],
    output_dir: str,
    repo: str,
    dump: str | None = None,
) -> list[pathlib.Path]:
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"workflow_jobs_fail_rate_{repo}.csv"
    headers = ["date", *_FAIL_RATE_STATUSES]
    _write_csv(csv_path, rows, headers)
    paths = [csv_path]
    if dump == "xlsx":
        xlsx_path = out / f"workflow_jobs_fail_rate_{repo}.xlsx"
        _write_xlsx(xlsx_path, rows, headers, "Fail rate")
        paths.append(xlsx_path)
    return paths


def _build_parser(token_default: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workflow-jobs",
        description=(
            "Collect queued jobs, running jobs, successful job durations, "
            "or fail-rate history."
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
    parser.add_argument("--since", default=None, help="Since date (default: 30 days ago).")
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
    runs = _fetch_workflow_runs(
        args.owner,
        args.repo,
        token=args.token,
        api_url=args.api_url,
        status="queued" if args.queued else "in_progress" if args.running else None,
        verbose=args.verbose,
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
    since = _parse_since_datetime(args.since)
    if args.duration:
        paths = _write_duration_outputs(
            _build_duration_rows(
                args.owner,
                args.repo,
                runs,
                since,
                token=args.token,
                api_url=args.api_url,
                verbose=args.verbose,
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
    fail_rows = _build_fail_rate_rows(
        args.owner,
        args.repo,
        runs,
        since,
        token=args.token,
        api_url=args.api_url,
        verbose=args.verbose,
    )
    headers = ["date", *_FAIL_RATE_STATUSES]
    print(
        _build_fixed_width_table(
            [{k: str(v) for k, v in row.items()} for row in fail_rows],
            headers,
        )
    )
    for path in _write_fail_rate_outputs(fail_rows, args.output_dir, args.repo, dump=args.dump):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
