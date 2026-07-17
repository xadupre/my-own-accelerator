import csv
import json
import os
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pandas

from moa.commands.workflow_jobs import (
    _WORKFLOW_RUNS_DAY_CACHE_VERSION,
    _build_average_per_hour_graph_inputs,
    _build_duration_rows,
    _build_fail_cost_rows,
    _build_fail_rate_job_rows,
    _build_fail_rate_rows,
    _build_queued_rows,
    _build_running_rows,
    _build_waiting_rows,
    _default_since,
    _fetch_run_jobs,
    _fetch_workflow_runs,
    _parse_since_datetime,
    _run_jobs_cache_path,
    _split_duration_outliers,
    _split_waiting_outliers,
    _workflow_jobs_cache_dir,
    _workflow_runs_cache_path,
    _workflow_runs_cache_path_day,
    _write_duration_outputs,
    _write_fail_cost_outputs,
    _write_fail_rate_outputs,
    _write_waiting_outputs,
    main,
)
from moa.ext_test_case import ExtTestCase


class TestWorkflowJobs(ExtTestCase):
    def test_default_since_uses_two_month_window(self) -> None:
        default_since = datetime.fromisoformat(_default_since()).date()
        delta = datetime.now(timezone.utc).date() - default_since
        self.assertGreaterEqual(delta.days, 59)
        self.assertLessEqual(delta.days, 60)

    def test_parse_since_integer_means_days_ago(self) -> None:
        before = datetime.now(timezone.utc)
        parsed = _parse_since_datetime("7")
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before - parsed, timedelta(days=7, seconds=1))
        self.assertGreaterEqual(after - parsed, timedelta(days=7))

    def test_fetch_workflow_runs_retries_without_token_on_403(self) -> None:
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=[
                    HTTPError("https://api.github.com", 403, "Forbidden", None, StringIO()),
                    {"workflow_runs": []},
                ],
            ) as mocked,
        ):
            rows = _fetch_workflow_runs("owner", "repo", token="tok", verbose=True)
        self.assertEqual(rows, [])
        self.assertEqual(mocked.call_args_list[0].args[1], "tok")
        self.assertIsNone(mocked.call_args_list[1].args[1])
        self.assertIn("retrying without token.", err.getvalue())

    def test_fetch_run_jobs_retries_without_token_on_403(self) -> None:
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=[
                    HTTPError("https://api.github.com", 403, "Forbidden", None, StringIO()),
                    {"jobs": []},
                ],
            ) as mocked,
        ):
            rows = _fetch_run_jobs("owner", "repo", 12, token="tok", verbose=True)
        self.assertEqual(rows, [])
        self.assertEqual(mocked.call_args_list[0].args[1], "tok")
        self.assertIsNone(mocked.call_args_list[1].args[1])
        self.assertIn("retrying without token for run_id=12.", err.getvalue())

    def test_fetch_workflow_runs_does_not_swallow_non_403(self) -> None:
        with (
            patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=HTTPError(
                    "https://api.github.com", 401, "Bad credentials", None, StringIO()
                ),
            ),
            self.assertRaises(HTTPError),
        ):
            _fetch_workflow_runs("owner", "repo", token="tok")

    def test_fetch_workflow_runs_verbose_reports_pages(self) -> None:
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=[
                    {
                        "workflow_runs": [
                            {"id": i, "created_at": f"2026-01-{(i % 9) + 1:02d}T00:00:00Z"}
                            for i in range(100)
                        ]
                    },
                    {"workflow_runs": [{"id": 101, "created_at": "2026-01-10T00:00:00Z"}]},
                ],
            ),
        ):
            rows = _fetch_workflow_runs("owner", "repo", verbose=True)
        self.assertEqual(len(rows), 101)
        self.assertIn("fetching workflow runs page 1", err.getvalue())
        self.assertIn("fetched 100 workflow run(s) from page 1", err.getvalue())
        self.assertIn("min(date)=2026-01-01T00:00:00+00:00", err.getvalue())
        self.assertIn("max(date)=2026-01-09T00:00:00+00:00", err.getvalue())
        self.assertIn("fetching workflow runs page 2", err.getvalue())

    def test_fetch_workflow_runs_writes_and_reuses_output_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = _workflow_runs_cache_path(
                tmp, "owner", "repo", None, datetime(2026, 1, 1, tzinfo=timezone.utc)
            )
            with patch(
                "moa.commands.workflow_jobs._fetch_json",
                return_value={"workflow_runs": [{"id": 1, "created_at": "2026-01-03T00:00:00Z"}]},
            ) as mocked:
                rows = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    stop_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    cache_path=cache_path,
                )
            self.assertEqual(rows, [{"id": 1, "created_at": "2026-01-03T00:00:00Z"}])
            self.assertTrue(cache_path.exists())
            self.assertEqual(mocked.call_count, 1)
            with patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=RuntimeError("Cache should be used."),
            ):
                cached_rows = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    stop_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    cache_path=cache_path,
                )
            self.assertEqual(cached_rows, rows)

    def test_fetch_workflow_runs_updates_output_cache_during_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = _workflow_runs_cache_path(
                tmp, "owner", "repo", None, datetime(2026, 1, 1, tzinfo=timezone.utc)
            )

            def fake_fetch_json(url: str, token: str | None) -> dict[str, object]:
                self.assertIsNone(token)
                page = parse_qs(urlparse(url).query)["page"][0]
                if page == "1":
                    return {
                        "workflow_runs": [
                            {"id": i, "created_at": "2026-01-03T00:00:00Z"} for i in range(100)
                        ]
                    }
                self.assertTrue(cache_path.exists())
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                self.assertEqual(len(cached["rows"]), 100)
                return {"workflow_runs": [{"id": 101, "created_at": "2026-01-02T00:00:00Z"}]}

            with patch("moa.commands.workflow_jobs._fetch_json", side_effect=fake_fetch_json):
                rows = _fetch_workflow_runs("owner", "repo", cache_path=cache_path)
            self.assertEqual(len(rows), 101)

    def test_fetch_workflow_runs_writes_one_daily_cache_file_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 1, 3, tzinfo=timezone.utc)
            with patch(
                "moa.commands.workflow_jobs._fetch_json",
                return_value={
                    "workflow_runs": [
                        {"id": 1, "created_at": "2026-01-03T10:00:00Z"},
                        {"id": 2, "created_at": "2026-01-02T10:00:00Z"},
                    ]
                },
            ):
                rows = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    stop_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    cache_dir=tmp,
                    now=now,
                    api_url="https://api.github.com",
                )
            self.assertEqual(len(rows), 2)
            cache_files = sorted(
                _workflow_jobs_cache_dir(tmp, "owner", "repo").glob("runs_all_*.json")
            )
            self.assertEqual(
                [path.name for path in cache_files],
                [
                    "runs_all_20260101.json",
                    "runs_all_20260102.json",
                    "runs_all_20260103.json",
                ],
            )
            day_rows = []
            for path in cache_files:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["meta"]["kind"], "workflow_runs_day")
                day_rows.append(payload["rows"])
            self.assertEqual(day_rows[0], [])
            self.assertEqual(day_rows[1][0]["id"], 2)
            self.assertEqual(day_rows[2][0]["id"], 1)

    def test_workflow_runs_cache_path_day_validates_stamp(self) -> None:
        self.assertEqual(
            _workflow_runs_cache_path_day(pathlib.Path("runs_completed_20260103.json")),
            "2026-01-03",
        )
        with self.assertRaises(ValueError):
            _workflow_runs_cache_path_day(pathlib.Path("runs_completed_today.json"))
        with self.assertRaises(ValueError):
            _workflow_runs_cache_path_day(pathlib.Path("runs_completed_20261399.json"))

    def test_fetch_workflow_runs_daily_cache_reuses_jobs_saved_in_day_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            day_path = _workflow_runs_cache_path(
                tmp, "owner", "repo", "completed", datetime(2026, 1, 3, tzinfo=timezone.utc)
            )
            current_day_path = _workflow_runs_cache_path(
                tmp, "owner", "repo", "completed", datetime(2026, 1, 4, tzinfo=timezone.utc)
            )
            day_path.parent.mkdir(parents=True, exist_ok=True)
            day_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "kind": "workflow_runs_day",
                            "version": _WORKFLOW_RUNS_DAY_CACHE_VERSION,
                            "owner": "owner",
                            "repo": "repo",
                            "status": "completed",
                            "day": "2026-01-03",
                        },
                        "rows": [
                            {
                                "id": 12,
                                "name": "build",
                                "conclusion": "success",
                                "created_at": "2026-01-03T10:00:00Z",
                                "run_started_at": "2026-01-03T10:00:00Z",
                                "updated_at": "2026-01-03T10:02:00Z",
                                "jobs": [
                                    {
                                        "conclusion": "success",
                                        "name": "build",
                                        "started_at": "2026-01-03T10:00:00Z",
                                        "completed_at": "2026-01-03T10:02:00Z",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            current_day_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "kind": "workflow_runs_day",
                            "version": _WORKFLOW_RUNS_DAY_CACHE_VERSION,
                            "owner": "owner",
                            "repo": "repo",
                            "status": "completed",
                            "day": "2026-01-04",
                        },
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "moa.commands.workflow_jobs._fetch_json",
                return_value={"workflow_runs": []},
            ):
                runs = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    stop_before=datetime(2026, 1, 3, tzinfo=timezone.utc),
                    cache_dir=tmp,
                    now=datetime(2026, 1, 4, tzinfo=timezone.utc),
                    status="completed",
                )
            with patch(
                "moa.commands.workflow_jobs._fetch_run_jobs",
                side_effect=RuntimeError("Embedded day-cache jobs should be reused."),
            ):
                rows = _build_duration_rows(
                    "owner",
                    "repo",
                    runs,
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                    cache_dir=tmp,
                )
            self.assertEqual(
                rows,
                [
                    {
                        "run_id": 12,
                        "created_at": "2026-01-03T10:00:00+00:00",
                        "name": "build",
                        "pr": "-",
                        "duration": 120,
                    }
                ],
            )

    def test_fetch_workflow_runs_daily_cache_refreshes_current_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yesterday_path = _workflow_runs_cache_path(
                tmp, "owner", "repo", "completed", datetime(2026, 1, 2, tzinfo=timezone.utc)
            )
            today_path = _workflow_runs_cache_path(
                tmp, "owner", "repo", "completed", datetime(2026, 1, 3, tzinfo=timezone.utc)
            )
            yesterday_path.parent.mkdir(parents=True, exist_ok=True)
            yesterday_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "kind": "workflow_runs_day",
                            "version": _WORKFLOW_RUNS_DAY_CACHE_VERSION,
                            "owner": "owner",
                            "repo": "repo",
                            "status": "completed",
                            "day": "2026-01-02",
                        },
                        "rows": [{"id": 1, "created_at": "2026-01-02T10:00:00Z"}],
                    }
                ),
                encoding="utf-8",
            )
            today_path.write_text(
                json.dumps(
                    {
                        "meta": {
                            "kind": "workflow_runs_day",
                            "version": _WORKFLOW_RUNS_DAY_CACHE_VERSION,
                            "owner": "owner",
                            "repo": "repo",
                            "status": "completed",
                            "day": "2026-01-03",
                        },
                        "rows": [{"id": 2, "created_at": "2026-01-03T10:00:00Z"}],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "moa.commands.workflow_jobs._fetch_json",
                return_value={"workflow_runs": [{"id": 3, "created_at": "2026-01-03T12:00:00Z"}]},
            ) as mocked:
                rows = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    stop_before=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    cache_dir=tmp,
                    now=datetime(2026, 1, 3, 15, 0, 0, tzinfo=timezone.utc),
                    status="completed",
                )
            self.assertEqual(
                rows,
                [
                    {"id": 1, "created_at": "2026-01-02T10:00:00Z"},
                    {"id": 3, "created_at": "2026-01-03T12:00:00Z"},
                ],
            )
            self.assertEqual(mocked.call_count, 1)

    def test_fetch_workflow_runs_invalidates_and_rebuilds_legacy_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for day, rows in [
                ("2026-01-01", []),
                ("2026-01-02", []),
                ("2026-01-03", [{"id": 3, "created_at": "2026-01-03T10:00:00Z"}]),
            ]:
                path = _workflow_runs_cache_path(tmp, "owner", "repo", "completed", day)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "meta": {
                                "kind": "workflow_runs_day",
                                "owner": "owner",
                                "repo": "repo",
                                "status": "completed",
                                "day": day,
                            },
                            "rows": rows,
                        }
                    ),
                    encoding="utf-8",
                )
            captured_urls: list[str] = []

            def fake_fetch_json(url: str, token: str | None) -> dict[str, object]:
                self.assertIsNone(token)
                captured_urls.append(url)
                created = parse_qs(urlparse(url).query)["created"][0]
                if created == "2026-01-01T00:00:00Z..2026-01-01T23:59:59Z":
                    return {"workflow_runs": [{"id": 1, "created_at": "2026-01-01T10:00:00Z"}]}
                if created == "2026-01-02T00:00:00Z..2026-01-02T23:59:59Z":
                    return {"workflow_runs": [{"id": 2, "created_at": "2026-01-02T10:00:00Z"}]}
                if created == "2026-01-03T00:00:00Z..2026-01-03T23:59:59Z":
                    return {"workflow_runs": [{"id": 4, "created_at": "2026-01-03T12:00:00Z"}]}
                raise AssertionError(f"Unexpected created query: {created}")

            with patch("moa.commands.workflow_jobs._fetch_json", side_effect=fake_fetch_json):
                rows = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    stop_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    cache_dir=tmp,
                    now=datetime(2026, 1, 3, 15, 0, 0, tzinfo=timezone.utc),
                    status="completed",
                )
            self.assertEqual(
                rows,
                [
                    {"id": 1, "created_at": "2026-01-01T10:00:00Z"},
                    {"id": 2, "created_at": "2026-01-02T10:00:00Z"},
                    {"id": 4, "created_at": "2026-01-03T12:00:00Z"},
                ],
            )
            self.assertEqual(len(captured_urls), 3)
            day1 = json.loads(
                _workflow_runs_cache_path(
                    tmp, "owner", "repo", "completed", "2026-01-01"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(day1["meta"]["version"], _WORKFLOW_RUNS_DAY_CACHE_VERSION)
            self.assertEqual(day1["rows"], [{"id": 1, "created_at": "2026-01-01T10:00:00Z"}])

    def test_fetch_workflow_runs_stops_when_page_reaches_since(self) -> None:
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=[
                    {
                        "workflow_runs": [
                            {"id": i, "created_at": "2026-02-15T00:00:00Z"} for i in range(100)
                        ]
                    },
                    {
                        "workflow_runs": [
                            {"id": 100 + i, "created_at": "2025-12-15T00:00:00Z"}
                            for i in range(100)
                        ]
                    },
                    {"workflow_runs": [{"id": 201, "created_at": "2025-11-15T00:00:00Z"}]},
                ],
            ) as mocked,
        ):
            rows = _fetch_workflow_runs(
                "owner",
                "repo",
                verbose=True,
                stop_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        # Older page-2 runs are now discarded by the defensive since-bound filtering.
        self.assertEqual(len(rows), 100)
        self.assertEqual(mocked.call_count, 2)
        self.assertIn("stopping workflow runs fetch on page 2", err.getvalue())

    def test_build_queued_rows_sorted_by_name(self) -> None:
        rows = _build_queued_rows(
            [
                {"status": "queued", "name": "zeta", "display_title": "wf-z"},
                {"status": "completed", "name": "beta", "display_title": "wf-b"},
                {"status": "queued", "name": "alpha", "display_title": "wf-a"},
            ]
        )
        self.assertEqual([row["name"] for row in rows], ["alpha", "zeta"])

    def test_build_fail_rate_rows(self) -> None:
        with patch(
            "moa.commands.workflow_jobs._fetch_run_jobs",
            return_value=[
                {"conclusion": "success", "completed_at": "2026-01-03T10:00:00Z"},
                {"conclusion": "failure", "completed_at": "2026-01-03T10:02:00Z"},
                {"conclusion": "skipped", "completed_at": "2026-01-04T10:02:00Z"},
                {"conclusion": "cancelled", "completed_at": "2026-01-04T10:03:00Z"},
            ],
        ):
            rows = _build_fail_rate_rows(
                "owner",
                "repo",
                [{"id": 1}],
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-01-03",
                    "failure": 1,
                    "cancelled": 0,
                    "skipped": 0,
                    "success": 1,
                },
                {
                    "date": "2026-01-04",
                    "failure": 0,
                    "cancelled": 1,
                    "skipped": 1,
                    "success": 0,
                },
            ],
        )

    def test_build_duration_rows_uses_workflow_run_metadata_without_fetching_jobs(self) -> None:
        rows = _build_duration_rows(
            "owner",
            "repo",
            [
                {
                    "id": 1,
                    "name": "build",
                    "conclusion": "success",
                    "created_at": "2026-01-03T10:00:00Z",
                    "updated_at": "2026-01-03T10:02:00Z",
                    "pull_requests": [{"number": 42}],
                }
            ],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            rows,
            [
                {
                    "run_id": 1,
                    "created_at": "2026-01-03T10:00:00+00:00",
                    "name": "build",
                    "pr": "42",
                    "duration": 120,
                }
            ],
        )

    def test_build_duration_rows_prefers_run_started_at_for_duration(self) -> None:
        rows = _build_duration_rows(
            "owner",
            "repo",
            [
                {
                    "id": 1,
                    "name": "build",
                    "conclusion": "success",
                    "created_at": "2026-01-01T00:00:00Z",
                    "run_started_at": "2026-01-12T09:00:00Z",
                    "updated_at": "2026-01-12T10:45:00Z",
                }
            ],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(rows[0]["created_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(rows[0]["duration"], 6300)

    def test_build_fail_rate_rows_uses_workflow_run_metadata_without_fetching_jobs(self) -> None:
        with patch(
            "moa.commands.workflow_jobs._fetch_run_jobs",
            side_effect=RuntimeError("Run-level metadata should avoid job fetches."),
        ):
            rows = _build_fail_rate_rows(
                "owner",
                "repo",
                [
                    {
                        "id": 1,
                        "conclusion": "success",
                        "updated_at": "2026-01-03T10:02:00Z",
                    },
                    {
                        "id": 2,
                        "conclusion": "failure",
                        "updated_at": "2026-01-03T11:02:00Z",
                    },
                ],
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-01-03",
                    "failure": 1,
                    "cancelled": 0,
                    "skipped": 0,
                    "success": 1,
                }
            ],
        )

    def test_build_fail_rate_job_rows_uses_workflow_run_metadata(self) -> None:
        rows = _build_fail_rate_job_rows(
            "owner",
            "repo",
            [
                {
                    "id": 1,
                    "name": "build",
                    "conclusion": "success",
                    "updated_at": "2026-01-03T10:02:00Z",
                },
                {
                    "id": 2,
                    "name": "build",
                    "conclusion": "failure",
                    "updated_at": "2026-01-03T11:02:00Z",
                },
                {
                    "id": 3,
                    "name": "build",
                    "conclusion": "cancelled",
                    "updated_at": "2026-01-03T12:02:00Z",
                },
                {
                    "id": 4,
                    "name": "test",
                    "conclusion": "success",
                    "updated_at": "2026-01-03T13:02:00Z",
                },
            ],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-01-03",
                    "name": "build",
                    "failure": 1,
                    "cancelled": 1,
                    "total": 3,
                    "fail_cancel_rate": 66.67,
                },
                {
                    "date": "2026-01-03",
                    "name": "test",
                    "failure": 0,
                    "cancelled": 0,
                    "total": 1,
                    "fail_cancel_rate": 0.0,
                },
            ],
        )

    def test_build_fail_cost_rows_uses_workflow_run_metadata(self) -> None:
        rows, job_rows = _build_fail_cost_rows(
            "owner",
            "repo",
            [
                {
                    "id": 1,
                    "name": "build",
                    "conclusion": "failure",
                    "created_at": "2026-01-03T10:00:00Z",
                    "run_started_at": "2026-01-03T10:01:00Z",
                    "updated_at": "2026-01-03T10:06:00Z",
                },
                {
                    "id": 2,
                    "name": "build",
                    "conclusion": "cancelled",
                    "created_at": "2026-01-03T11:00:00Z",
                    "run_started_at": "2026-01-03T11:02:00Z",
                    "updated_at": "2026-01-03T11:04:00Z",
                },
                {
                    "id": 3,
                    "name": "test",
                    "conclusion": "success",
                    "created_at": "2026-01-03T12:00:00Z",
                    "run_started_at": "2026-01-03T12:01:00Z",
                    "updated_at": "2026-01-03T12:07:00Z",
                },
            ],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-01-03",
                    "failure_seconds": 300,
                    "cancelled_seconds": 120,
                    "total_seconds": 420,
                }
            ],
        )
        self.assertEqual(
            job_rows,
            [
                {
                    "date": "2026-01-03",
                    "name": "build",
                    "failure_seconds": 300,
                    "cancelled_seconds": 120,
                    "total_seconds": 420,
                }
            ],
        )

    def test_build_waiting_rows_uses_workflow_run_metadata(self) -> None:
        rows = _build_waiting_rows(
            "owner",
            "repo",
            [
                {
                    "id": 1,
                    "name": "build",
                    "conclusion": "success",
                    "created_at": "2026-01-03T10:00:00Z",
                    "run_started_at": "2026-01-03T10:02:00Z",
                    "pull_requests": [{"number": 42}],
                }
            ],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            rows,
            [
                {
                    "run_id": 1,
                    "created_at": "2026-01-03T10:00:00+00:00",
                    "started_at": "2026-01-03T10:02:00+00:00",
                    "name": "build",
                    "pr": "42",
                    "waiting_seconds": 120,
                }
            ],
        )

    def test_build_waiting_rows_keeps_non_success_started_runs(self) -> None:
        rows = _build_waiting_rows(
            "owner",
            "repo",
            [
                {
                    "id": 1,
                    "name": "build",
                    "conclusion": "failure",
                    "created_at": "2026-01-03T10:00:00Z",
                    "run_started_at": "2026-01-03T10:02:00Z",
                }
            ],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["waiting_seconds"], 120)

    def test_build_duration_rows_verbose_reports_progress(self) -> None:
        err = StringIO()
        with patch("sys.stderr", err):
            rows = _build_duration_rows(
                "owner",
                "repo",
                [
                    {
                        "id": 1,
                        "name": "build",
                        "conclusion": "success",
                        "created_at": "2026-01-03T10:00:00Z",
                        "updated_at": "2026-01-03T10:02:00Z",
                    },
                    {
                        "id": 2,
                        "name": "test",
                        "conclusion": "success",
                        "created_at": "2026-01-04T10:00:00Z",
                        "updated_at": "2026-01-04T10:03:00Z",
                    },
                ],
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                verbose=True,
            )
        self.assertEqual(len(rows), 2)
        self.assertIn("collecting duration history from 2 run(s)", err.getvalue())
        self.assertNotIn("fetching jobs for run 1/2 (run_id=1)", err.getvalue())
        self.assertIn("2/2", err.getvalue())

    def test_fetch_run_jobs_verbose_reports_pages(self) -> None:
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=[
                    {
                        "jobs": [
                            {
                                "id": i,
                                "started_at": "2026-01-03T10:00:00Z",
                                "completed_at": "2026-01-03T10:02:00Z",
                            }
                            for i in range(100)
                        ]
                    },
                    {
                        "jobs": [
                            {
                                "id": 101,
                                "started_at": "2026-01-04T10:00:00Z",
                                "completed_at": "2026-01-04T10:05:00Z",
                            }
                        ]
                    },
                ],
            ),
        ):
            rows = _fetch_run_jobs("owner", "repo", 12, verbose=True)
        self.assertEqual(len(rows), 101)
        self.assertIn("fetching jobs page 1 for run_id=12", err.getvalue())
        self.assertIn("fetched 100 job(s) from page 1 for run_id=12", err.getvalue())
        self.assertIn("min(date)=2026-01-03T10:00:00+00:00", err.getvalue())
        self.assertIn("max(date)=2026-01-03T10:02:00+00:00", err.getvalue())
        self.assertIn("fetching jobs page 2 for run_id=12", err.getvalue())

    def test_fetch_run_jobs_writes_and_reuses_output_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = _run_jobs_cache_path(tmp, "owner", "repo", 12)
            with patch(
                "moa.commands.workflow_jobs._fetch_json",
                return_value={"jobs": [{"id": 1, "started_at": "2026-01-03T10:00:00Z"}]},
            ) as mocked:
                rows = _fetch_run_jobs("owner", "repo", 12, cache_path=cache_path)
            self.assertEqual(rows, [{"id": 1, "started_at": "2026-01-03T10:00:00Z"}])
            self.assertTrue(cache_path.exists())
            self.assertEqual(mocked.call_count, 1)
            with patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=RuntimeError("Cache should be used."),
            ):
                cached_rows = _fetch_run_jobs("owner", "repo", 12, cache_path=cache_path)
            self.assertEqual(cached_rows, rows)

    def test_fetch_run_jobs_updates_output_cache_during_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = _run_jobs_cache_path(tmp, "owner", "repo", 12)

            def fake_fetch_json(url: str, token: str | None) -> dict[str, object]:
                self.assertIsNone(token)
                page = parse_qs(urlparse(url).query)["page"][0]
                if page == "1":
                    return {
                        "jobs": [
                            {"id": i, "started_at": "2026-01-03T10:00:00Z"} for i in range(100)
                        ]
                    }
                self.assertTrue(cache_path.exists())
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                self.assertEqual(len(cached["rows"]), 100)
                return {"jobs": [{"id": 101, "started_at": "2026-01-04T10:00:00Z"}]}

            with patch("moa.commands.workflow_jobs._fetch_json", side_effect=fake_fetch_json):
                rows = _fetch_run_jobs("owner", "repo", 12, cache_path=cache_path)
            self.assertEqual(len(rows), 101)

    def test_build_running_rows(self) -> None:
        with patch(
            "moa.commands.workflow_jobs._fetch_run_jobs",
            return_value=[
                {
                    "status": "in_progress",
                    "name": "build",
                    "started_at": "2026-01-03T10:00:00Z",
                    "html_url": "https://x/job-build",
                },
                {
                    "status": "completed",
                    "name": "done",
                    "started_at": "2026-01-03T09:00:00Z",
                    "html_url": "https://x/job-done",
                },
            ],
        ):
            rows = _build_running_rows(
                "owner",
                "repo",
                [{"id": 1, "display_title": "wf-a"}],
                now=datetime(2026, 1, 3, 10, 2, 0, tzinfo=timezone.utc),
            )
        self.assertEqual(
            rows,
            [
                {
                    "name": "build",
                    "workflow": "wf-a",
                    "started_at": "2026-01-03T10:00:00+00:00",
                    "duration_seconds": 120,
                    "url": "https://x/job-build",
                }
            ],
        )

    def test_write_duration_outputs_verbose_reports_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            err = StringIO()
            with patch("sys.stderr", err):
                paths = _write_duration_outputs(
                    [
                        {
                            "run_id": 1,
                            "created_at": "2026-01-03T10:00:00+00:00",
                            "name": "build",
                            "pr": "42",
                            "duration": 120,
                        }
                    ],
                    "owner",
                    "repo",
                    tmp,
                    dump="xlsx",
                    verbose=True,
                )
            self.assertGreaterEqual(len(paths), 4)
            self.assertIn("writing", err.getvalue())
            self.assertIn("generating 3 graph(s)", err.getvalue())
            self.assertIn("3/3", err.getvalue())

    def test_split_duration_outliers_uses_per_name_median(self) -> None:
        plotted, outliers = _split_duration_outliers(
            [
                {
                    "run_id": 1,
                    "created_at": "2026-01-01T10:00:00+00:00",
                    "name": "build",
                    "pr": "1",
                    "duration": 100,
                },
                {
                    "run_id": 2,
                    "created_at": "2026-01-02T10:00:00+00:00",
                    "name": "build",
                    "pr": "2",
                    "duration": 100,
                },
                {
                    "run_id": 3,
                    "created_at": "2026-01-03T10:00:00+00:00",
                    "name": "build",
                    "pr": "3",
                    "duration": 300,
                },
                {
                    "run_id": 4,
                    "created_at": "2026-01-01T12:00:00+00:00",
                    "name": "test",
                    "pr": "4",
                    "duration": 50,
                },
            ]
        )
        self.assertEqual([row["run_id"] for row in plotted], [1, 2, 4])
        self.assertEqual([row["run_id"] for row in outliers], [3])

    def test_split_waiting_outliers_ignores_zero_waits_for_median(self) -> None:
        plotted, outliers = _split_waiting_outliers(
            [
                {
                    "run_id": 1,
                    "created_at": "2026-01-01T10:00:00+00:00",
                    "started_at": "2026-01-01T10:00:00+00:00",
                    "name": "build",
                    "pr": "1",
                    "waiting_seconds": 0,
                },
                {
                    "run_id": 2,
                    "created_at": "2026-01-02T10:00:00+00:00",
                    "started_at": "2026-01-02T10:02:00+00:00",
                    "name": "build",
                    "pr": "2",
                    "waiting_seconds": 120,
                },
                {
                    "run_id": 3,
                    "created_at": "2026-01-03T10:00:00+00:00",
                    "started_at": "2026-01-03T10:03:00+00:00",
                    "name": "build",
                    "pr": "3",
                    "waiting_seconds": 180,
                },
                {
                    "run_id": 4,
                    "created_at": "2026-01-04T10:00:00+00:00",
                    "started_at": "2026-01-04T10:20:00+00:00",
                    "name": "build",
                    "pr": "4",
                    "waiting_seconds": 540,
                },
            ]
        )
        self.assertEqual([row["run_id"] for row in plotted], [1, 2, 3])
        self.assertEqual([row["run_id"] for row in outliers], [4])

    def test_build_average_per_hour_graph_inputs_splits_weekday_and_weekend(self) -> None:
        graphs = _build_average_per_hour_graph_inputs(
            [
                {
                    "created_at": "2026-01-02T10:00:00+00:00",
                    "duration": 120,
                },
                {
                    "created_at": "2026-01-02T10:30:00+00:00",
                    "duration": 240,
                },
                {
                    "created_at": "2026-01-03T10:00:00+00:00",
                    "duration": 600,
                },
            ],
            lambda row: row.get("duration"),
            time_keys=("created_at",),
        )
        weekday_values, weekday_labels = graphs["weekday"]
        weekend_values, weekend_labels = graphs["weekend"]
        self.assertEqual(weekday_values["10h"], 3.0)
        self.assertEqual(weekday_labels["10h"], "n=2")
        self.assertEqual(weekend_values["10h"], 10.0)
        self.assertEqual(weekend_labels["10h"], "n=1")

    def test_write_duration_outputs_dumps_outliers_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_duration_outputs(
                [
                    {
                        "run_id": 1,
                        "created_at": "2026-01-01T10:00:00+00:00",
                        "name": "build",
                        "pr": "1",
                        "duration": 100,
                    },
                    {
                        "run_id": 2,
                        "created_at": "2026-01-02T10:00:00+00:00",
                        "name": "build",
                        "pr": "2",
                        "duration": 110,
                    },
                    {
                        "run_id": 3,
                        "created_at": "2026-01-03T10:00:00+00:00",
                        "name": "build",
                        "pr": "3",
                        "duration": 400,
                        "url": "https://x/runs/3",
                    },
                ],
                "owner",
                "repo",
                tmp,
                dump="xlsx",
            )
            outlier_csv = pathlib.Path(tmp) / "workflow_jobs_duration_outliers_repo.csv"
            outlier_xlsx = pathlib.Path(tmp) / "workflow_jobs_duration_outliers_repo.xlsx"
            self.assertIn(outlier_csv, paths)
            self.assertIn(outlier_xlsx, paths)
            csv_path = pathlib.Path(tmp) / "workflow_jobs_duration_repo.csv"
            with csv_path.open("r", encoding="utf-8") as f:
                duration_rows = list(csv.DictReader(f))
            self.assertEqual(duration_rows[0]["url"], "")
            self.assertEqual(duration_rows[-1]["url"], "https://x/runs/3")
            with outlier_csv.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "3")
            self.assertEqual(rows[0]["url"], "https://x/runs/3")
            graph_svg = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_duration_build.svg"
            weekday_hourly_svg = (
                pathlib.Path(tmp)
                / "graphs_repo"
                / "workflow_jobs_duration_weekday_hourly_average.svg"
            )
            weekend_hourly_svg = (
                pathlib.Path(tmp)
                / "graphs_repo"
                / "workflow_jobs_duration_weekend_hourly_average.svg"
            )
            html_path = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_duration_repo.html"
            self.assertTrue(graph_svg.exists())
            self.assertTrue(weekday_hourly_svg.exists())
            self.assertTrue(weekend_hourly_svg.exists())
            content = graph_svg.read_text(encoding="utf-8")
            self.assertNotIn("2026-01-03", content)
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Avg workflow duration by hour (Weekdays)", html)
            self.assertIn("Avg workflow duration by hour (Weekends)", html)

    def test_write_fail_rate_outputs_writes_per_job_dump_and_graphs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_fail_rate_outputs(
                [
                    {
                        "date": "2026-01-03",
                        "failure": 1,
                        "cancelled": 1,
                        "skipped": 0,
                        "success": 1,
                    }
                ],
                [
                    {
                        "date": "2026-01-03",
                        "name": "build",
                        "failure": 1,
                        "cancelled": 1,
                        "total": 3,
                        "fail_cancel_rate": 66.67,
                    },
                    {
                        "date": "2026-01-04",
                        "name": "test",
                        "failure": 0,
                        "cancelled": 0,
                        "total": 2,
                        "fail_cancel_rate": 0.0,
                    },
                ],
                "owner",
                tmp,
                "repo",
                dump="xlsx",
            )
            job_csv = pathlib.Path(tmp) / "workflow_jobs_fail_rate_by_job_repo.csv"
            job_xlsx = pathlib.Path(tmp) / "workflow_jobs_fail_rate_by_job_repo.xlsx"
            graph_svg = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_fail_rate_build.svg"
            html_path = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_fail_rate_repo.html"
            self.assertIn(job_csv, paths)
            self.assertIn(job_xlsx, paths)
            self.assertIn(graph_svg, paths)
            self.assertIn(html_path, paths)
            self.assertTrue(job_csv.exists())
            self.assertTrue(job_xlsx.exists())
            self.assertTrue(graph_svg.exists())
            self.assertTrue(html_path.exists())
            self.assertFalse(
                (pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_fail_rate_test.svg").exists()
            )
            with job_csv.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["name"], "build")
            self.assertEqual(rows[0]["fail_cancel_rate"], "66.67")
            graph = graph_svg.read_text(encoding="utf-8")
            self.assertIn("#e05c5c", graph)
            self.assertIn("#f2cc60", graph)
            self.assertIn(">failure</text>", graph)
            self.assertIn(">cancelled</text>", graph)
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Workflow fail/cancel rate: build", html)

    def test_write_fail_cost_outputs_writes_per_job_dump_and_graphs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_fail_cost_outputs(
                [
                    {
                        "date": "2026-01-03",
                        "failure_seconds": 3600,
                        "cancelled_seconds": 1800,
                        "total_seconds": 5400,
                    }
                ],
                [
                    {
                        "date": "2026-01-03",
                        "name": "build",
                        "failure_seconds": 3600,
                        "cancelled_seconds": 1800,
                        "total_seconds": 5400,
                    },
                    {
                        "date": "2026-01-04",
                        "name": "test",
                        "failure_seconds": 0,
                        "cancelled_seconds": 0,
                        "total_seconds": 0,
                    },
                ],
                "owner",
                tmp,
                "repo",
                dump="xlsx",
            )
            job_csv = pathlib.Path(tmp) / "workflow_jobs_fail_cost_by_job_repo.csv"
            job_xlsx = pathlib.Path(tmp) / "workflow_jobs_fail_cost_by_job_repo.xlsx"
            graph_svg = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_fail_cost_build.svg"
            html_path = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_fail_cost_repo.html"
            self.assertIn(job_csv, paths)
            self.assertIn(job_xlsx, paths)
            self.assertIn(graph_svg, paths)
            self.assertIn(html_path, paths)
            self.assertTrue(job_csv.exists())
            self.assertTrue(job_xlsx.exists())
            self.assertTrue(graph_svg.exists())
            self.assertTrue(html_path.exists())
            self.assertFalse(
                (pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_fail_cost_test.svg").exists()
            )
            with job_csv.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["name"], "build")
            self.assertEqual(rows[0]["total_seconds"], "5400")
            graph = graph_svg.read_text(encoding="utf-8")
            self.assertIn("#e05c5c", graph)
            self.assertIn("#f2cc60", graph)
            self.assertIn(">failure</text>", graph)
            self.assertIn(">cancelled</text>", graph)
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Workflow fail/cancel cost: build", html)

    def test_build_duration_row_from_run_keeps_html_url(self) -> None:
        row = _build_duration_rows(
            "owner",
            "repo",
            [
                {
                    "id": 3,
                    "created_at": "2026-01-03T10:00:00Z",
                    "updated_at": "2026-01-03T10:06:40Z",
                    "conclusion": "success",
                    "name": "build",
                    "html_url": "https://x/runs/3",
                    "pull_requests": [{"number": 12}],
                }
            ],
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["url"], "https://x/runs/3")

    def test_write_waiting_outputs_writes_csv_and_graphs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_waiting_outputs(
                [
                    {
                        "run_id": 1,
                        "created_at": "2026-01-01T10:00:00+00:00",
                        "started_at": "2026-01-01T10:02:00+00:00",
                        "name": "build",
                        "pr": "1",
                        "waiting_seconds": 120,
                    },
                ],
                "owner",
                "repo",
                tmp,
                dump="xlsx",
            )
            csv_path = pathlib.Path(tmp) / "workflow_jobs_waiting_repo.csv"
            xlsx_path = pathlib.Path(tmp) / "workflow_jobs_waiting_repo.xlsx"
            graph_svg = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_waiting_build.svg"
            weekday_hourly_svg = (
                pathlib.Path(tmp)
                / "graphs_repo"
                / "workflow_jobs_waiting_weekday_hourly_average.svg"
            )
            weekend_hourly_svg = (
                pathlib.Path(tmp)
                / "graphs_repo"
                / "workflow_jobs_waiting_weekend_hourly_average.svg"
            )
            html_path = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_waiting_repo.html"
            self.assertIn(csv_path, paths)
            self.assertIn(xlsx_path, paths)
            self.assertIn(graph_svg, paths)
            self.assertIn(weekday_hourly_svg, paths)
            self.assertIn(weekend_hourly_svg, paths)
            self.assertIn(html_path, paths)
            with csv_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["waiting_seconds"], "120")
            html = html_path.read_text(encoding="utf-8")
            self.assertIn("Avg workflow waiting time by hour (Weekdays)", html)
            self.assertIn("Avg workflow waiting time by hour (Weekends)", html)

    def test_write_waiting_outputs_dumps_outliers_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_waiting_outputs(
                [
                    {
                        "run_id": 0,
                        "created_at": "2026-01-01T09:00:00+00:00",
                        "started_at": "2026-01-01T09:00:00+00:00",
                        "name": "build",
                        "pr": "0",
                        "waiting_seconds": 0,
                    },
                    {
                        "run_id": 1,
                        "created_at": "2026-01-01T10:00:00+00:00",
                        "started_at": "2026-01-01T10:02:00+00:00",
                        "name": "build",
                        "pr": "1",
                        "waiting_seconds": 120,
                    },
                    {
                        "run_id": 2,
                        "created_at": "2026-01-02T10:00:00+00:00",
                        "started_at": "2026-01-02T10:04:00+00:00",
                        "name": "build",
                        "pr": "2",
                        "waiting_seconds": 240,
                    },
                    {
                        "run_id": 3,
                        "created_at": "2026-01-03T10:00:00+00:00",
                        "started_at": "2026-01-03T10:20:00+00:00",
                        "name": "build",
                        "pr": "3",
                        "waiting_seconds": 1200,
                        "url": "https://x/runs/3",
                    },
                ],
                "owner",
                "repo",
                tmp,
                dump="xlsx",
            )
            outlier_csv = pathlib.Path(tmp) / "workflow_jobs_waiting_outliers_repo.csv"
            outlier_xlsx = pathlib.Path(tmp) / "workflow_jobs_waiting_outliers_repo.xlsx"
            self.assertIn(outlier_csv, paths)
            self.assertIn(outlier_xlsx, paths)
            with outlier_csv.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["run_id"], "3")
            self.assertEqual(rows[0]["url"], "https://x/runs/3")
            self.assertEqual([row["waiting_seconds"] for row in rows], ["1200"])
            graph_svg = pathlib.Path(tmp) / "graphs_repo" / "workflow_jobs_waiting_build.svg"
            self.assertTrue(graph_svg.exists())
            content = graph_svg.read_text(encoding="utf-8")
            self.assertNotIn("2026-01-03", content)

    def test_main_queued_prints_fixed_width_table(self) -> None:
        out = StringIO()
        with (
            patch("sys.stdout", out),
            patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
            patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
            patch(
                "moa.commands.workflow_jobs._fetch_workflow_runs",
                return_value=[
                    {
                        "status": "queued",
                        "name": "b",
                        "display_title": "wf-b",
                        "created_at": "2026-01-02T00:00:00Z",
                        "html_url": "https://x/b",
                    },
                    {
                        "status": "queued",
                        "name": "a",
                        "display_title": "wf-a",
                        "created_at": "2026-01-01T00:00:00Z",
                        "html_url": "https://x/a",
                    },
                ],
            ),
        ):
            code = main(["owner", "repo", "--queued"])
        self.assertEqual(code, 0)
        self.assertIn("name  workflow", out.getvalue())
        self.assertNotIn("| name |", out.getvalue())
        self.assertLess(out.getvalue().find("\na   "), out.getvalue().find("\nb   "))

    def test_main_queued_dump_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = StringIO()
            with (
                patch("sys.stdout", out),
                patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
                patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
                patch(
                    "moa.commands.workflow_jobs._fetch_workflow_runs",
                    return_value=[
                        {
                            "status": "queued",
                            "name": "a",
                            "display_title": "wf-a",
                            "created_at": "2026-01-01T00:00:00Z",
                            "html_url": "https://x/a",
                        }
                    ],
                ),
            ):
                code = main(["owner", "repo", "--queued", "--dump", "csv", "--output-dir", tmp])
            self.assertEqual(code, 0)
            csv_path = pathlib.Path(tmp) / "workflow_jobs_queued_repo.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["workflow"], "wf-a")

    def test_main_fail_rate_dump_writes_xlsx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
                patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
                patch("moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]),
                patch(
                    "moa.commands.workflow_jobs._build_fail_rate_rows",
                    return_value=[
                        {
                            "date": "2026-01-03",
                            "failure": 1,
                            "cancelled": 2,
                            "skipped": 3,
                            "success": 4,
                        }
                    ],
                ),
                patch(
                    "moa.commands.workflow_jobs._build_fail_rate_job_rows",
                    return_value=[
                        {
                            "date": "2026-01-03",
                            "name": "build",
                            "failure": 1,
                            "cancelled": 2,
                            "total": 4,
                            "fail_cancel_rate": 75.0,
                        }
                    ],
                ),
            ):
                code = main(
                    ["owner", "repo", "--fail-rate", "--dump", "xlsx", "--output-dir", tmp]
                )
            self.assertEqual(code, 0)
            xlsx_path = pathlib.Path(tmp) / "workflow_jobs_fail_rate_repo.xlsx"
            job_xlsx_path = pathlib.Path(tmp) / "workflow_jobs_fail_rate_by_job_repo.xlsx"
            self.assertTrue(xlsx_path.exists())
            self.assertTrue(job_xlsx_path.exists())
            sheets = pandas.read_excel(xlsx_path, sheet_name=None)
            self.assertEqual(
                sheets["Fail rate"].to_dict(orient="records")[0]["success"],
                4,
            )
            job_sheets = pandas.read_excel(job_xlsx_path, sheet_name=None)
            self.assertEqual(
                job_sheets["Per job"].to_dict(orient="records")[0]["fail_cancel_rate"],
                75.0,
            )

    def test_main_running_dump_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = StringIO()
            with (
                patch("sys.stdout", out),
                patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
                patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
                patch("moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]),
                patch(
                    "moa.commands.workflow_jobs._build_running_rows",
                    return_value=[
                        {
                            "name": "build",
                            "workflow": "wf-a",
                            "started_at": "2026-01-03T10:00:00+00:00",
                            "duration_seconds": 120,
                            "url": "https://x/job-build",
                        }
                    ],
                ),
            ):
                code = main(["owner", "repo", "--running", "--dump", "csv", "--output-dir", tmp])
            self.assertEqual(code, 0)
            csv_path = pathlib.Path(tmp) / "workflow_jobs_running_repo.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["duration_seconds"], "120")

    def test_main_gh_rejects_non_empty_github_token_env(self) -> None:
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "env_token"}, clear=False),
            patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
        ):
            with self.assertRaisesRegex(RuntimeError, "gh auth login"):
                main(["owner", "repo", "--queued", "--gh"])

    def test_main_fail_rate_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = StringIO()
            with (
                patch("sys.stdout", out),
                patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
                patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
                patch("moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]),
                patch(
                    "moa.commands.workflow_jobs._build_fail_rate_rows",
                    return_value=[
                        {
                            "date": "2026-01-03",
                            "failure": 1,
                            "cancelled": 2,
                            "skipped": 3,
                            "success": 4,
                        }
                    ],
                ),
                patch(
                    "moa.commands.workflow_jobs._build_fail_rate_job_rows",
                    return_value=[
                        {
                            "date": "2026-01-03",
                            "name": "build",
                            "failure": 1,
                            "cancelled": 2,
                            "total": 4,
                            "fail_cancel_rate": 75.0,
                        }
                    ],
                ),
            ):
                code = main(["owner", "repo", "--fail-rate", "--output-dir", tmp])
            self.assertEqual(code, 0)
            csv_path = pathlib.Path(tmp) / "workflow_jobs_fail_rate_repo.csv"
            job_csv_path = pathlib.Path(tmp) / "workflow_jobs_fail_rate_by_job_repo.csv"
            self.assertTrue(csv_path.exists())
            self.assertTrue(job_csv_path.exists())
            with csv_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["success"], "4")
            with job_csv_path.open("r", encoding="utf-8") as f:
                job_rows = list(csv.DictReader(f))
            self.assertEqual(job_rows[0]["fail_cancel_rate"], "75.0")

    def test_main_fail_cost_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = StringIO()
            with (
                patch("sys.stdout", out),
                patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
                patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
                patch("moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]),
                patch(
                    "moa.commands.workflow_jobs._build_fail_cost_rows",
                    return_value=(
                        [
                            {
                                "date": "2026-01-03",
                                "failure_seconds": 3600,
                                "cancelled_seconds": 1800,
                                "total_seconds": 5400,
                            }
                        ],
                        [
                            {
                                "date": "2026-01-03",
                                "name": "build",
                                "failure_seconds": 3600,
                                "cancelled_seconds": 1800,
                                "total_seconds": 5400,
                            }
                        ],
                    ),
                ),
            ):
                code = main(["owner", "repo", "--fail-cost", "--output-dir", tmp])
            self.assertEqual(code, 0)
            csv_path = pathlib.Path(tmp) / "workflow_jobs_fail_cost_repo.csv"
            job_csv_path = pathlib.Path(tmp) / "workflow_jobs_fail_cost_by_job_repo.csv"
            self.assertTrue(csv_path.exists())
            self.assertTrue(job_csv_path.exists())
            self.assertIn("failure_seconds", out.getvalue())
            with csv_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["total_seconds"], "5400")
            with job_csv_path.open("r", encoding="utf-8") as f:
                job_rows = list(csv.DictReader(f))
            self.assertEqual(job_rows[0]["name"], "build")

    def test_main_duration_passes_integer_since_as_day_window(self) -> None:
        with (
            patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
            patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
            patch("moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]),
            patch(
                "moa.commands.workflow_jobs._build_duration_rows", return_value=[]
            ) as build_rows,
            patch("moa.commands.workflow_jobs._write_duration_outputs", return_value=[]),
        ):
            before = datetime.now(timezone.utc)
            code = main(["owner", "repo", "--duration", "--since", "5"])
            after = datetime.now(timezone.utc)
        self.assertEqual(code, 0)
        since_arg = build_rows.call_args.args[3]
        self.assertIsInstance(since_arg, datetime)
        self.assertLessEqual(before - since_arg, timedelta(days=5, seconds=1))
        self.assertGreaterEqual(after - since_arg, timedelta(days=5))

    def test_main_waiting_passes_integer_since_as_day_window(self) -> None:
        with (
            patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
            patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
            patch("moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]),
            patch(
                "moa.commands.workflow_jobs._build_waiting_rows", return_value=[]
            ) as build_rows,
            patch("moa.commands.workflow_jobs._write_waiting_outputs", return_value=[]),
        ):
            before = datetime.now(timezone.utc)
            code = main(["owner", "repo", "--waiting", "--since", "5"])
            after = datetime.now(timezone.utc)
        self.assertEqual(code, 0)
        since_arg = build_rows.call_args.args[3]
        self.assertIsInstance(since_arg, datetime)
        self.assertLessEqual(before - since_arg, timedelta(days=5, seconds=1))
        self.assertGreaterEqual(after - since_arg, timedelta(days=5))

    def test_fetch_workflow_runs_history_uses_subfolder_and_created_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured_urls: list[str] = []

            def fake_fetch_json(url: str, token: str | None) -> dict[str, object]:
                self.assertIsNone(token)
                captured_urls.append(url)
                created = parse_qs(urlparse(url).query)["created"][0]
                if created == "2026-01-02T00:00:00Z..2026-01-02T23:59:59Z":
                    return {"workflow_runs": [{"id": 1, "created_at": "2026-01-02T00:00:00Z"}]}
                if created == "2026-01-03T00:00:00Z..2026-01-03T23:59:59Z":
                    return {"workflow_runs": [{"id": 2, "created_at": "2026-01-03T00:00:00Z"}]}
                raise AssertionError(f"Unexpected created query: {created}")

            with patch("moa.commands.workflow_jobs._fetch_json", side_effect=fake_fetch_json):
                rows = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    status="completed",
                    stop_before=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    cache_dir=tmp,
                    now=datetime(2026, 1, 3, 12, 0, 0, tzinfo=timezone.utc),
                )
            self.assertEqual(
                rows,
                [
                    {"id": 1, "created_at": "2026-01-02T00:00:00Z"},
                    {"id": 2, "created_at": "2026-01-03T00:00:00Z"},
                ],
            )
            self.assertEqual(len(captured_urls), 2)
            for url in captured_urls:
                query = parse_qs(urlparse(url).query)
                self.assertEqual(query["status"], ["completed"])
            self.assertTrue(
                _workflow_runs_cache_path(
                    tmp,
                    "owner",
                    "repo",
                    "completed",
                    datetime(2026, 1, 3, tzinfo=timezone.utc),
                ).exists()
            )

    def test_fetch_workflow_runs_history_fetches_each_day_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            captured_created: list[str] = []

            def fake_fetch_json(url: str, token: str | None) -> dict[str, object]:
                self.assertIsNone(token)
                created = parse_qs(urlparse(url).query)["created"][0]
                captured_created.append(created)
                rows = {
                    "2026-01-01T00:00:00Z..2026-01-01T23:59:59Z": [
                        {"id": 1, "created_at": "2026-01-01T10:00:00Z"}
                    ],
                    "2026-01-02T00:00:00Z..2026-01-02T23:59:59Z": [
                        {"id": 2, "created_at": "2026-01-02T10:00:00Z"}
                    ],
                    "2026-01-03T00:00:00Z..2026-01-03T23:59:59Z": [
                        {"id": 3, "created_at": "2026-01-03T10:00:00Z"}
                    ],
                }
                return {"workflow_runs": rows[created]}

            with patch("moa.commands.workflow_jobs._fetch_json", side_effect=fake_fetch_json):
                rows = _fetch_workflow_runs(
                    "owner",
                    "repo",
                    status="completed",
                    stop_before=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    cache_dir=tmp,
                    now=datetime(2026, 1, 3, 12, 0, 0, tzinfo=timezone.utc),
                )
            self.assertEqual(
                rows,
                [
                    {"id": 1, "created_at": "2026-01-01T10:00:00Z"},
                    {"id": 2, "created_at": "2026-01-02T10:00:00Z"},
                    {"id": 3, "created_at": "2026-01-03T10:00:00Z"},
                ],
            )
            self.assertEqual(
                captured_created,
                [
                    "2026-01-01T00:00:00Z..2026-01-01T23:59:59Z",
                    "2026-01-02T00:00:00Z..2026-01-02T23:59:59Z",
                    "2026-01-03T00:00:00Z..2026-01-03T23:59:59Z",
                ],
            )

    def test_main_duration_fetches_completed_runs(self) -> None:
        with (
            patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
            patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
            patch(
                "moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]
            ) as fetch_runs,
            patch("moa.commands.workflow_jobs._build_duration_rows", return_value=[]),
            patch("moa.commands.workflow_jobs._write_duration_outputs", return_value=[]),
        ):
            code = main(["owner", "repo", "--duration", "--since", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(fetch_runs.call_args.kwargs["status"], "completed")

    def test_main_waiting_fetches_completed_runs(self) -> None:
        with (
            patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
            patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
            patch(
                "moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]
            ) as fetch_runs,
            patch("moa.commands.workflow_jobs._build_waiting_rows", return_value=[]),
            patch("moa.commands.workflow_jobs._write_waiting_outputs", return_value=[]),
        ):
            code = main(["owner", "repo", "--waiting", "--since", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(fetch_runs.call_args.kwargs["status"], "completed")

    def test_main_fail_cost_fetches_completed_runs(self) -> None:
        with (
            patch("moa.commands.workflow_jobs._load_token_cache", return_value={}),
            patch("moa.commands.workflow_jobs._resolve_cached_token", return_value=None),
            patch(
                "moa.commands.workflow_jobs._fetch_workflow_runs", return_value=[]
            ) as fetch_runs,
            patch("moa.commands.workflow_jobs._build_fail_cost_rows", return_value=([], [])),
            patch("moa.commands.workflow_jobs._write_fail_cost_outputs", return_value=[]),
        ):
            code = main(["owner", "repo", "--fail-cost", "--since", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(fetch_runs.call_args.kwargs["status"], "completed")
