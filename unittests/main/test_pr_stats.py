import csv
import hashlib
import json
import pathlib
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

import pandas

from moa.commands.pr_stats import (
    DEFAULT_OUTPUT_DIR,
    SVG_LABEL_CHAR_WIDTH,
    _build_avg_duration_per_user_week_rows,
    _collect_pr_job_duration_seconds,
    _count_comments,
    _default_since,
    _save_bar_graph,
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
            },
            {
                "number": 2,
                "author": "bob",
                "title": "B",
                "created_at": "2026-01-08T00:00:00Z",
                "merged_at": "",
                "closed_at": "2026-01-08T12:00:00Z",
                "status": "cancelled",
                "manual_comments": 1,
                "copilot_commands": 2,
                "total_job_duration_seconds": 0,
                "html_url": "https://github.com/o/r/pull/2",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch("moa.commands.pr_stats.build_pr_activity_rows", return_value=fake_rows):
                outputs = save_pr_activity_report("o", "r", output_dir=tmp, prefix="report")
            self.assertTrue(outputs["csv"].exists())
            self.assertTrue(outputs["xlsx"].exists())
            self.assertTrue(outputs["status_svg"].exists())
            self.assertTrue(outputs["comments_svg"].exists())
            self.assertTrue(outputs["prs_per_week_svg"].exists())
            self.assertTrue(outputs["comments_per_pr_svg"].exists())
            self.assertTrue(outputs["comments_per_week_svg"].exists())
            self.assertTrue(outputs["avg_duration_per_user_svg"].exists())
            self.assertTrue(outputs["avg_duration_per_week_svg"].exists())
            self.assertTrue(outputs["cache"].exists())
            with outputs["csv"].open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            cache = json.loads(outputs["cache"].read_text(encoding="utf-8"))
            status_svg = outputs["status_svg"].read_text(encoding="utf-8")
            comments_per_pr_svg = outputs["comments_per_pr_svg"].read_text(encoding="utf-8")
            comments_per_week_svg = outputs["comments_per_week_svg"].read_text(encoding="utf-8")
            prs_per_week_svg = outputs["prs_per_week_svg"].read_text(encoding="utf-8")
            avg_duration_per_user_svg = outputs["avg_duration_per_user_svg"].read_text(
                encoding="utf-8"
            )
            avg_duration_per_week_svg = outputs["avg_duration_per_week_svg"].read_text(
                encoding="utf-8"
            )
            xlsx_sheets = pandas.read_excel(outputs["xlsx"], sheet_name=None)
        self.assertEqual(rows[0]["author"], "alice")
        self.assertEqual(rows[0]["copilot_commands"], "1")
        self.assertEqual(rows[0]["total_job_duration_seconds"], "180")
        self.assertIn("prefers-color-scheme: dark", status_svg)
        self.assertIn('class="bar"', status_svg)
        self.assertIn('class="label"', status_svg)
        self.assertIn("1", cache["rows"])
        self.assertIn('transform="rotate(-30', status_svg)
        self.assertIn("Pull requests per week", prs_per_week_svg)
        self.assertIn("Comments per pull request", comments_per_pr_svg)
        self.assertIn("Comments per week", comments_per_week_svg)
        self.assertIn("Avg PR duration per user", avg_duration_per_user_svg)
        self.assertIn("Avg PR duration per week", avg_duration_per_week_svg)
        self.assertEqual(
            set(xlsx_sheets),
            {
                "PR activity",
                "PRs per week",
                "Comments per PR",
                "Comments per week",
                "Avg PR duration",
            },
        )
        self.assertEqual(
            xlsx_sheets["PR activity"].to_dict(orient="records")[0]["author"], "alice"
        )
        self.assertEqual(
            xlsx_sheets["PR activity"].to_dict(orient="records")[0]["copilot_commands"], 1
        )
        self.assertEqual(
            xlsx_sheets["PRs per week"].to_dict(orient="records"),
            [
                {"week": "2026-W01", "pull_requests": 1},
                {"week": "2026-W02", "pull_requests": 1},
            ],
        )
        self.assertEqual(
            xlsx_sheets["Comments per PR"].to_dict(orient="records"),
            [
                {
                    "number": 1,
                    "title": "A",
                    "created_at": "2026-01-01T00:00:00Z",
                    "manual_comments": 2,
                    "copilot_commands": 1,
                    "total_comments": 3,
                },
                {
                    "number": 2,
                    "title": "B",
                    "created_at": "2026-01-08T00:00:00Z",
                    "manual_comments": 1,
                    "copilot_commands": 2,
                    "total_comments": 3,
                },
            ],
        )
        self.assertEqual(
            xlsx_sheets["Comments per week"].to_dict(orient="records"),
            [
                {
                    "week": "2026-W01",
                    "manual_comments": 2,
                    "copilot_commands": 1,
                    "total_comments": 3,
                },
                {
                    "week": "2026-W02",
                    "manual_comments": 1,
                    "copilot_commands": 2,
                    "total_comments": 3,
                },
            ],
        )
        # Only PR #1 (alice, merged 2026-01-03 in W01) contributes; duration = 2 days = 172800s
        self.assertEqual(
            xlsx_sheets["Avg PR duration"].to_dict(orient="records"),
            [
                {
                    "author": "alice",
                    "week": "2026-W01",
                    "pr_count": 1,
                    "avg_duration_seconds": 172800,
                }
            ],
        )

    def test_build_avg_duration_per_user_week_rows(self) -> None:
        # ISO weeks: W01=Dec29-Jan4, W02=Jan5-Jan11, W03=Jan12-Jan18
        rows = [
            {
                "author": "alice",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-03T00:00:00Z",  # 2 days = 172800s, merged in W01
            },
            {
                "author": "alice",
                "created_at": "2026-01-05T00:00:00Z",
                "merged_at": "2026-01-07T00:00:00Z",  # 2 days = 172800s, merged in W02
            },
            {
                "author": "bob",
                "created_at": "2026-01-12T00:00:00Z",
                "merged_at": "2026-01-13T00:00:00Z",  # 1 day = 86400s, merged in W03
            },
            {
                "author": "carol",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "",  # not merged, must be excluded
            },
        ]
        result = _build_avg_duration_per_user_week_rows(rows)
        self.assertEqual(len(result), 3)
        alice_w01 = next(r for r in result if r["author"] == "alice" and r["week"] == "2026-W01")
        alice_w02 = next(r for r in result if r["author"] == "alice" and r["week"] == "2026-W02")
        bob_row = next(r for r in result if r["author"] == "bob")
        self.assertEqual(alice_w01["pr_count"], 1)
        self.assertEqual(alice_w01["avg_duration_seconds"], 172800)
        self.assertEqual(alice_w02["pr_count"], 1)
        self.assertEqual(alice_w02["avg_duration_seconds"], 172800)
        self.assertEqual(bob_row["week"], "2026-W03")
        self.assertEqual(bob_row["pr_count"], 1)
        self.assertEqual(bob_row["avg_duration_seconds"], 86400)

    def test_save_pr_activity_report_writes_valid_xlsx_xml(self) -> None:
        fake_rows = [
            {
                "number": 1,
                "author": "alice",
                "title": "bad\x0btitle",
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
            with zipfile.ZipFile(outputs["xlsx"], "r") as zf:
                worksheet_xml = zf.read("xl/worksheets/sheet1.xml")

        ET.fromstring(worksheet_xml)
        self.assertNotIn(b"\x0b", worksheet_xml)

    def test_save_bar_graph_adds_left_margin_for_long_x_labels(self) -> None:
        values = {"manual_comments": 4, "copilot_commands": 2}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "graph.svg"
            _save_bar_graph(path, values, "Long labels")
            svg = path.read_text(encoding="utf-8")
        expected_left = max(60, 20 + len("copilot_commands") * SVG_LABEL_CHAR_WIDTH)
        self.assertIn(f'x1="{expected_left - 20}"', svg)
        self.assertIn(f'<rect x="{expected_left}"', svg)
        self.assertIn('transform="rotate(-30 ', svg)
        self.assertIn("manual_comments", svg)
        self.assertIn("copilot_commands", svg)

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

    def test_default_since_format_and_value(self) -> None:
        result = _default_since()
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}$")
        # Must be roughly 6 months before today
        today = datetime.now(timezone.utc)
        result_dt = datetime.fromisoformat(result)
        diff_days = (today.replace(tzinfo=None) - result_dt).days
        self.assertGreaterEqual(diff_days, 180)
        self.assertLessEqual(diff_days, 185)

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
                patch(
                    "moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths
                ) as mocked_save,
                patch("sys.stdout", out),
            ):
                code = main(["owner", "repo"])
        self.assertEqual(code, 0)
        self.assertEqual(mocked_save.call_args.kwargs["output_dir"], DEFAULT_OUTPUT_DIR)
        self.assertEqual(mocked_save.call_args.kwargs["prefix"], "pr_activity_repo")
        # --since should default to 6 months ago (not None)
        since_val = mocked_save.call_args.kwargs["since"]
        self.assertIsNotNone(since_val)
        self.assertRegex(since_val, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn(".csv", out.getvalue())
        self.assertIn(".xlsx", out.getvalue())

    def test_main_default_prefix_sanitizes_repo_name(self) -> None:
        fake_paths = {
            "csv": "/tmp/a.csv",
            "xlsx": "/tmp/a.xlsx",
            "status_svg": "/tmp/a_status.svg",
            "comments_svg": "/tmp/a_comments.svg",
            "cache": "/tmp/a_cache.json",
        }
        with patch(
            "moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths
        ) as mocked:
            code = main(["owner", "my:repo/name"])
        self.assertEqual(code, 0)
        self.assertEqual(mocked.call_args.kwargs["prefix"], "pr_activity_my_repo_name")

    def test_main_default_prefix_fallback_is_stable_for_invalid_repo(self) -> None:
        fake_paths = {
            "csv": "/tmp/a.csv",
            "xlsx": "/tmp/a.xlsx",
            "status_svg": "/tmp/a_status.svg",
            "comments_svg": "/tmp/a_comments.svg",
            "cache": "/tmp/a_cache.json",
        }
        with patch(
            "moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths
        ) as mocked:
            code = main(["owner", "..."])
        self.assertEqual(code, 0)
        expected = hashlib.sha256(b"...").hexdigest()[:8]
        self.assertEqual(mocked.call_args.kwargs["prefix"], f"pr_activity_repo_{expected}")

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

    def test_main_verbose_flag_prints_progress(self) -> None:
        out = StringIO()
        err = StringIO()
        fake_paths = {
            "csv": "/tmp/a.csv",
            "xlsx": "/tmp/a.xlsx",
            "status_svg": "/tmp/a_status.svg",
            "comments_svg": "/tmp/a_comments.svg",
            "cache": "/tmp/a_cache.json",
        }
        with (
            patch("moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths),
            patch("sys.stdout", out),
            patch("sys.stderr", err),
        ):
            code = main(["-v", "owner", "repo"])
        self.assertEqual(code, 0)
        self.assertIn("pr-stats: collecting pull request data for owner/repo...", err.getvalue())
        self.assertIn("pr-stats: done.", err.getvalue())
