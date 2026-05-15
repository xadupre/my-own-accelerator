import runpy
from io import StringIO
from unittest.mock import patch

from moa.ext_test_case import ExtTestCase


class TestMain(ExtTestCase):
    def test_package_main_executes_review_pr_main(self) -> None:
        with (
            patch("sys.argv", ["-m"]),
            patch("moa.commands.review_pr.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with([])

    def test_package_main_routes_review_local(self) -> None:
        with (
            patch("sys.argv", ["-m", "review-local", "README.md"]),
            patch("moa.commands.review_local.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with(["README.md"])

    def test_package_main_routes_explicit_review_pr(self) -> None:
        with (
            patch("sys.argv", ["-m", "review-pr", "owner", "repo", "1"]),
            patch("moa.commands.review_pr.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with(["owner", "repo", "1"])

    def test_package_main_help(self) -> None:
        for flag in ("-h", "--help"):
            out = StringIO()
            with (
                patch("sys.argv", ["-m", flag]),
                patch("sys.stdout", out),
                self.assertRaises(SystemExit) as ctx,
            ):
                runpy.run_module("moa", run_name="__main__")
            self.assertEqual(ctx.exception.code, 0)
            self.assertIn("review-pr", out.getvalue())
            self.assertIn("review-local", out.getvalue())
