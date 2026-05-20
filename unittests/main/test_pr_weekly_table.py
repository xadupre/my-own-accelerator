from io import StringIO
from unittest.mock import patch

from moa.commands.pr_weekly_table import (
    build_weekly_pr_markdown_table,
    build_weekly_pr_summary_rows,
    main,
)
from moa.ext_test_case import ExtTestCase


class TestPRWeeklyTable(ExtTestCase):
    def test_build_weekly_rows_reuses_cache_for_unchanged_pr(self) -> None:
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "Cached",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
            }
        ]
        cached = {
            "number": 1,
            "title": "Cached",
            "author": "alice",
            "created_at": "2026-05-19T00:00:00Z",
            "updated_at": "2026-05-20T00:00:00Z",
            "link": "https://github.com/o/r/pull/1",
            "head_sha": "abc",
            "needs_ci_approval": "no",
            "ci_status": "green",
            "reviewers": "bob",
        }
        with (
            patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_weekly_table._collect_required_contexts") as required,
            patch("moa.commands.pr_weekly_table._needs_ci_approval") as approval,
            patch("moa.commands.pr_weekly_table._collect_ci_status") as ci,
            patch("moa.commands.pr_weekly_table._collect_reviewers") as reviewers,
        ):
            rows = build_weekly_pr_summary_rows(
                "o",
                "r",
                since="2026-05-10T00:00:00Z",
                cached_rows={"1": cached},
            )
        self.assertEqual(rows, [cached])
        required.assert_not_called()
        approval.assert_not_called()
        ci.assert_not_called()
        reviewers.assert_not_called()

    def test_build_weekly_rows_refreshes_changed_pr(self) -> None:
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-21T00:00:00Z",
                "title": "Updated PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "requested_reviewers": [{"login": "bob"}],
            }
        ]
        cached = {
            "number": 1,
            "title": "Old",
            "author": "alice",
            "created_at": "2026-05-19T00:00:00Z",
            "updated_at": "2026-05-20T00:00:00Z",
            "link": "https://github.com/o/r/pull/1",
            "head_sha": "abc",
            "needs_ci_approval": "no",
            "ci_status": "green",
            "reviewers": "bob",
        }
        with (
            patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=["ci"]),
            patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=True),
            patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="failing: ci"),
            patch("moa.commands.pr_weekly_table._collect_reviewers", return_value="bob, carol"),
        ):
            rows = build_weekly_pr_summary_rows(
                "o",
                "r",
                since="2026-05-10T00:00:00Z",
                cached_rows={"1": cached},
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Updated PR")
        self.assertEqual(rows[0]["needs_ci_approval"], "yes")
        self.assertEqual(rows[0]["ci_status"], "failing: ci")
        self.assertEqual(rows[0]["reviewers"], "bob, carol")

    def test_build_markdown_table_with_copilot_columns(self) -> None:
        table = build_weekly_pr_markdown_table(
            [
                {
                    "title": "PR",
                    "author": "alice",
                    "created_at": "2026-05-19",
                    "updated_at": "2026-05-20",
                    "link": "https://github.com/o/r/pull/1",
                    "needs_ci_approval": "no",
                    "ci_status": "green",
                    "reviewers": "bob",
                    "copilot_summary": "Looks good",
                    "help_needed": "no",
                }
            ],
            copilot=True,
        )
        self.assertIn("Copilot summary", table)
        self.assertIn("Help needed", table)
        self.assertIn("Looks good", table)

    def test_build_weekly_rows_with_copilot_adds_summary_and_help(self) -> None:
        pulls = [
            {
                "number": 2,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "Needs help?",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/2",
                "head": {"sha": "def"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }
        ]
        with (
            patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
            patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=[]),
            patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=False),
            patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="green"),
            patch("moa.commands.pr_weekly_table._collect_reviewers", return_value=""),
            patch(
                "moa.commands.pr_weekly_table._call_copilot_summary",
                return_value=("Short summary", "yes"),
            ),
        ):
            rows = build_weekly_pr_summary_rows(
                "o",
                "r",
                token="tok",
                since="2026-05-10T00:00:00Z",
                copilot=True,
            )
        self.assertEqual(rows[0]["copilot_summary"], "Short summary")
        self.assertEqual(rows[0]["help_needed"], "yes")

    def test_main_copilot_requires_token(self) -> None:
        out = StringIO()
        err = StringIO()
        with (
            patch("sys.stdout", out),
            patch("sys.stderr", err),
            patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
            patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
        ):
            code = main(["owner", "repo", "--copilot"])
        self.assertEqual(code, 1)
        self.assertIn("required for --copilot", err.getvalue())
