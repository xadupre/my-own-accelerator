import json
import pathlib
import tempfile
from datetime import datetime, timezone
from http.client import IncompleteRead
from io import StringIO
from unittest.mock import patch
from urllib.error import HTTPError

from moa.commands.pr_weekly_table import (
    _build_parser,
    _load_cache,
    _parse_since_datetime,
    build_weekly_pr_markdown_table,
    build_weekly_pr_summary_rows,
    main,
)
from moa.commands.review_token import CONFIG_FILE
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

    def test_build_weekly_rows_accepts_plain_date_since(self) -> None:
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
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
        ):
            rows = build_weekly_pr_summary_rows("o", "r", since="2026-05-10")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "PR")

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
                    "number": 1,
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
        self.assertIn("| # |", table)
        self.assertIn("[#1](https://github.com/o/r/pull/1)", table)

    def test_build_markdown_table_falls_back_to_raw_link_without_number(self) -> None:
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
                }
            ]
        )
        self.assertIn("https://github.com/o/r/pull/1", table)
        self.assertNotIn("[#", table)

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

    def test_build_weekly_rows_with_copilot_falls_back_on_http_error(self) -> None:
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
                side_effect=HTTPError(
                    "https://models.inference.ai.azure.com/chat/completions",
                    400,
                    "Bad Request",
                    {},
                    None,
                ),
            ),
        ):
            warnings = []
            rows = build_weekly_pr_summary_rows(
                "o",
                "r",
                token="tok",
                since="2026-05-10T00:00:00Z",
                copilot=True,
                warnings=warnings,
            )
        self.assertNotIn("copilot_summary", rows[0])
        self.assertNotIn("help_needed", rows[0])
        self.assertEqual(
            warnings,
            [
                "pr-weekly-table: warning: unable to generate Copilot summary "
                "for PR #2: HTTP 400: Bad Request."
            ],
        )

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

    def test_main_copilot_requires_project_token_not_classic_cached_token(self) -> None:
        out = StringIO()
        err = StringIO()
        with (
            patch("sys.stdout", out),
            patch("sys.stderr", err),
            patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
            patch(
                "moa.commands.pr_weekly_table._load_token_cache",
                return_value={"token": "classic_tok"},
            ),
        ):
            code = main(["owner", "repo", "--copilot"])
        self.assertEqual(code, 1)
        self.assertIn("required for --copilot", err.getvalue())

    def test_main_verbose_flag_prints_progress(self) -> None:
        out = StringIO()
        err = StringIO()
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
                patch("moa.commands.pr_weekly_table.DEFAULT_CACHE_DIR", tmp),
                patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
                patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
                patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=[]),
                patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=False),
                patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="green"),
                patch("moa.commands.pr_weekly_table._collect_reviewers", return_value=""),
            ):
                code = main(["owner", "repo", "--since", "2026-05-10T00:00:00Z", "--verbose"])
        self.assertEqual(code, 0)
        self.assertIn("pr-weekly-table: token source=none, type=none.", err.getvalue())
        self.assertIn("pr-weekly-table: cache file=", err.getvalue())
        self.assertIn("pr-weekly-table: output file=", err.getvalue())
        self.assertIn(
            "pr-weekly-table: collecting pull request data for owner/repo...",
            err.getvalue(),
        )
        self.assertIn("pr-weekly-table: done.", err.getvalue())

    def test_main_verbose_flag_prints_copilot_model(self) -> None:
        out = StringIO()
        err = StringIO()
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }
        ]

        def fake_call_copilot_summary(*args: object, **kwargs: object) -> tuple[str, str]:
            on_model_used = kwargs.get("on_model_used")
            self.assertTrue(callable(on_model_used))
            on_model_used("openai/gpt-4.1")
            return ("Summary", "no")

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
                patch("moa.commands.pr_weekly_table.DEFAULT_CACHE_DIR", tmp),
                patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
                patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
                patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=[]),
                patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=False),
                patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="green"),
                patch("moa.commands.pr_weekly_table._collect_reviewers", return_value=""),
                patch(
                    "moa.commands.pr_weekly_table._call_copilot_summary",
                    side_effect=fake_call_copilot_summary,
                ),
            ):
                code = main(
                    [
                        "owner",
                        "repo",
                        "--since",
                        "2026-05-10T00:00:00Z",
                        "--copilot",
                        "--token",
                        "tok",
                        "--verbose",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertIn("pr-weekly-table: copilot model=openai/gpt-4.1.", err.getvalue())

    def test_main_verbose_flag_prints_cached_project_token_origin(self) -> None:
        out = StringIO()
        err = StringIO()
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
                patch("moa.commands.pr_weekly_table.DEFAULT_CACHE_DIR", tmp),
                patch(
                    "moa.commands.pr_weekly_table._load_token_cache",
                    return_value={"project_tokens": {"owner/repo": "cached_tok"}},
                ),
                patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
                patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=[]),
                patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=False),
                patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="green"),
                patch("moa.commands.pr_weekly_table._collect_reviewers", return_value=""),
            ):
                code = main(["owner", "repo", "--since", "2026-05-10T00:00:00Z", "-v"])
        self.assertEqual(code, 0)
        self.assertIn(
            f"pr-weekly-table: token source={CONFIG_FILE} (owner/repo), type=fine-grained.",
            err.getvalue(),
        )

    def test_build_parser_lists_verbose_and_model_examples(self) -> None:
        help_text = _build_parser().format_help()
        self.assertIn("-v, --verbose", help_text)
        self.assertIn("relative values like '-1 day'", help_text)
        self.assertIn("--output-file OUTPUT_FILE", help_text)
        self.assertIn("When omitted, Copilot", help_text)
        self.assertIn("falls back to", help_text)
        self.assertIn("openai/gpt-4.1 if needed", help_text)
        self.assertIn("Any model available", help_text)
        self.assertIn("Models API is accepted", help_text)
        self.assertIn("openai/gpt-4.1", help_text)

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

    def test_main_writes_default_output_file(self) -> None:
        out = StringIO()
        err = StringIO()
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            expected_output = pathlib.Path(tmp) / "pr_weekly_repo.md"
            expected_cache = pathlib.Path(tmp) / "pr_weekly_repo_cache.json"
            with (
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
                patch("moa.commands.pr_weekly_table.DEFAULT_CACHE_DIR", tmp),
                patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
                patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
                patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=[]),
                patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=False),
                patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="green"),
                patch("moa.commands.pr_weekly_table._collect_reviewers", return_value=""),
            ):
                code = main(["owner", "repo", "--since", "2026-05-10T00:00:00Z", "--verbose"])
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue().strip(), str(expected_output))
            self.assertIn(f"pr-weekly-table: cache file={expected_cache}", err.getvalue())
            self.assertIn(f"pr-weekly-table: output file={expected_output}", err.getvalue())
            self.assertTrue(expected_output.exists())
            self.assertTrue(expected_cache.exists())
            output_text = expected_output.read_text(encoding="utf-8")
            self.assertIn("| Title | Author |", output_text)
            self.assertIn("[#1](https://github.com/o/r/pull/1)", output_text)
            cache_payload = json.loads(expected_cache.read_text(encoding="utf-8"))
            self.assertIn("1", cache_payload["rows"])

    def test_main_copilot_http_error_still_writes_output(self) -> None:
        out = StringIO()
        err = StringIO()
        pulls = [
            {
                "number": 1,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/1",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            expected_output = pathlib.Path(tmp) / "pr_weekly_repo.md"
            with (
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
                patch("moa.commands.pr_weekly_table.DEFAULT_CACHE_DIR", tmp),
                patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
                patch("moa.commands.pr_weekly_table._fetch_paginated", return_value=pulls),
                patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=[]),
                patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=False),
                patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="green"),
                patch("moa.commands.pr_weekly_table._collect_reviewers", return_value=""),
                patch(
                    "moa.commands.pr_weekly_table._call_copilot_summary",
                    side_effect=HTTPError(
                        "https://models.inference.ai.azure.com/chat/completions",
                        400,
                        "Bad Request",
                        {},
                        None,
                    ),
                ),
            ):
                code = main(
                    [
                        "owner",
                        "repo",
                        "--since",
                        "2026-05-10T00:00:00Z",
                        "--copilot",
                        "--token",
                        "tok",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue().strip(), str(expected_output))
            self.assertIn(
                "pr-weekly-table: warning: unable to generate Copilot summary",
                err.getvalue(),
            )
            self.assertIn("HTTP 400: Bad Request.", err.getvalue())
            output_text = expected_output.read_text(encoding="utf-8")
            self.assertNotIn("Copilot summary unavailable", output_text)
            cache_payload = json.loads(
                (pathlib.Path(tmp) / "pr_weekly_repo_cache.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("copilot_summary", cache_payload["rows"]["1"])
            self.assertNotIn("help_needed", cache_payload["rows"]["1"])

    def test_load_cache_removes_failed_copilot_fallback_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = pathlib.Path(tmp) / "cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "rows": {
                            "1": {
                                "number": 1,
                                "title": "PR",
                                "copilot_summary": "Copilot summary unavailable (HTTPError)",
                                "help_needed": "unknown",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            rows = _load_cache(cache_path)
        self.assertIn("1", rows)
        self.assertNotIn("copilot_summary", rows["1"])
        self.assertNotIn("help_needed", rows["1"])

    def test_build_weekly_rows_retries_paginated_incomplete_read(self) -> None:
        pulls = [
            {
                "number": 3,
                "created_at": "2026-05-19T00:00:00Z",
                "updated_at": "2026-05-20T00:00:00Z",
                "title": "Retry PR",
                "user": {"login": "alice"},
                "html_url": "https://github.com/o/r/pull/3",
                "head": {"sha": "ghi"},
                "base": {"ref": "main"},
                "requested_reviewers": [],
            }
        ]
        with (
            patch(
                "moa.commands.pr_weekly_table._fetch_paginated",
                side_effect=[IncompleteRead(b"partial", 10), pulls],
            ) as fetch_paginated,
            patch("moa.commands.pr_weekly_table._collect_required_contexts", return_value=[]),
            patch("moa.commands.pr_weekly_table._needs_ci_approval", return_value=False),
            patch("moa.commands.pr_weekly_table._collect_ci_status", return_value="green"),
            patch("moa.commands.pr_weekly_table._collect_reviewers", return_value=""),
        ):
            rows = build_weekly_pr_summary_rows(
                "o",
                "r",
                since="2026-05-10T00:00:00Z",
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Retry PR")
        self.assertEqual(fetch_paginated.call_count, 2)

    def test_main_handles_incomplete_read(self) -> None:
        out = StringIO()
        err = StringIO()
        with (
            patch("sys.stdout", out),
            patch("sys.stderr", err),
            patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
            patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
            patch(
                "moa.commands.pr_weekly_table._fetch_paginated",
                side_effect=IncompleteRead(b"partial", 10),
            ),
        ):
            code = main(["owner", "repo"])
        self.assertEqual(code, 1)
        self.assertIn("Unable to build weekly PR table (IncompleteRead)", err.getvalue())

    def test_main_gh_flag_fetches_token(self) -> None:
        out = StringIO()
        with (
            patch("sys.stdout", out),
            patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
            patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
            patch(
                "moa.commands.pr_weekly_table._fetch_token_from_gh_cli",
                return_value="ghp_from_cli",
            ),
            patch(
                "moa.commands.pr_weekly_table.build_weekly_pr_summary_rows",
                return_value=[],
            ) as mocked_build,
        ):
            with tempfile.TemporaryDirectory() as tmp:
                code = main(
                    [
                        "--gh",
                        "--output-file",
                        str(pathlib.Path(tmp) / "out.md"),
                        "--cache-file",
                        str(pathlib.Path(tmp) / "cache.json"),
                        "owner",
                        "repo",
                    ]
                )
        self.assertEqual(code, 0)
        self.assertEqual(mocked_build.call_args.kwargs["token"], "ghp_from_cli")

    def test_main_gh_and_token_are_mutually_exclusive(self) -> None:
        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": ""}),
            patch("moa.commands.pr_weekly_table._load_token_cache", return_value={}),
        ):
            with self.assertRaises(SystemExit) as ctx:
                main(["--gh", "--token", "explicit_tok", "owner", "repo"])
        self.assertEqual(ctx.exception.code, 2)
