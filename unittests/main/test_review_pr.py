from io import StringIO
from unittest.mock import patch

from moa.commands.review_pr import (
    DEFAULT_MODEL,
    _call_copilot_review,
    build_pull_request_review_markdown,
    main,
    review_pull_request,
)
from moa.ext_test_case import ExtTestCase


class TestReviewPR(ExtTestCase):
    def test_build_pull_request_review_markdown(self) -> None:
        pr = {
            "title": "Add feature",
            "state": "open",
            "user": {"login": "alice"},
            "html_url": "https://github.com/owner/repo/pull/12",
            "changed_files": 2,
            "additions": 6,
            "deletions": 1,
            "body": "This updates two files.",
        }
        files = [
            {"filename": "a.py", "additions": 5, "deletions": 0},
            {"filename": "b.py", "additions": 1, "deletions": 1},
        ]

        got = build_pull_request_review_markdown(pr, files)

        self.assertIn("# Pull Request Review", got)
        self.assertIn("- **Title:** Add feature", got)
        self.assertIn("- **Files changed:** 2", got)
        self.assertIn("- `a.py` (+5/-0)", got)
        self.assertIn("- `b.py` (+1/-1)", got)

    def test_main_prints_markdown_to_stdout(self) -> None:
        import os

        out = StringIO()
        # Remove GITHUB_TOKEN / GITHUB_API_URL so defaults are None / https://api.github.com
        env_overrides = {k: "" for k in ("GITHUB_TOKEN", "GITHUB_API_URL")}
        env_backup = {k: os.environ.pop(k) for k in list(env_overrides) if k in os.environ}
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
            ):
                code = main(["owner", "repo", "12"])
        finally:
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="owner",
            repo="repo",
            pull_request=12,
            token=None,
            api_url="https://api.github.com",
            copilot_review=False,
            model=DEFAULT_MODEL,
        )
        self.assertEqual(out.getvalue(), "# review\n")

    def test_main_uses_env_vars_automatically(self) -> None:
        import os

        out = StringIO()
        env_patch = {
            "GITHUB_TOKEN": "env_token",
            "GITHUB_API_URL": "https://github.example.com/api/v3",
        }
        env_backup = {k: os.environ.pop(k) for k in env_patch if k in os.environ}
        os.environ.update(env_patch)
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
            ):
                code = main(["owner", "repo", "12"])
        finally:
            for k in env_patch:
                os.environ.pop(k, None)
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="owner",
            repo="repo",
            pull_request=12,
            token="env_token",
            api_url="https://github.example.com/api/v3",
            copilot_review=False,
            model=DEFAULT_MODEL,
        )

    def test_call_copilot_review_returns_content(self) -> None:
        import json

        fake_response = {"choices": [{"message": {"content": "Looks good to me!"}}]}
        with patch("moa.commands.review_pr.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = lambda s: s
            mock_urlopen.return_value.__exit__ = lambda s, *a: False
            mock_urlopen.return_value.read = lambda: json.dumps(fake_response).encode()
            mock_urlopen.return_value.__iter__ = lambda s: iter([])
            # Patch json.load to return the fake response
            with patch("moa.commands.review_pr.json.load", return_value=fake_response):
                result = _call_copilot_review("## PR Summary", "mytoken")

        self.assertEqual(result, "Looks good to me!")

    def test_review_pull_request_with_copilot_review(self) -> None:
        pr_data = {
            "title": "Test PR",
            "state": "open",
            "user": {"login": "bob"},
            "html_url": "https://github.com/o/r/pull/1",
            "changed_files": 1,
            "additions": 2,
            "deletions": 0,
            "body": "A change.",
        }
        files_data: list[dict] = []
        with (
            patch("moa.commands.review_pr._fetch_json", return_value=pr_data),
            patch("moa.commands.review_pr._fetch_files", return_value=files_data),
            patch(
                "moa.commands.review_pr._call_copilot_review",
                return_value="AI feedback here.",
            ) as mock_ai,
        ):
            result = review_pull_request(
                owner="o",
                repo="r",
                pull_request=1,
                token="tok",
                copilot_review=True,
            )

        self.assertIn("## Copilot Review", result)
        self.assertIn("AI feedback here.", result)
        mock_ai.assert_called_once()

    def test_review_pull_request_copilot_requires_token(self) -> None:
        pr_data = {
            "title": "T",
            "state": "open",
            "user": {"login": "u"},
            "html_url": "",
            "changed_files": 0,
            "additions": 0,
            "deletions": 0,
            "body": "",
        }
        with (
            patch("moa.commands.review_pr._fetch_json", return_value=pr_data),
            patch("moa.commands.review_pr._fetch_files", return_value=[]),
        ):
            with self.assertRaisesRegex(ValueError, "token"):
                review_pull_request(
                    owner="o",
                    repo="r",
                    pull_request=1,
                    token=None,
                    copilot_review=True,
                )

    def test_main_copilot_review_flag(self) -> None:
        import os

        out = StringIO()
        env_backup = {
            k: os.environ.pop(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL") if k in os.environ
        }
        os.environ["GITHUB_TOKEN"] = "tok"
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review with AI",
                ) as mocked,
                patch("sys.stdout", out),
            ):
                code = main(["--copilot-review", "owner", "repo", "12"])
        finally:
            os.environ.pop("GITHUB_TOKEN", None)
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        call_kwargs = mocked.call_args.kwargs
        self.assertTrue(call_kwargs["copilot_review"])
        self.assertEqual(call_kwargs["model"], DEFAULT_MODEL)
        self.assertIn("# review with AI", out.getvalue())
