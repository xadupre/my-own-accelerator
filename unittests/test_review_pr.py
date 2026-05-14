import runpy
from io import StringIO
from unittest.mock import patch

from my_own_accelerator.ext_test_case import ExtTestCase
from my_own_accelerator.review_pr import build_pull_request_review_markdown, main


class TestReviewPR(ExtTestCase):
    def test_package_main_executes_review_pr_main(self) -> None:
        with patch("my_own_accelerator.review_pr.main", return_value=0) as mocked:
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_module("my_own_accelerator", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with()

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
        out = StringIO()
        with (
            patch(
                "my_own_accelerator.review_pr.review_pull_request", return_value="# review"
            ) as mocked,
            patch("sys.stdout", out),
        ):
            code = main(["owner", "repo", "12"])

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="owner",
            repo="repo",
            pull_request=12,
            token=None,
            api_url="https://api.github.com",
        )
        self.assertEqual(out.getvalue(), "# review\n")
