import csv
import json
import tempfile
from io import StringIO
from unittest.mock import patch

from moa.commands.pr_stats import (
    _collect_pr_job_duration_seconds,
    _count_comments,
    build_pr_activity_rows,
    main,
    save_pr_activity_report,
)
from moa.ext_test_case import ExtTestCase


class TestPRStats(ExtTestCase):
    def test_count_comments_splits_manual_and_copilot(self) -> None:
        manual, copilot = _count_comments(
            [
                "Looks good.",
                "@copilot please check tests",
                "Can you update docs?",
                "/copilot summarize changes",
            ]
        )
        self.assertEqual(manual, 2)
        self.assertEqual(copilot, 2)

    def test_build_pr_activity_rows_skips_non_closed(self) -> None:
        pulls = [
            {
                "number": 10,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "A",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/10",
            },
            {"number": 11, "state": "open"},
        ]
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats", return_value=(3, 1)),
            patch("moa.commands.pr_stats._collect_pr_job_duration_seconds", return_value=240),
        ):
            rows = build_pr_activity_rows("o", "r")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], 10)
        self.assertEqual(rows[0]["status"], "merged")
        self.assertEqual(rows[0]["manual_comments"], 3)
        self.assertEqual(rows[0]["copilot_commands"], 1)
        self.assertEqual(rows[0]["total_job_duration_seconds"], 240)

    def test_save_pr_activity_report_writes_files(self) -> None:
        fake_rows = [
            {
                "number": 1,
                "author": "alice",
                "title": "A",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-03T00:00:00Z",
                "closed_at": "2026-01-03T00:00:00Z",
                "status": "merged",
                "manual_comments": 2,
                "copilot_commands": 1,
                "total_job_duration_seconds": 180,
                "html_url": "https://github.com/o/r/pull/1",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch("moa.commands.pr_stats.build_pr_activity_rows", return_value=fake_rows):
                outputs = save_pr_activity_report("o", "r", output_dir=tmp, prefix="report")
            self.assertTrue(outputs["csv"].exists())
            self.assertTrue(outputs["xlsx"].exists())
            self.assertTrue(outputs["status_svg"].exists())
            self.assertTrue(outputs["comments_svg"].exists())
            self.assertTrue(outputs["cache"].exists())
            with outputs["csv"].open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            status_svg = outputs["status_svg"].read_text(encoding="utf-8")
            cache = json.loads(outputs["cache"].read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["author"], "alice")
        self.assertEqual(rows[0]["copilot_commands"], "1")
        self.assertEqual(rows[0]["total_job_duration_seconds"], "180")
        self.assertIn("prefers-color-scheme: dark", status_svg)
        self.assertIn('class="bar"', status_svg)
        self.assertIn('class="label"', status_svg)
        self.assertIn("1", cache["rows"])

    def test_build_pr_activity_rows_respects_since_date(self) -> None:
        pulls = [
            {
                "number": 12,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "Old",
                "created_at": "2025-12-31T12:00:00Z",
                "merged_at": None,
                "closed_at": "2025-12-31T13:00:00Z",
                "html_url": "https://github.com/o/r/pull/12",
            },
            {
                "number": 13,
                "state": "closed",
                "user": {"login": "bob"},
                "title": "New",
                "created_at": "2026-01-02T12:00:00Z",
                "merged_at": "2026-01-03T12:00:00Z",
                "closed_at": "2026-01-03T12:00:00Z",
                "html_url": "https://github.com/o/r/pull/13",
            },
        ]
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats", return_value=(1, 0)),
            patch("moa.commands.pr_stats._collect_pr_job_duration_seconds", return_value=30),
        ):
            rows = build_pr_activity_rows("o", "r", since="2026-01-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], 13)

    def test_build_pr_activity_rows_reuses_cache(self) -> None:
        pulls = [
            {
                "number": 22,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "Cached PR",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/22",
            }
        ]
        cached_rows = {
            "22": {
                "number": 22,
                "author": "alice",
                "title": "Cached PR",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "status": "merged",
                "manual_comments": 9,
                "copilot_commands": 4,
                "total_job_duration_seconds": 456,
                "html_url": "https://github.com/o/r/pull/22",
            }
        }
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats") as mocked_collect,
        ):
            rows = build_pr_activity_rows("o", "r", cached_rows=cached_rows)
        self.assertEqual(rows[0]["manual_comments"], 9)
        self.assertEqual(rows[0]["copilot_commands"], 4)
        self.assertEqual(rows[0]["total_job_duration_seconds"], 456)
        mocked_collect.assert_not_called()

    def test_main_prints_output_paths(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            fake_paths = {
                "csv": tempfile.NamedTemporaryFile(dir=tmp, suffix=".csv", delete=False).name,
                "xlsx": tempfile.NamedTemporaryFile(dir=tmp, suffix=".xlsx", delete=False).name,
                "status_svg": tempfile.NamedTemporaryFile(
                    dir=tmp, suffix=".svg", delete=False
                ).name,
                "comments_svg": tempfile.NamedTemporaryFile(
                    dir=tmp, suffix=".svg", delete=False
                ).name,
            }
            with (
                patch("moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths),
                patch("sys.stdout", out),
            ):
                code = main(["owner", "repo"])
        self.assertEqual(code, 0)
        self.assertIn(".csv", out.getvalue())
        self.assertIn(".xlsx", out.getvalue())

    def test_collect_pr_job_duration_seconds(self) -> None:
        runs_payload = {
            "workflow_runs": [
                {"id": 101, "pull_requests": [{"number": 22}]},
                {"id": 102, "pull_requests": [{"number": 99}]},
            ]
        }
        jobs_run_101 = {
            "jobs": [
                {
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:01:30Z",
                },
                {
                    "started_at": "2026-01-01T00:02:00Z",
                    "completed_at": "2026-01-01T00:03:00Z",
                },
            ]
        }
        with patch(
            "moa.commands.pr_stats._fetch_json",
            side_effect=[runs_payload, jobs_run_101],
        ):
            duration = _collect_pr_job_duration_seconds("o", "r", 22, "abc")
        self.assertEqual(duration, 150)
