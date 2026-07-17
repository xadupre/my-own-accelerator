import csv
import hashlib
import json
import os
import pathlib
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch
from urllib.error import HTTPError

import pandas

from moa.commands.pr_stats import (
    DEFAULT_OUTPUT_DIR,
    SVG_LABEL_CHAR_WIDTH,
    SVG_X_AXIS_LABEL_ROTATION,
    _build_avg_duration_per_job_rows,
    _build_avg_duration_per_user_rows,
    _build_avg_duration_per_user_week_rows,
    _build_job_duration_sheet_rows,
    _build_pr_comments_distribution,
    _collect_pr_comment_stats,
    _collect_pr_comment_stats_batch,
    _collect_pr_job_duration_hours,
    _collect_pr_job_info,
    _collect_pr_job_info_batch,
    _count_comments,
    _default_since,
    _parse_since_datetime,
    _print_progress,
    build_pr_activity_rows,
    main,
    save_pr_activity_report,
)
from moa.commands.pr_stats_graphs import (
    compute_moving_average,
    save_bar_graph,
    save_job_duration_line_graph,
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
            patch(
                "moa.commands.pr_stats._collect_pr_job_info",
                return_value=(240, []),
            ),
        ):
            rows = build_pr_activity_rows("o", "r")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], 10)
        self.assertEqual(rows[0]["status"], "merged")
        self.assertEqual(rows[0]["manual_comments"], 3)
        self.assertEqual(rows[0]["copilot_commands"], 1)
        self.assertAlmostEqual(rows[0]["total_job_duration_hours"], round(240 / 3600, 2))
        self.assertEqual(rows[0]["successful_job_durations"], [])

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
                "total_job_duration_hours": round(180 / 3600, 2),
                "successful_job_durations": [
                    {
                        "job_name": "build",
                        "completed_at": "2026-01-03T01:00:00Z",
                        "duration_seconds": 120,
                    },
                    {
                        "job_name": "test",
                        "completed_at": "2026-01-03T01:02:00Z",
                        "duration_seconds": 60,
                    },
                ],
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
                "total_job_duration_hours": 0.0,
                "successful_job_durations": [],
                "html_url": "https://github.com/o/r/pull/2",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch("moa.commands.pr_stats.build_pr_activity_rows", return_value=fake_rows):
                outputs = save_pr_activity_report(
                    "o", "my:repo/name", output_dir=tmp, prefix="report"
                )
            self.assertTrue(outputs["csv"].exists())
            self.assertTrue(outputs["xlsx"].exists())
            self.assertTrue(outputs["status_svg"].exists())
            self.assertTrue(outputs["comments_svg"].exists())
            self.assertTrue(outputs["prs_per_week_svg"].exists())
            self.assertTrue(outputs["comments_per_pr_svg"].exists())
            self.assertTrue(outputs["comments_per_week_svg"].exists())
            self.assertTrue(outputs["avg_duration_per_user_svg"].exists())
            self.assertTrue(outputs["avg_duration_per_week_svg"].exists())
            self.assertTrue(outputs["avg_duration_per_job_svg"].exists())
            self.assertTrue(outputs["graphs_html"].exists())
            self.assertTrue(outputs["cache"].exists())
            graph_dir = pathlib.Path(tmp) / "graphs_my_repo_name"
            self.assertEqual(outputs["csv"].parent, pathlib.Path(tmp))
            self.assertEqual(outputs["xlsx"].parent, pathlib.Path(tmp))
            self.assertEqual(outputs["cache"].parent, pathlib.Path(tmp))
            self.assertEqual(outputs["status_svg"].parent, graph_dir)
            self.assertEqual(outputs["comments_svg"].parent, graph_dir)
            self.assertEqual(outputs["prs_per_week_svg"].parent, graph_dir)
            self.assertEqual(outputs["comments_per_pr_svg"].parent, graph_dir)
            self.assertEqual(outputs["comments_per_week_svg"].parent, graph_dir)
            self.assertEqual(outputs["avg_duration_per_user_svg"].parent, graph_dir)
            self.assertEqual(outputs["avg_duration_per_week_svg"].parent, graph_dir)
            self.assertEqual(outputs["graphs_html"].parent, graph_dir)
            job_dur_svgs = outputs["job_duration_svgs"]
            self.assertIn("build", job_dur_svgs)
            self.assertIn("test", job_dur_svgs)
            self.assertTrue(job_dur_svgs["build"].exists())
            self.assertTrue(job_dur_svgs["test"].exists())
            self.assertEqual(job_dur_svgs["build"].parent, graph_dir / "job_durations")
            self.assertEqual(job_dur_svgs["test"].parent, graph_dir / "job_durations")
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
            graphs_html = outputs["graphs_html"].read_text(encoding="utf-8")
            job_build_svg = job_dur_svgs["build"].read_text(encoding="utf-8")
            avg_dur_per_job_svg = outputs["avg_duration_per_job_svg"].read_text(encoding="utf-8")
            xlsx_sheets = pandas.read_excel(outputs["xlsx"], sheet_name=None)
        self.assertEqual(rows[0]["author"], "alice")
        self.assertEqual(rows[0]["copilot_commands"], "1")
        self.assertEqual(rows[0]["total_job_duration_hours"], "0.05")
        self.assertIn("prefers-color-scheme: dark", status_svg)
        self.assertIn('class="bar"', status_svg)
        self.assertIn('class="label"', status_svg)
        self.assertIn("1", cache["rows"])
        self.assertIn(f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION}', status_svg)
        self.assertIn("Pull requests per week", prs_per_week_svg)
        self.assertIn("Pull requests (count)", prs_per_week_svg)
        self.assertIn("Week", prs_per_week_svg)
        self.assertIn("2025-12-29", prs_per_week_svg)
        self.assertIn("2026-01-05", prs_per_week_svg)
        self.assertNotIn("2026-W01", prs_per_week_svg)
        self.assertIn("PR count by number of comments", comments_per_pr_svg)
        self.assertIn("Comments per week", comments_per_week_svg)
        self.assertIn("Comments (count)", comments_per_week_svg)
        self.assertIn("2025-12-29", comments_per_week_svg)
        self.assertIn("2026-01-05", comments_per_week_svg)
        self.assertNotIn("2026-W01", comments_per_week_svg)
        self.assertIn("Avg PR duration per user", avg_duration_per_user_svg)
        self.assertIn("Duration (hours)", avg_duration_per_user_svg)
        self.assertIn("Author", avg_duration_per_user_svg)
        self.assertIn("n=1", avg_duration_per_user_svg)
        self.assertNotIn(
            f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} ', avg_duration_per_user_svg
        )
        self.assertIn("Avg PR duration per week", avg_duration_per_week_svg)
        self.assertIn("2025-12-29", avg_duration_per_week_svg)
        self.assertNotIn("2026-W01", avg_duration_per_week_svg)
        self.assertIn("PR stats graphs for o/my:repo/name", graphs_html)
        self.assertIn("Pull requests by status", graphs_html)
        self.assertIn("Manual comments vs Copilot commands", graphs_html)
        self.assertIn("Pull requests per week", graphs_html)
        self.assertIn("PR count by number of comments", graphs_html)
        self.assertIn("Comments per week", graphs_html)
        self.assertIn("Avg PR duration per user", graphs_html)
        self.assertIn("Avg PR duration per week", graphs_html)
        self.assertIn("Job duration: build", graphs_html)
        self.assertIn("Job duration: test", graphs_html)
        self.assertIn("Avg job duration per job name", graphs_html)
        self.assertIn("<svg xmlns=", graphs_html)
        self.assertNotIn("<img ", graphs_html)
        self.assertIn("svg{display:block;height:auto;}", graphs_html)
        self.assertIn("section{margin:24px 0;overflow-x:auto;}", graphs_html)
        self.assertNotIn("svg{max-width:100%;height:auto;}", graphs_html)
        self.assertIn("Avg job duration per job name", avg_dur_per_job_svg)
        self.assertIn("build", avg_dur_per_job_svg)
        self.assertIn("Job name", avg_dur_per_job_svg)
        self.assertIn("Duration (minutes)", avg_dur_per_job_svg)
        self.assertNotIn(f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} ', avg_dur_per_job_svg)
        self.assertIn("Job duration: build", job_build_svg)
        self.assertIn("Duration (minutes)", job_build_svg)
        self.assertIn("Completion date", job_build_svg)
        self.assertIn("prefers-color-scheme: dark", job_build_svg)
        self.assertEqual(
            set(xlsx_sheets),
            {
                "PR activity",
                "PRs per week",
                "Comments per PR",
                "Comments per week",
                "Comments distribution",
                "Avg PR duration",
                "Job durations",
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
        # Both PRs have total_comments=3, so the distribution is {3: 2}
        self.assertEqual(
            xlsx_sheets["Comments distribution"].to_dict(orient="records"),
            [{"total_comments": 3, "pull_requests": 2}],
        )
        # Only PR #1 (alice, merged 2026-01-03 in W01) contributes; duration = 2 days = 48h
        self.assertEqual(
            xlsx_sheets["Avg PR duration"].to_dict(orient="records"),
            [
                {
                    "author": "alice",
                    "week": "2026-W01",
                    "pr_count": 1,
                    "avg_duration_hours": 48.0,
                }
            ],
        )
        job_dur_records = xlsx_sheets["Job durations"].to_dict(orient="records")
        self.assertEqual(len(job_dur_records), 2)
        self.assertEqual(job_dur_records[0]["job_name"], "build")
        self.assertEqual(job_dur_records[0]["duration_seconds"], 120)
        self.assertEqual(job_dur_records[0]["pr_number"], 1)
        self.assertEqual(job_dur_records[1]["job_name"], "test")
        self.assertEqual(job_dur_records[1]["duration_seconds"], 60)

    def test_build_avg_duration_per_user_week_rows(self) -> None:
        # ISO weeks: W01=Dec29-Jan4, W02=Jan5-Jan11, W03=Jan12-Jan18
        rows = [
            {
                "author": "alice",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-03T00:00:00Z",  # 2 days = 48h, merged in W01
            },
            {
                "author": "alice",
                "created_at": "2026-01-05T00:00:00Z",
                "merged_at": "2026-01-07T00:00:00Z",  # 2 days = 48h, merged in W02
            },
            {
                "author": "bob",
                "created_at": "2026-01-12T00:00:00Z",
                "merged_at": "2026-01-13T00:00:00Z",  # 1 day = 24h, merged in W03
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
        self.assertEqual(alice_w01["avg_duration_hours"], 48.0)
        self.assertEqual(alice_w02["pr_count"], 1)
        self.assertEqual(alice_w02["avg_duration_hours"], 48.0)
        self.assertEqual(bob_row["week"], "2026-W03")
        self.assertEqual(bob_row["pr_count"], 1)
        self.assertEqual(bob_row["avg_duration_hours"], 24.0)

    def test_build_avg_duration_per_user_rows_sorted_by_decreasing_duration(self) -> None:
        rows = [
            {
                "author": "bob",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-03T00:00:00Z",  # 48h
            },
            {
                "author": "alice",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",  # 24h
            },
            {
                "author": "carol",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-03T00:00:00Z",  # 48h
            },
            {
                "author": "dan",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "",  # not merged, excluded
            },
        ]
        self.assertEqual(
            _build_avg_duration_per_user_rows(rows),
            [
                {"author": "bob", "avg_duration_hours": 48.0, "pr_count": 1},
                {"author": "carol", "avg_duration_hours": 48.0, "pr_count": 1},
                {"author": "alice", "avg_duration_hours": 24.0, "pr_count": 1},
            ],
        )

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
                "total_job_duration_hours": round(180 / 3600, 2),
                "successful_job_durations": [],
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
            save_bar_graph(path, values, "Long labels")
            svg = path.read_text(encoding="utf-8")
        expected_left = max(60, 20 + len("copilot_commands") * SVG_LABEL_CHAR_WIDTH)
        self.assertIn(f'x1="{expected_left - 20}"', svg)
        self.assertIn(f'<rect x="{expected_left}"', svg)
        self.assertIn(f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} ', svg)
        self.assertIn("manual_comments", svg)
        self.assertIn("copilot_commands", svg)

    def test_save_bar_graph_renders_bar_labels(self) -> None:
        values = {"alice": 48.0, "bob": 24.0}
        bar_labels = {"alice": "n=3", "bob": "n=1"}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "graph.svg"
            save_bar_graph(path, values, "Avg duration", bar_labels=bar_labels)
            svg = path.read_text(encoding="utf-8")
        self.assertIn("n=3", svg)
        self.assertIn("n=1", svg)
        # bar_labels are rendered in a smaller, muted font
        self.assertIn('fill="#888" font-size="11"', svg)

    def test_save_bar_graph_horizontal_renders_author_labels_without_rotation(self) -> None:
        values = {"alice": 48.0, "bob": 24.0}
        bar_labels = {"alice": "n=3", "bob": "n=1"}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "graph.svg"
            save_bar_graph(
                path,
                values,
                "Avg duration",
                x_axis_label="Duration (hours)",
                y_axis_label="Author",
                bar_labels=bar_labels,
                horizontal=True,
            )
            svg = path.read_text(encoding="utf-8")
        self.assertIn("alice", svg)
        self.assertIn("bob", svg)
        self.assertIn('text-anchor="end"', svg)
        self.assertNotIn(f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} ', svg)

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
            patch("moa.commands.pr_stats._collect_pr_job_info", return_value=(30, [])),
        ):
            rows = build_pr_activity_rows("o", "r", since="2026-01-01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], 13)

    def test_build_pr_activity_rows_accepts_relative_since(self) -> None:
        pulls = [
            {
                "number": 12,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "Old",
                "created_at": "2026-05-18T12:00:00Z",
                "merged_at": None,
                "closed_at": "2026-05-18T13:00:00Z",
                "html_url": "https://github.com/o/r/pull/12",
            },
            {
                "number": 13,
                "state": "closed",
                "user": {"login": "bob"},
                "title": "New",
                "created_at": "2026-05-20T18:00:00Z",
                "merged_at": "2026-05-20T19:00:00Z",
                "closed_at": "2026-05-20T19:00:00Z",
                "html_url": "https://github.com/o/r/pull/13",
            },
        ]
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats", return_value=(1, 0)),
            patch("moa.commands.pr_stats._collect_pr_job_info", return_value=(30, [])),
            patch("moa.commands.pr_stats.datetime") as mock_datetime,
        ):
            mock_datetime.now.return_value = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            rows = build_pr_activity_rows("o", "r", since="-1 day")
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
                "total_job_duration_hours": round(456 / 3600, 2),
                "successful_job_durations": [],
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
        self.assertEqual(rows[0]["total_job_duration_hours"], 0.13)
        mocked_collect.assert_not_called()

    def test_build_pr_activity_rows_batches_comment_queries_for_many_prs(self) -> None:
        pulls = [
            {
                "number": 1,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "PR 1",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
            },
            {
                "number": 2,
                "state": "closed",
                "user": {"login": "bob"},
                "title": "PR 2",
                "created_at": "2026-01-03T00:00:00Z",
                "merged_at": "2026-01-04T00:00:00Z",
                "closed_at": "2026-01-04T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/2",
                "head": {"sha": "def"},
            },
        ]
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch(
                "moa.commands.pr_stats._collect_pr_comment_stats_batch",
                return_value={1: (1, 0), 2: (2, 0)},
            ) as mocked_batch,
            patch(
                "moa.commands.pr_stats._collect_pr_comment_stats",
            ),
            patch("moa.commands.pr_stats._collect_pr_job_info", return_value=(0, [])),
        ):
            rows = build_pr_activity_rows("o", "r")
        mocked_batch.assert_called_once_with(
            owner="o",
            repo="r",
            pull_numbers=[1, 2],
            token=None,
            api_url="https://api.github.com",
            verbose=False,
        )
        self.assertEqual([row["number"] for row in rows], [1, 2])
        self.assertEqual([row["manual_comments"] for row in rows], [1, 2])

    def test_build_pr_activity_rows_batches_job_queries_for_many_prs(self) -> None:
        pulls = [
            {
                "number": 1,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "PR 1",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
            },
            {
                "number": 2,
                "state": "closed",
                "user": {"login": "bob"},
                "title": "PR 2",
                "created_at": "2026-01-03T00:00:00Z",
                "merged_at": "2026-01-04T00:00:00Z",
                "closed_at": "2026-01-04T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/2",
                "head": {"sha": "def"},
            },
        ]
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats_batch", return_value={}),
            patch(
                "moa.commands.pr_stats._collect_pr_job_info_batch",
                return_value={1: (0, []), 2: (0, [])},
            ) as mocked_batch,
            patch("moa.commands.pr_stats._collect_pr_job_info") as mocked_single,
        ):
            rows = build_pr_activity_rows("o", "r")
        mocked_batch.assert_called_once_with(
            owner="o",
            repo="r",
            pull_head_shas={1: "abc", 2: "def"},
            token=None,
            api_url="https://api.github.com",
            verbose=False,
        )
        mocked_single.assert_not_called()
        self.assertEqual([row["number"] for row in rows], [1, 2])

    def test_build_pr_activity_rows_falls_back_when_batch_job_stats_missing_pr(self) -> None:
        pulls = [
            {
                "number": 1,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "PR 1",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
            },
            {
                "number": 2,
                "state": "closed",
                "user": {"login": "bob"},
                "title": "PR 2",
                "created_at": "2026-01-03T00:00:00Z",
                "merged_at": "2026-01-04T00:00:00Z",
                "closed_at": "2026-01-04T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/2",
                "head": {"sha": "def"},
            },
        ]
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats_batch", return_value={}),
            patch("moa.commands.pr_stats._collect_pr_job_info_batch", return_value={1: (60, [])}),
            patch(
                "moa.commands.pr_stats._collect_pr_job_info", return_value=(120, [])
            ) as mocked_single,
        ):
            rows = build_pr_activity_rows("o", "r")
        mocked_single.assert_called_once_with(
            owner="o",
            repo="r",
            pull_number=2,
            head_sha="def",
            token=None,
            api_url="https://api.github.com",
        )
        by_number = {row["number"]: row for row in rows}
        self.assertEqual(by_number[1]["total_job_duration_hours"], round(60 / 3600, 2))
        self.assertEqual(by_number[2]["total_job_duration_hours"], round(120 / 3600, 2))

    def test_build_pr_activity_rows_warns_and_keeps_partial_data_on_http_error(self) -> None:
        pulls = [
            {
                "number": 1,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "PR 1",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
            },
            {
                "number": 2,
                "state": "closed",
                "user": {"login": "bob"},
                "title": "PR 2",
                "created_at": "2026-01-03T00:00:00Z",
                "merged_at": "2026-01-04T00:00:00Z",
                "closed_at": "2026-01-04T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/2",
                "head": {"sha": "def"},
            },
        ]
        err = StringIO()

        def fake_collect_comments(
            owner: str,
            repo: str,
            pull_number: int,
            token: str | None = None,
            api_url: str = "https://api.github.com",
            verbose: bool = False,
        ) -> tuple[int, int]:
            del owner, repo, token, api_url, verbose
            if pull_number == 2:
                raise HTTPError("https://api.github.com", 403, "rate limited", {}, None)
            return 1, 0

        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch(
                "moa.commands.pr_stats._collect_pr_comment_stats",
                side_effect=fake_collect_comments,
            ),
            patch("moa.commands.pr_stats._collect_pr_job_info", return_value=(0, [])),
            patch("sys.stderr", err),
        ):
            rows = build_pr_activity_rows("o", "r")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], 1)
        self.assertIn(
            "pr-stats: warning: failed to collect stats for PR #2 (HTTPError 403); "
            "continuing with partial data.",
            err.getvalue(),
        )

    def test_save_pr_activity_report_saves_cached_data_on_http_error(self) -> None:
        """When build_pr_activity_rows raises HTTPError, cached data is saved to disk."""
        cached_row = {
            "number": 5,
            "author": "alice",
            "title": "Cached PR",
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": "2026-01-02T00:00:00Z",
            "closed_at": "2026-01-02T00:00:00Z",
            "status": "merged",
            "manual_comments": 2,
            "copilot_commands": 0,
            "total_job_duration_hours": 0.0,
            "successful_job_durations": [],
            "html_url": "https://github.com/o/r/pull/5",
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = pathlib.Path(tmp) / "report_cache.json"
            cache_path.write_text(json.dumps({"rows": {"5": cached_row}}), encoding="utf-8")
            with self.assertRaises(HTTPError):
                with patch(
                    "moa.commands.pr_stats.build_pr_activity_rows",
                    side_effect=HTTPError(
                        "https://api.github.com", 500, "Server Error", {}, None
                    ),
                ):
                    save_pr_activity_report(
                        "o",
                        "r",
                        output_dir=tmp,
                        prefix="report",
                        cache_file=str(cache_path),
                    )
            csv_path = pathlib.Path(tmp) / "report.csv"
            self.assertTrue(csv_path.exists(), "CSV should be saved even on HTTPError")
            with csv_path.open("r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["number"], "5")
            saved_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertIn("5", saved_cache.get("rows", {}))

    def test_save_pr_activity_report_no_save_when_no_cached_data_on_http_error(self) -> None:
        """When build_pr_activity_rows raises HTTPError and cache is empty, no files written."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HTTPError):
                with patch(
                    "moa.commands.pr_stats.build_pr_activity_rows",
                    side_effect=HTTPError("https://api.github.com", 503, "Unavailable", {}, None),
                ):
                    save_pr_activity_report(
                        "o",
                        "r",
                        output_dir=tmp,
                        prefix="report",
                    )
            csv_path = pathlib.Path(tmp) / "report.csv"
            self.assertFalse(csv_path.exists(), "No CSV should be written when cache is empty")

    def test_save_pr_activity_report_no_save_on_repo_not_found(self) -> None:
        cached_row = {
            "number": 5,
            "author": "alice",
            "title": "Cached PR",
            "created_at": "2026-01-01T00:00:00Z",
            "merged_at": "2026-01-02T00:00:00Z",
            "closed_at": "2026-01-02T00:00:00Z",
            "status": "merged",
            "manual_comments": 2,
            "copilot_commands": 0,
            "total_job_duration_hours": 0.0,
            "successful_job_durations": [],
            "html_url": "https://github.com/o/r/pull/5",
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = pathlib.Path(tmp) / "report_cache.json"
            cache_path.write_text(json.dumps({"rows": {"5": cached_row}}), encoding="utf-8")
            with self.assertRaises(HTTPError):
                with patch(
                    "moa.commands.pr_stats.build_pr_activity_rows",
                    side_effect=HTTPError("https://api.github.com", 404, "Not Found", {}, None),
                ):
                    save_pr_activity_report(
                        "o",
                        "missing",
                        output_dir=tmp,
                        prefix="report",
                        cache_file=str(cache_path),
                    )
            csv_path = pathlib.Path(tmp) / "report.csv"
            self.assertFalse(csv_path.exists(), "No CSV should be written when repo is missing")
            saved_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_cache, {"rows": {"5": cached_row}})

    def test_default_since_format_and_value(self) -> None:
        result = _default_since()
        self.assertRegex(result, r"^\d{4}-\d{2}-\d{2}$")
        # Must be roughly 6 months before today
        today = datetime.now(timezone.utc)
        result_dt = datetime.fromisoformat(result)
        diff_days = (today.replace(tzinfo=None) - result_dt).days
        self.assertGreaterEqual(diff_days, 180)
        self.assertLessEqual(diff_days, 185)

    def test_parse_since_datetime_supports_requested_relative_day_forms(self) -> None:
        now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
        cases = {
            "-1 day": datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
            "-2 days": datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc),
            "-3d": datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
            "-4 d": datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc),
            "-11 day": datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(_parse_since_datetime(value, now=now), expected)

    def test_parse_since_datetime_raises_clear_error_on_invalid_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid --since value"):
            _parse_since_datetime("yesterday-ish")

    def test_main_prints_output_paths(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            graph_dir = pathlib.Path(tmp) / "graphs_repo"
            graph_dir.mkdir()
            fake_paths = {
                "csv": tempfile.NamedTemporaryFile(dir=tmp, suffix=".csv", delete=False).name,
                "xlsx": tempfile.NamedTemporaryFile(dir=tmp, suffix=".xlsx", delete=False).name,
                "status_svg": tempfile.NamedTemporaryFile(
                    dir=graph_dir, suffix=".svg", delete=False
                ).name,
                "comments_svg": tempfile.NamedTemporaryFile(
                    dir=graph_dir, suffix=".svg", delete=False
                ).name,
                "graphs_html": tempfile.NamedTemporaryFile(
                    dir=graph_dir, suffix=".html", delete=False
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
        self.assertIn("/graphs_repo/", out.getvalue())
        self.assertIn(".html", out.getvalue())

    def test_build_parser_help_mentions_relative_since_values(self) -> None:
        from moa.commands.pr_stats import _build_parser

        help_text = _build_parser().format_help()
        self.assertIn("relative values like '-1", help_text)
        self.assertIn("or '-3d'). Default: 6 months ago.", help_text)

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

    def test_collect_pr_job_duration_hours(self) -> None:
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
                    "conclusion": "success",
                    "name": "build",
                },
                {
                    "started_at": "2026-01-01T00:02:00Z",
                    "completed_at": "2026-01-01T00:03:00Z",
                    "conclusion": "success",
                    "name": "test",
                },
            ]
        }
        with patch(
            "moa.commands.pr_stats._fetch_json",
            side_effect=[runs_payload, jobs_run_101],
        ):
            duration = _collect_pr_job_duration_hours("o", "r", 22, "abc")
        self.assertEqual(duration, round(150 / 3600, 2))

    def test_collect_pr_job_info_separates_successful_jobs(self) -> None:
        runs_payload = {
            "workflow_runs": [
                {"id": 101, "pull_requests": [{"number": 22}]},
            ]
        }
        jobs_run_101 = {
            "jobs": [
                {
                    "name": "build",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:01:30Z",
                    "conclusion": "success",
                },
                {
                    "name": "lint",
                    "started_at": "2026-01-01T00:02:00Z",
                    "completed_at": "2026-01-01T00:03:00Z",
                    "conclusion": "failure",
                },
            ]
        }
        with patch(
            "moa.commands.pr_stats._fetch_json",
            side_effect=[runs_payload, jobs_run_101],
        ):
            total, successful = _collect_pr_job_info("o", "r", 22, "abc")
        # Total includes both jobs regardless of conclusion
        self.assertEqual(total, 150)
        # Only the successful job appears in the per-job list
        self.assertEqual(len(successful), 1)
        self.assertEqual(successful[0]["job_name"], "build")
        self.assertEqual(successful[0]["duration_seconds"], 90)

    def test_collect_pr_job_info_batch(self) -> None:
        runs_payload = {
            "workflow_runs": [
                {"id": 101, "head_sha": "sha1", "pull_requests": [{"number": 22}]},
                {"id": 102, "head_sha": "sha2", "pull_requests": [{"number": 23}]},
            ]
        }
        jobs_run_101 = [
            {
                "name": "build",
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:01:30Z",
                "conclusion": "success",
            }
        ]
        jobs_run_102 = [
            {
                "name": "test",
                "started_at": "2026-01-01T00:02:00Z",
                "completed_at": "2026-01-01T00:03:00Z",
                "conclusion": "failure",
            }
        ]
        job_map = {101: jobs_run_101, 102: jobs_run_102}
        with (
            patch("moa.commands.pr_stats._fetch_json", return_value=runs_payload),
            patch(
                "moa.commands.pr_stats._fetch_workflow_run_jobs",
                side_effect=lambda o, r, run_id, tok=None, api=None: job_map[run_id],
            ),
        ):
            results = _collect_pr_job_info_batch(
                "o", "r", {22: "sha1", 23: "sha2", 24: "missing"}, token="abc"
            )
        self.assertEqual(results[22][0], 90)
        self.assertEqual(results[22][1][0]["job_name"], "build")
        self.assertEqual(results[23][0], 60)
        self.assertEqual(results[23][1], [])
        self.assertNotIn(24, results)

    def test_collect_pr_job_info_batch_verbose(self) -> None:
        runs_payload = {
            "workflow_runs": [
                {"id": 101, "head_sha": "sha1", "pull_requests": [{"number": 22}]},
            ]
        }
        jobs_run_101 = [
            {
                "name": "build",
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:01:30Z",
                "conclusion": "success",
            }
        ]
        import io

        buf = io.StringIO()
        with (
            patch("moa.commands.pr_stats._fetch_json", return_value=runs_payload),
            patch(
                "moa.commands.pr_stats._fetch_workflow_run_jobs",
                return_value=jobs_run_101,
            ),
            patch("sys.stderr", buf),
        ):
            results = _collect_pr_job_info_batch(
                "o", "r", {22: "sha1"}, token="abc", verbose=True
            )
        output = buf.getvalue()
        self.assertIn("fetching workflow runs page 1", output)
        self.assertIn("collecting job info for PR #22", output)
        self.assertEqual(results[22][0], 90)

    def test_collect_pr_comment_stats_verbose(self) -> None:
        import io

        buf = io.StringIO()
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=[]),
            patch("sys.stderr", buf),
        ):
            manual, copilot = _collect_pr_comment_stats("o", "r", 42, verbose=True)
        output = buf.getvalue()
        self.assertIn("fetching comment stats for PR #42", output)
        self.assertEqual(manual, 0)
        self.assertEqual(copilot, 0)

    def test_collect_pr_comment_stats_batch_verbose(self) -> None:
        import io

        buf = io.StringIO()
        with (
            patch(
                "moa.commands.pr_stats._fetch_graphql_json",
                side_effect=OSError("network"),
            ),
            patch(
                "moa.commands.pr_stats._collect_pr_comment_stats",
                return_value=(1, 0),
            ) as mocked_fallback,
            patch("sys.stderr", buf),
        ):
            results = _collect_pr_comment_stats_batch("o", "r", [5, 6], verbose=True)
        output = buf.getvalue()
        self.assertIn("fetching comment stats batch 1/1", output)
        # Fallback calls pass verbose=True through to _collect_pr_comment_stats
        for call in mocked_fallback.call_args_list:
            self.assertTrue(call.kwargs.get("verbose") or (len(call.args) > 5 and call.args[5]))
        self.assertEqual(results[5], (1, 0))
        self.assertEqual(results[6], (1, 0))

    def test_collect_pr_job_info_verbose(self) -> None:
        import io

        buf = io.StringIO()
        runs_payload = {
            "workflow_runs": [
                {"id": 101, "head_sha": "sha1", "pull_requests": [{"number": 7}]},
            ]
        }
        jobs_run_101 = {
            "jobs": [
                {
                    "name": "build",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:01:00Z",
                    "conclusion": "success",
                }
            ]
        }
        with (
            patch("moa.commands.pr_stats._fetch_json", side_effect=[runs_payload, jobs_run_101]),
            patch("sys.stderr", buf),
        ):
            total, jobs = _collect_pr_job_info("o", "r", 7, "sha1", verbose=True)
        output = buf.getvalue()
        self.assertIn("collecting job info for PR #7", output)
        self.assertEqual(total, 60)
        self.assertEqual(len(jobs), 1)

    def test_collect_pr_job_info_warns_when_old_run_has_no_jobs(self) -> None:
        runs_payload = {
            "workflow_runs": [
                {
                    "id": 101,
                    "head_sha": "sha1",
                    "pull_requests": [{"number": 7}],
                    "created_at": "2026-01-01T00:00:00Z",
                },
            ]
        }
        jobs_run_101 = {"jobs": []}
        err = StringIO()
        with (
            patch("moa.commands.pr_stats._fetch_json", side_effect=[runs_payload, jobs_run_101]),
            patch("sys.stderr", err),
        ):
            total, jobs = _collect_pr_job_info("o", "r", 7, "sha1")
        self.assertEqual(total, 0)
        self.assertEqual(jobs, [])
        self.assertIn(
            "workflow run #101 for PR #7 is older than 2 weeks but has no jobs",
            err.getvalue(),
        )

    def test_collect_pr_job_info_batch_warns_when_old_run_has_no_jobs(self) -> None:
        runs_payload = {
            "workflow_runs": [
                {
                    "id": 101,
                    "head_sha": "sha1",
                    "pull_requests": [{"number": 22}],
                    "created_at": "2026-01-01T00:00:00Z",
                },
            ]
        }
        err = StringIO()
        with (
            patch("moa.commands.pr_stats._fetch_json", return_value=runs_payload),
            patch("moa.commands.pr_stats._fetch_workflow_run_jobs", return_value=[]),
            patch("sys.stderr", err),
        ):
            results = _collect_pr_job_info_batch("o", "r", {22: "sha1"}, token="abc")
        self.assertEqual(results[22], (0, []))
        self.assertIn(
            "workflow run #101 for PR #22 is older than 2 weeks but has no jobs",
            err.getvalue(),
        )

    def test_collect_pr_job_info_batch_early_exit(self) -> None:
        """Batch stops fetching pages once all target SHAs found and a page has no targets."""
        # page1 has PAGE_SIZE (100) runs: 1 target + 99 non-target to simulate a full page.
        page1 = {
            "workflow_runs": [
                {"id": 201, "head_sha": "sha1", "pull_requests": [{"number": 30}]},
            ]
            + [
                {"id": 300 + i, "head_sha": f"other{i}", "pull_requests": []}
                for i in range(99)  # PAGE_SIZE - 1 non-target runs to fill the page
            ]
        }
        page2 = {
            "workflow_runs": [
                {"id": 401 + i, "head_sha": f"old{i}", "pull_requests": []} for i in range(100)
            ]
        }
        jobs_run_201 = [
            {
                "name": "build",
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:01:00Z",
                "conclusion": "success",
            }
        ]
        pages = [page1, page2]
        fetch_json_calls: list[str] = []

        def fake_fetch_json(url: str, token: str | None = None) -> dict:
            fetch_json_calls.append(url)
            page_index = len(fetch_json_calls) - 1
            if page_index < len(pages):
                return pages[page_index]
            return {"workflow_runs": []}

        with (
            patch("moa.commands.pr_stats._fetch_json", side_effect=fake_fetch_json),
            patch(
                "moa.commands.pr_stats._fetch_workflow_run_jobs",
                return_value=jobs_run_201,
            ),
        ):
            results = _collect_pr_job_info_batch("o", "r", {30: "sha1"})
        # Two pages were fetched: page1 (found sha1) + page2 (no target → exit).
        self.assertEqual(len(fetch_json_calls), 2)
        self.assertEqual(results[30][0], 60)

    def test_build_job_duration_sheet_rows(self) -> None:
        pr_rows = [
            {
                "number": 1,
                "successful_job_durations": [
                    {
                        "job_name": "test",
                        "completed_at": "2026-01-03T01:05:00Z",
                        "duration_seconds": 60,
                    },
                    {
                        "job_name": "build",
                        "completed_at": "2026-01-03T01:00:00Z",
                        "duration_seconds": 120,
                    },
                ],
            },
            {
                "number": 2,
                "successful_job_durations": [],
            },
            {
                "number": 3,
                # no successful_job_durations key → treated as empty
            },
        ]
        result = _build_job_duration_sheet_rows(pr_rows)
        # Sorted by (job_name, completed_at): build < test
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["job_name"], "build")
        self.assertEqual(result[0]["duration_seconds"], 120)
        self.assertEqual(result[0]["pr_number"], 1)
        self.assertEqual(result[1]["job_name"], "test")
        self.assertEqual(result[1]["duration_seconds"], 60)

    def test_compute_moving_average(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = compute_moving_average(values, window=3)
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])
        self.assertAlmostEqual(result[2], 2.0)
        self.assertAlmostEqual(result[3], 3.0)
        self.assertAlmostEqual(result[4], 4.0)

    def test_compute_moving_average_window_larger_than_series(self) -> None:
        values = [10.0, 20.0]
        result = compute_moving_average(values, window=5)
        self.assertIsNone(result[0])
        self.assertIsNone(result[1])

    def test_save_job_duration_line_graph_creates_svg(self) -> None:
        series = [
            {"completed_at": "2026-01-01T01:00:00Z", "duration_seconds": 90},
            {"completed_at": "2026-01-02T01:00:00Z", "duration_seconds": 120},
            {"completed_at": "2026-01-03T01:00:00Z", "duration_seconds": 60},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "job_dur.svg"
            save_job_duration_line_graph(path, series, "Job duration: build")
            svg = path.read_text(encoding="utf-8")
        self.assertIn("Job duration: build", svg)
        self.assertIn("prefers-color-scheme: dark", svg)
        self.assertIn("<polyline", svg)
        self.assertIn("duration", svg)
        self.assertIn("Duration (minutes)", svg)
        self.assertIn(">1.5</text>", svg)
        self.assertIn("Completion date", svg)
        self.assertIn(f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} ', svg)

    def test_save_job_duration_line_graph_empty_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "job_dur_empty.svg"
            save_job_duration_line_graph(path, [], "Empty job")
            svg = path.read_text(encoding="utf-8")
        self.assertIn("No data", svg)
        self.assertNotIn("<polyline", svg)

    def test_save_job_duration_line_graph_shows_moving_average(self) -> None:
        # With >= 10 data points the moving average line should appear
        series = [
            {"completed_at": f"2026-01-{i + 1:02d}T00:00:00Z", "duration_seconds": 60 + i * 10}
            for i in range(12)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "job_dur_avg.svg"
            save_job_duration_line_graph(path, series, "Build over time")
            svg = path.read_text(encoding="utf-8")
        # Moving average line uses stroke-dasharray
        self.assertIn("stroke-dasharray", svg)
        self.assertIn("avg-10", svg)
        self.assertIn(">2.8</text>", svg)

    def test_build_avg_duration_per_job_rows_basic(self) -> None:
        rows = [
            {
                "number": 1,
                "successful_job_durations": [
                    {
                        "job_name": "build",
                        "completed_at": "2026-01-01T00:00:00Z",
                        "duration_seconds": 120,
                    },
                    {
                        "job_name": "test",
                        "completed_at": "2026-01-01T00:00:00Z",
                        "duration_seconds": 60,
                    },
                ],
            },
            {
                "number": 2,
                "successful_job_durations": [
                    {
                        "job_name": "build",
                        "completed_at": "2026-01-02T00:00:00Z",
                        "duration_seconds": 180,
                    },
                ],
            },
        ]
        result = _build_avg_duration_per_job_rows(rows)
        # Should have two entries sorted by job name
        self.assertEqual(len(result), 2)
        build_row = next(r for r in result if r["job_name"] == "build")
        test_row = next(r for r in result if r["job_name"] == "test")
        # build: (120 + 180) / 2 / 60 = 2.5 minutes
        self.assertAlmostEqual(build_row["avg_duration_minutes"], 2.5)
        # test: 60 / 60 = 1.0 minute
        self.assertAlmostEqual(test_row["avg_duration_minutes"], 1.0)

    def test_build_avg_duration_per_job_rows_empty(self) -> None:
        result = _build_avg_duration_per_job_rows([])
        self.assertEqual(result, [])

    def test_save_pr_activity_report_includes_avg_duration_per_job_svg(self) -> None:
        pulls = [
            {
                "number": 1,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "PR 1",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
            }
        ]
        job_info = (
            1.0,
            [
                {
                    "job_name": "build",
                    "completed_at": "2026-01-02T00:00:00Z",
                    "duration_seconds": 90,
                }
            ],
        )
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats", return_value=(0, 0)),
            patch("moa.commands.pr_stats._collect_pr_job_info", return_value=job_info),
            patch("moa.commands.pr_stats._collect_pr_job_info_batch", return_value={1: job_info}),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                result = save_pr_activity_report("o", "r", tmp, "test")
                self.assertIn("avg_duration_per_job_svg", result)
                avg_job_svg = pathlib.Path(result["avg_duration_per_job_svg"])
                self.assertTrue(avg_job_svg.exists())
                svg = avg_job_svg.read_text(encoding="utf-8")
                self.assertIn("Avg job duration per job name", svg)
                self.assertIn("build", svg)
                self.assertIn("Duration (minutes)", svg)
                self.assertIn("Job name", svg)
                self.assertNotIn(f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} ', svg)
                # HTML report should include the final graph section
                html = pathlib.Path(result["graphs_html"]).read_text(encoding="utf-8")
                self.assertIn("Avg job duration per job name", html)
                self.assertIn("Duration (minutes)", html)
                self.assertIn("Job name", html)

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
            patch("moa.commands.pr_stats._load_token_cache", return_value={}),
        ):
            code = main(["-v", "owner", "repo"])
        self.assertEqual(code, 0)
        self.assertIn("pr-stats: collecting pull request data for owner/repo...", err.getvalue())
        self.assertIn("pr-stats: done.", err.getvalue())

    def test_main_verbose_flag_prints_env_token_origin(self) -> None:
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
            patch("moa.commands.pr_stats._load_token_cache", return_value={}),
            patch.dict(os.environ, {"GITHUB_TOKEN": "env_tok"}),
        ):
            code = main(["-v", "owner", "repo"])
        self.assertEqual(code, 0)
        self.assertIn("pr-stats: token source=GITHUB_TOKEN, type=explicit.", err.getvalue())

    def test_main_uses_classic_cached_token(self) -> None:
        out = StringIO()
        fake_paths = {
            "csv": "/tmp/a.csv",
            "xlsx": "/tmp/a.xlsx",
            "status_svg": "/tmp/a_status.svg",
            "comments_svg": "/tmp/a_comments.svg",
            "cache": "/tmp/a_cache.json",
        }
        env_backup = os.environ.pop("GITHUB_TOKEN", None)
        try:
            with (
                patch(
                    "moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths
                ) as mocked,
                patch("sys.stdout", out),
                patch(
                    "moa.commands.pr_stats._load_token_cache",
                    return_value={"token": "classic_tok"},
                ),
            ):
                code = main(["owner", "repo"])
        finally:
            if env_backup is not None:
                os.environ["GITHUB_TOKEN"] = env_backup
        self.assertEqual(code, 0)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["token"], "classic_tok")

    def test_main_uses_project_cached_token(self) -> None:
        out = StringIO()
        fake_paths = {
            "csv": "/tmp/a.csv",
            "xlsx": "/tmp/a.xlsx",
            "status_svg": "/tmp/a_status.svg",
            "comments_svg": "/tmp/a_comments.svg",
            "cache": "/tmp/a_cache.json",
        }
        env_backup = os.environ.pop("GITHUB_TOKEN", None)
        try:
            with (
                patch(
                    "moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths
                ) as mocked,
                patch("sys.stdout", out),
                patch(
                    "moa.commands.pr_stats._load_token_cache",
                    return_value={
                        "token": "classic_tok",
                        "project_tokens": {"owner/repo": "project_tok"},
                    },
                ),
            ):
                code = main(["owner", "repo"])
        finally:
            if env_backup is not None:
                os.environ["GITHUB_TOKEN"] = env_backup
        self.assertEqual(code, 0)
        mocked.assert_called_once()
        _, kwargs = mocked.call_args
        self.assertEqual(kwargs["token"], "project_tok")

    def test_main_verbose_cached_token_origin(self) -> None:
        out = StringIO()
        err = StringIO()
        fake_paths = {
            "csv": "/tmp/a.csv",
            "xlsx": "/tmp/a.xlsx",
            "status_svg": "/tmp/a_status.svg",
            "comments_svg": "/tmp/a_comments.svg",
            "cache": "/tmp/a_cache.json",
        }
        env_backup = os.environ.pop("GITHUB_TOKEN", None)
        try:
            with (
                patch("moa.commands.pr_stats.save_pr_activity_report", return_value=fake_paths),
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch(
                    "moa.commands.pr_stats._load_token_cache",
                    return_value={"token": "classic_tok"},
                ),
            ):
                code = main(["-v", "owner", "repo"])
        finally:
            if env_backup is not None:
                os.environ["GITHUB_TOKEN"] = env_backup
        self.assertEqual(code, 0)
        self.assertIn("pr-stats: token source=", err.getvalue())
        self.assertIn("classic", err.getvalue())

    def test_print_progress_outputs_bar(self) -> None:
        buf = StringIO()
        _print_progress(1, 5, file=buf)
        output = buf.getvalue()
        self.assertIn("[", output)
        self.assertIn("]", output)
        self.assertIn("1/5", output)
        # Non-interactive streams should get line-based updates.
        self.assertTrue(output.endswith("\n"))

    def test_print_progress_tty_intermediate_step_ends_with_carriage_return(self) -> None:
        class _TTYBuffer(StringIO):
            def isatty(self) -> bool:
                return True

        buf = _TTYBuffer()
        _print_progress(1, 5, file=buf)
        output = buf.getvalue()
        self.assertIn("1/5", output)
        self.assertTrue(output.endswith("\r"))

    def test_print_progress_final_step_ends_with_newline(self) -> None:
        buf = StringIO()
        _print_progress(5, 5, file=buf)
        output = buf.getvalue()
        self.assertIn("5/5", output)
        self.assertTrue(output.endswith("\n"))

    def test_build_pr_activity_rows_verbose_prints_progress(self) -> None:
        pulls = [
            {
                "number": 1,
                "state": "closed",
                "user": {"login": "alice"},
                "title": "PR 1",
                "created_at": "2026-01-01T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": "2026-01-02T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
            },
            {
                "number": 2,
                "state": "closed",
                "user": {"login": "bob"},
                "title": "PR 2",
                "created_at": "2026-01-03T00:00:00Z",
                "merged_at": None,
                "closed_at": "2026-01-04T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/2",
                "head": {"sha": "def"},
            },
        ]
        err = StringIO()
        with (
            patch("moa.commands.pr_stats._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_stats._collect_pr_comment_stats", return_value=(0, 0)),
            patch("moa.commands.pr_stats._collect_pr_job_info", return_value=(0, [])),
            patch("sys.stderr", err),
        ):
            rows = build_pr_activity_rows("o", "r", verbose=True)
        self.assertEqual(len(rows), 2)
        progress_output = err.getvalue()
        self.assertIn("collecting comment stats for 2 uncached PRs", progress_output)
        self.assertIn("collecting workflow job stats for 2 uncached PRs", progress_output)
        # Progress bar should mention 2/2 PRs total
        self.assertIn("2/2", progress_output)
        # Final line ends with a newline
        self.assertTrue(progress_output.endswith("\n"))

    def test_build_pr_comments_distribution(self) -> None:
        rows = [
            {"manual_comments": 0, "copilot_commands": 0},
            {"manual_comments": 2, "copilot_commands": 1},
            {"manual_comments": 1, "copilot_commands": 0},
            {"manual_comments": 2, "copilot_commands": 1},
            {"manual_comments": 0, "copilot_commands": 0},
            {"manual_comments": 0, "copilot_commands": 0},
        ]
        result = _build_pr_comments_distribution(rows)
        # 3 PRs with 0 comments, 1 PR with 1 comment, 2 PRs with 3 comments
        self.assertEqual(result, {"0": 3, "1": 1, "3": 2})
        # Keys must be in ascending order
        self.assertEqual(list(result.keys()), ["0", "1", "3"])

    def test_build_pr_comments_distribution_empty(self) -> None:
        result = _build_pr_comments_distribution([])
        self.assertEqual(result, {})

    def test_main_gh_flag_fetches_token(self) -> None:
        out = StringIO()
        env_backup = {k: os.environ.pop(k) for k in ("GITHUB_TOKEN",) if k in os.environ}
        try:
            with (
                patch("moa.commands.pr_stats.save_pr_activity_report", return_value={}) as mocked,
                patch("sys.stdout", out),
                patch("moa.commands.pr_stats._load_token_cache", return_value={}),
                patch(
                    "moa.commands.pr_stats._fetch_token_from_gh_cli",
                    return_value="ghp_from_cli",
                ),
            ):
                code = main(["--gh", "owner", "repo"])
        finally:
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        self.assertEqual(mocked.call_args.kwargs["token"], "ghp_from_cli")

    def test_main_gh_and_token_are_mutually_exclusive(self) -> None:
        with (patch("moa.commands.pr_stats._load_token_cache", return_value={}),):
            with self.assertRaises(SystemExit) as ctx:
                main(["--gh", "--token", "explicit_tok", "owner", "repo"])
        self.assertEqual(ctx.exception.code, 2)
