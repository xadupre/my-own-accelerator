from unittest.mock import patch

from moa.commands.review_token import (
    CONFIG_FILE,
    _classify_token_by_prefix,
    _fetch_token_from_gh_cli,
    _resolve_token_origin,
)
from moa.ext_test_case import ExtTestCase


class TestReviewToken(ExtTestCase):
    def test_classify_token_by_prefix_classic(self) -> None:
        self.assertEqual(_classify_token_by_prefix("ghp_abc123"), "classic")

    def test_classify_token_by_prefix_fine_grained(self) -> None:
        self.assertEqual(_classify_token_by_prefix("github_pat_abc123"), "fine-grained")

    def test_classify_token_by_prefix_unknown(self) -> None:
        self.assertIsNone(_classify_token_by_prefix("random_tok"))
        self.assertIsNone(_classify_token_by_prefix("gho_xyz"))

    def test_resolve_token_origin_none(self) -> None:
        self.assertEqual(_resolve_token_origin([], None, None, None), ("none", "none"))
        self.assertEqual(_resolve_token_origin([], "", None, None), ("none", "none"))

    def test_resolve_token_origin_explicit_flag_classic_prefix(self) -> None:
        argv = ["--token", "ghp_abc", "owner", "repo"]
        self.assertEqual(
            _resolve_token_origin(argv, "ghp_abc", None, {}),
            ("--token", "classic"),
        )

    def test_resolve_token_origin_explicit_flag_fine_grained_prefix(self) -> None:
        argv = ["--token", "github_pat_abc", "owner", "repo"]
        self.assertEqual(
            _resolve_token_origin(argv, "github_pat_abc", None, {}),
            ("--token", "fine-grained"),
        )

    def test_resolve_token_origin_explicit_flag_unknown_prefix(self) -> None:
        argv = ["--token", "random_tok"]
        self.assertEqual(
            _resolve_token_origin(argv, "random_tok", None, {}),
            ("--token", "explicit"),
        )

    def test_resolve_token_origin_env_classic_prefix(self) -> None:
        self.assertEqual(
            _resolve_token_origin([], "ghp_env", "ghp_env", {}),
            ("GITHUB_TOKEN", "classic"),
        )

    def test_resolve_token_origin_env_fine_grained_prefix(self) -> None:
        self.assertEqual(
            _resolve_token_origin([], "github_pat_env", "github_pat_env", {}),
            ("GITHUB_TOKEN", "fine-grained"),
        )

    def test_resolve_token_origin_env_unknown_prefix(self) -> None:
        self.assertEqual(
            _resolve_token_origin([], "env_tok", "env_tok", {}),
            ("GITHUB_TOKEN", "explicit"),
        )

    def test_resolve_token_origin_project_cache(self) -> None:
        cache = {"project_tokens": {"o/r": "tok"}}
        self.assertEqual(
            _resolve_token_origin([], "tok", None, cache, "o", "r"),
            (f"{CONFIG_FILE} (o/r)", "fine-grained"),
        )

    def test_resolve_token_origin_classic_cache(self) -> None:
        cache = {"token": "tok"}
        self.assertEqual(
            _resolve_token_origin([], "tok", None, cache),
            (str(CONFIG_FILE), "classic"),
        )

    def test_fetch_token_from_gh_cli_success(self) -> None:
        import subprocess

        mock_result = subprocess.CompletedProcess(
            args=["gh", "auth", "token"], returncode=0, stdout="ghp_token123\n", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            token = _fetch_token_from_gh_cli()
        self.assertEqual(token, "ghp_token123")

    def test_fetch_token_from_gh_cli_failure(self) -> None:
        import subprocess

        mock_result = subprocess.CompletedProcess(
            args=["gh", "auth", "token"], returncode=1, stdout="", stderr="not logged in"
        )
        with patch("subprocess.run", return_value=mock_result):
            with self.assertRaises(RuntimeError) as ctx:
                _fetch_token_from_gh_cli()
        self.assertIn("gh auth token failed", str(ctx.exception))
        self.assertIn("not logged in", str(ctx.exception))

    def test_fetch_token_from_gh_cli_empty_token(self) -> None:
        import subprocess

        mock_result = subprocess.CompletedProcess(
            args=["gh", "auth", "token"], returncode=0, stdout="   \n", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result):
            with self.assertRaises(RuntimeError) as ctx:
                _fetch_token_from_gh_cli()
        self.assertIn("empty token", str(ctx.exception))
