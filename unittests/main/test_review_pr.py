from io import StringIO
from unittest.mock import patch

from moa.commands.review_pr import build_pull_request_review_markdown, main
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
        )
