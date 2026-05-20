import json
import pathlib
import tempfile
from io import StringIO
from unittest.mock import patch

from moa.commands.github_token import main
from moa.ext_test_case import ExtTestCase


class TestGitHubToken(ExtTestCase):
    def test_main_save_classic_token(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            fake_config = pathlib.Path(tmp) / "review_pr.json"
            with (
                patch("sys.stdout", out),
                patch("moa.commands.github_token.CONFIG_FILE", fake_config),
                patch("moa.commands.review_token.CONFIG_FILE", fake_config),
            ):
                code = main(["--token", "classic_tok", "--classic"])
            saved = json.loads(fake_config.read_text())

        self.assertEqual(code, 0)
        self.assertEqual(saved["token"], "classic_tok")
        self.assertIn("saved classic token", out.getvalue())

    def test_main_save_project_token(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            fake_config = pathlib.Path(tmp) / "review_pr.json"
            fake_config.write_text(json.dumps({"token": "classic_tok"}), encoding="utf-8")
            with (
                patch("sys.stdout", out),
                patch("moa.commands.github_token.CONFIG_FILE", fake_config),
                patch("moa.commands.review_token.CONFIG_FILE", fake_config),
            ):
                code = main(["--token", "project_tok", "--owner", "owner", "--repo", "repo"])
            saved = json.loads(fake_config.read_text())

        self.assertEqual(code, 0)
        self.assertEqual(saved["token"], "classic_tok")
        self.assertEqual(saved["project_tokens"]["owner/repo"], "project_tok")
        self.assertIn("saved token for owner/repo", out.getvalue())

    def test_main_fails_when_owner_without_repo(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(["--token", "project_tok", "--owner", "owner"])
        self.assertEqual(ctx.exception.code, 2)

    def test_main_list_tokens(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            fake_config = pathlib.Path(tmp) / "review_pr.json"
            fake_config.write_text(
                json.dumps(
                    {
                        "token": "classic_tok",
                        "project_tokens": {"owner/repo": "project_tok"},
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("sys.stdout", out),
                patch("moa.commands.github_token.CONFIG_FILE", fake_config),
                patch("moa.commands.review_token.CONFIG_FILE", fake_config),
            ):
                code = main(["--list"])

        self.assertEqual(code, 0)
        self.assertEqual(
            out.getvalue().splitlines(), ["classic: classic_tok", "owner/repo: project_tok"]
        )

    def test_main_list_tokens_verbose(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            fake_config = pathlib.Path(tmp) / "review_pr.json"
            fake_config.write_text(
                json.dumps(
                    {
                        "token": "classic_tok",
                        "project_tokens": {"owner/repo": "project_tok"},
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("sys.stdout", out),
                patch("moa.commands.github_token.CONFIG_FILE", fake_config),
                patch("moa.commands.review_token.CONFIG_FILE", fake_config),
            ):
                code = main(["--list", "--verbose"])

        self.assertEqual(code, 0)
        self.assertEqual(
            out.getvalue().splitlines(),
            [
                f"classic ({fake_config}): classic_tok [type=classic]",
                f"owner/repo ({fake_config}): project_tok [type=fine-grained]",
            ],
        )
