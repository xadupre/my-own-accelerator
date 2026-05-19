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
        mocked.assert_called_once_with([], prog="python -m moa review-pr")

    def test_package_main_routes_review_local(self) -> None:
        with (
            patch("sys.argv", ["-m", "review-local", "README.md"]),
            patch("moa.commands.review_local.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with(["README.md"], prog="python -m moa review-local")

    def test_package_main_routes_explicit_review_pr(self) -> None:
        with (
            patch("sys.argv", ["-m", "review-pr", "owner", "repo", "1"]),
            patch("moa.commands.review_pr.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with(["owner", "repo", "1"], prog="python -m moa review-pr")

    def test_package_main_routes_pr_stats(self) -> None:
        with (
            patch("sys.argv", ["-m", "pr-stats", "owner", "repo"]),
            patch("moa.commands.pr_stats.main", return_value=0) as mocked,
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        mocked.assert_called_once_with(["owner", "repo"], prog="python -m moa pr-stats")

    def _assert_package_main_help(self, flag: str) -> None:
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
        self.assertIn("pr-stats", out.getvalue())
        self.assertIn("Review a GitHub pull request and print markdown.", out.getvalue())
        self.assertIn("Review local files and print markdown.", out.getvalue())
        self.assertIn("Build pull request activity reports", out.getvalue())
        self.assertIn("python -m moa", out.getvalue())

    def _assert_subcommand_help_uses_module_prog(self, command: str) -> None:
        out = StringIO()
        with (
            patch("sys.argv", ["-m", command, "--help"]),
            patch("sys.stdout", out),
            self.assertRaises(SystemExit) as ctx,
        ):
            runpy.run_module("moa", run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(f"usage: python -m moa {command}", out.getvalue())

    def test_package_main_help_short_flag(self) -> None:
        self._assert_package_main_help("-h")

    def test_package_main_help_long_flag(self) -> None:
        self._assert_package_main_help("--help")

    def test_subcommand_help_uses_module_prog_review_pr(self) -> None:
        self._assert_subcommand_help_uses_module_prog("review-pr")

    def test_subcommand_help_uses_module_prog_review_local(self) -> None:
        self._assert_subcommand_help_uses_module_prog("review-local")

    def test_subcommand_help_uses_module_prog_pr_stats(self) -> None:
        self._assert_subcommand_help_uses_module_prog("pr-stats")
