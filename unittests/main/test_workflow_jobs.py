import csv
import os
import pathlib
import tempfile
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch
from urllib.error import HTTPError

import pandas

from moa.commands.workflow_jobs import (
    _build_duration_rows,
    _build_fail_rate_rows,
    _build_queued_rows,
    _build_running_rows,
    _fetch_run_jobs,
    _fetch_workflow_runs,
    _write_duration_outputs,
    main,
)
from moa.ext_test_case import ExtTestCase


class TestWorkflowJobs(ExtTestCase):
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
                    {"workflow_runs": [{"id": i} for i in range(100)]},
                    {"workflow_runs": [{"id": 101}]},
                ],
            ),
        ):
            rows = _fetch_workflow_runs("owner", "repo", verbose=True)
        self.assertEqual(len(rows), 101)
        self.assertIn("fetching workflow runs page 1", err.getvalue())
        self.assertIn("fetched 100 workflow run(s) from page 1", err.getvalue())
        self.assertIn("fetching workflow runs page 2", err.getvalue())

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

    def test_build_duration_rows_verbose_reports_progress(self) -> None:
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch(
                "moa.commands.workflow_jobs._fetch_run_jobs",
                return_value=[
                    {
                        "conclusion": "success",
                        "name": "build",
                        "started_at": "2026-01-03T10:00:00Z",
                        "completed_at": "2026-01-03T10:02:00Z",
                    }
                ],
            ),
        ):
            rows = _build_duration_rows(
                "owner",
                "repo",
                [{"id": 1}, {"id": 2}],
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                verbose=True,
            )
        self.assertEqual(len(rows), 2)
        self.assertIn("collecting duration history from 2 run(s)", err.getvalue())
        self.assertIn("fetching jobs for run 1/2 (run_id=1)", err.getvalue())
        self.assertIn("2/2", err.getvalue())

    def test_fetch_run_jobs_verbose_reports_pages(self) -> None:
        err = StringIO()
        with (
            patch("sys.stderr", err),
            patch(
                "moa.commands.workflow_jobs._fetch_json",
                side_effect=[
                    {"jobs": [{"id": i} for i in range(100)]},
                    {"jobs": [{"id": 101}]},
                ],
            ),
        ):
            rows = _fetch_run_jobs("owner", "repo", 12, verbose=True)
        self.assertEqual(len(rows), 101)
        self.assertIn("fetching jobs page 1 for run_id=12", err.getvalue())
        self.assertIn("fetched 100 job(s) from page 1 for run_id=12", err.getvalue())
        self.assertIn("fetching jobs page 2 for run_id=12", err.getvalue())

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
                            "job_name": "build",
                            "completed_at": "2026-01-03T10:02:00+00:00",
                            "duration_seconds": 120,
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
            self.assertIn("generating 1 graph(s)", err.getvalue())
            self.assertIn("1/1", err.getvalue())

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
            ):
                code = main(
                    ["owner", "repo", "--fail-rate", "--dump", "xlsx", "--output-dir", tmp]
                )
            self.assertEqual(code, 0)
            xlsx_path = pathlib.Path(tmp) / "workflow_jobs_fail_rate_repo.xlsx"
            self.assertTrue(xlsx_path.exists())
            sheets = pandas.read_excel(xlsx_path, sheet_name=None)
            self.assertEqual(
                sheets["Fail rate"].to_dict(orient="records")[0]["success"],
                4,
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
            ):
                code = main(["owner", "repo", "--fail-rate", "--output-dir", tmp])
            self.assertEqual(code, 0)
            csv_path = pathlib.Path(tmp) / "workflow_jobs_fail_rate_repo.csv"
            self.assertTrue(csv_path.exists())
            with csv_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["success"], "4")
