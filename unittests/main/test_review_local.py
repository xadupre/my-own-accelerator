import json
import os
import pathlib
import tempfile
from io import StringIO
from unittest.mock import patch

from moa.commands.review_local import (
    DEFAULT_MODEL,
    build_local_files_review_markdown,
    main,
    review_local_files,
)
from moa.ext_test_case import ExtTestCase


class TestReviewLocal(ExtTestCase):
    def test_build_local_files_review_markdown(self) -> None:
        got = build_local_files_review_markdown({"a.py": "print('x')"})
        self.assertIn("# Local Files Review", got)
        self.assertIn("- **Files reviewed:** 1", got)
        self.assertIn("### `a.py`", got)

    def test_review_local_files_returns_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file1 = pathlib.Path(tmp) / "a.py"
            file1.write_text("print('a')", encoding="utf-8")
            got = review_local_files([str(file1)])
        self.assertIn("###", got)
        self.assertIn("print('a')", got)

    def test_review_local_files_with_copilot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file1 = pathlib.Path(tmp) / "a.py"
            file1.write_text("print('a')", encoding="utf-8")
            with patch(
                "moa.commands.review_local._call_copilot_review",
                return_value="AI feedback",
            ) as mock_ai:
                got = review_local_files([str(file1)], copilot_review=True, token="tok")
        self.assertIn("## Copilot Review", got)
        self.assertIn("AI feedback", got)
        mock_ai.assert_called_once()

    def test_main_prints_markdown(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            file1 = pathlib.Path(tmp) / "a.py"
            file1.write_text("print('a')", encoding="utf-8")
            with patch("sys.stdout", out):
                code = main([str(file1)])
        self.assertEqual(code, 0)
        self.assertIn("# Local Files Review", out.getvalue())

    def test_main_copilot_review_flag(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            file1 = pathlib.Path(tmp) / "a.py"
            file1.write_text("print('a')", encoding="utf-8")
            with (
                patch(
                    "moa.commands.review_local._call_copilot_review",
                    return_value="AI review",
                ) as mock_ai,
                patch("sys.stdout", out),
            ):
                code = main(["--copilot-review", "--token", "tok", str(file1)])
        self.assertEqual(code, 0)
        self.assertIn("AI review", out.getvalue())
        mock_ai.assert_called_once()
        self.assertEqual(mock_ai.call_args.args[2], DEFAULT_MODEL)

    def test_main_uses_cached_token_when_no_env(self) -> None:
        out = StringIO()
        env_backup = (
            {"GITHUB_TOKEN": os.environ.pop("GITHUB_TOKEN")}
            if "GITHUB_TOKEN" in os.environ
            else {}
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                file1 = pathlib.Path(tmp) / "a.py"
                file1.write_text("print('a')", encoding="utf-8")
                with (
                    patch(
                        "moa.commands.review_local._call_copilot_review",
                        return_value="AI review",
                    ) as mock_ai,
                    patch("sys.stdout", out),
                    patch(
                        "moa.commands.review_local._load_cache",
                        return_value={"token": "cached_tok"},
                    ),
                ):
                    code = main(["--copilot-review", str(file1)])
        finally:
            os.environ.update(env_backup)
        self.assertEqual(code, 0)
        self.assertEqual(mock_ai.call_args.args[1], "cached_tok")

    def test_main_save_flag_persists_token(self) -> None:
        out = StringIO()
        env_backup = (
            {"GITHUB_TOKEN": os.environ.pop("GITHUB_TOKEN")}
            if "GITHUB_TOKEN" in os.environ
            else {}
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_config = pathlib.Path(tmp) / "review_pr.json"
                file1 = pathlib.Path(tmp) / "a.py"
                file1.write_text("print('a')", encoding="utf-8")
                with (
                    patch("sys.stdout", out),
                    patch("moa.commands.review_local.CONFIG_FILE", fake_config),
                    patch("moa.commands.review_pr.CONFIG_FILE", fake_config),
                ):
                    code = main(["--token", "saved_tok", "--save", str(file1)])
                saved = json.loads(fake_config.read_text())
        finally:
            os.environ.update(env_backup)
        self.assertEqual(code, 0)
        self.assertEqual(saved["token"], "saved_tok")

    def test_main_prompt_flag_passed_to_review(self) -> None:
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            file1 = pathlib.Path(tmp) / "a.py"
            file1.write_text("print('a')", encoding="utf-8")
            with (
                patch(
                    "moa.commands.review_local._call_copilot_review",
                    return_value="AI review",
                ) as mock_ai,
                patch("sys.stdout", out),
            ):
                code = main(
                    [
                        "--copilot-review",
                        "--token",
                        "tok",
                        "--prompt",
                        "Any security issues?",
                        str(file1),
                    ]
                )
        self.assertEqual(code, 0)
        mock_ai.assert_called_once()
        call_kwargs = mock_ai.call_args.kwargs
        self.assertEqual(call_kwargs.get("extra_prompts"), ["Any security issues?"])
