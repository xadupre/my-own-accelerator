import io
import json
import os
import pathlib
import tempfile
from asyncio import run
from datetime import datetime
from io import StringIO
from unittest.mock import AsyncMock, patch

from moa.commands.copilot_models import (
    FALLBACK_MODEL,
    NO_MODEL_AVAILABLE_MESSAGE_PREFIX,
    CopilotSessionError,
    _log_copilot_request_and_answer,
    _send_chat_request,
    _send_copilot_prompts,
)
from moa.commands.review_pr import (
    DEFAULT_MODEL,
    _call_copilot_review,
    _extract_owner_repo,
    _load_cache,
    _resolve_positional_argv,
    _save_cache,
    build_pull_request_review_markdown,
    main,
    review_pull_request,
)
from moa.commands.review_token import CONFIG_FILE
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
                patch("moa.commands.review_pr._load_cache", return_value={}),
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
            extra_prompts=None,
        )
        self.assertEqual(out.getvalue(), "# review\n")

    def test_main_uses_env_vars_automatically(self) -> None:
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
                patch("moa.commands.review_pr._load_cache", return_value={}),
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
            extra_prompts=None,
        )

    def test_main_verbose_flag_prints_progress(self) -> None:
        out = StringIO()
        err = StringIO()
        with (
            patch(
                "moa.commands.review_pr.review_pull_request",
                return_value="# review",
            ),
            patch("sys.stdout", out),
            patch("sys.stderr", err),
            patch.dict(os.environ, {"GITHUB_TOKEN": ""}),
            patch("moa.commands.review_pr._load_cache", return_value={}),
        ):
            code = main(["-v", "owner", "repo", "12"])
        self.assertEqual(code, 0)
        self.assertIn("review-pr: token source=none, type=none.", err.getvalue())
        self.assertIn("review-pr: fetching owner/repo#12...", err.getvalue())
        self.assertIn("review-pr: done.", err.getvalue())

    def test_main_verbose_flag_prints_copilot_model(self) -> None:
        out = StringIO()
        err = StringIO()

        def fake_review_pull_request(*args: object, **kwargs: object) -> str:
            on_model_used = kwargs.get("on_model_used")
            self.assertIsNotNone(on_model_used)
            on_model_used("openai/gpt-4.1")
            return "# review"

        with (
            patch(
                "moa.commands.review_pr.review_pull_request",
                side_effect=fake_review_pull_request,
            ),
            patch("sys.stdout", out),
            patch("sys.stderr", err),
            patch.dict(os.environ, {"GITHUB_TOKEN": ""}),
            patch("moa.commands.review_pr._load_cache", return_value={}),
        ):
            code = main(["-v", "--copilot-review", "--token", "tok", "owner", "repo", "12"])
        self.assertEqual(code, 0)
        self.assertIn("review-pr: copilot model=openai/gpt-4.1.", err.getvalue())

    def test_main_verbose_flag_prints_fine_grained_token_origin(self) -> None:
        out = StringIO()
        err = StringIO()
        env_token_backup = os.environ.pop("GITHUB_TOKEN", None)
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ),
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch(
                    "moa.commands.review_pr._load_cache",
                    return_value={
                        "token": "classic_tok",
                        "project_tokens": {"owner/repo": "project_tok"},
                    },
                ),
            ):
                code = main(["-v", "owner", "repo", "12"])
        finally:
            if env_token_backup is not None:
                os.environ["GITHUB_TOKEN"] = env_token_backup
        self.assertEqual(code, 0)
        self.assertIn(
            f"review-pr: token source={CONFIG_FILE} (owner/repo), type=fine-grained.",
            err.getvalue(),
        )

    def test_call_copilot_review_returns_content(self) -> None:
        with patch(
            "moa.commands.review_pr._send_copilot_prompts",
            new=AsyncMock(return_value=["Looks good to me!"]),
        ):
            result = _call_copilot_review("## PR Summary", "mytoken")
        self.assertEqual(result, "Looks good to me!")

    def test_call_copilot_review_logs_request_and_answer(self) -> None:
        with (
            patch(
                "moa.commands.copilot_models._send_session_prompt",
                new=AsyncMock(return_value="Looks good to me!"),
            ),
            patch("moa.commands.copilot_models._log_copilot_request_and_answer") as mocked_log,
            patch("moa.commands.copilot_models.CopilotClient") as mocked_client,
        ):
            mocked_client.return_value.__aenter__ = AsyncMock(
                return_value=mocked_client.return_value
            )
            mocked_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mocked_session = mocked_client.return_value.create_session.return_value
            mocked_client.return_value.create_session = AsyncMock(return_value=mocked_session)
            mocked_session.__aenter__ = AsyncMock(return_value=mocked_session)
            mocked_session.__aexit__ = AsyncMock(return_value=False)
            _call_copilot_review("## PR Summary", "mytoken")
        mocked_log.assert_called_once()
        self.assertEqual(mocked_log.call_args.kwargs["command_name"], "review-pr")

    def test_log_copilot_request_and_answer_creates_weekly_timestamped_files(self) -> None:
        now = datetime(2026, 5, 15, 11, 33, 30)
        payload = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
        result = {"choices": [{"message": {"content": "world"}}]}
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = pathlib.Path(tmp)
            _log_copilot_request_and_answer(payload, result, logs_dir=logs_dir, now=now)

            target_dir = logs_dir / "2026" / "05" / "week-20" / "review-pr"
            request_file = target_dir / "2026-05-15_11-33-30_request.json"
            answer_file = target_dir / "2026-05-15_11-33-30_answer.json"
            self.assertTrue(target_dir.exists())
            self.assertTrue(request_file.exists())
            self.assertTrue(answer_file.exists())
            self.assertEqual(json.loads(request_file.read_text(encoding="utf-8")), payload)
            self.assertEqual(json.loads(answer_file.read_text(encoding="utf-8")), result)

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
                patch("moa.commands.review_pr._load_cache", return_value={}),
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

    def test_load_cache_missing_file(self) -> None:
        with patch("moa.commands.review_token.CONFIG_FILE") as mock_path:
            mock_path.open.side_effect = FileNotFoundError
            result = _load_cache()
        self.assertEqual(result, {})

    def test_load_cache_invalid_json(self) -> None:
        with patch("moa.commands.review_token.CONFIG_FILE") as mock_path:
            mock_path.open.return_value.__enter__ = lambda s: io.StringIO("not-json")
            mock_path.open.return_value.__exit__ = lambda s, *a: False
            with patch(
                "moa.commands.review_pr.json.load", side_effect=json.JSONDecodeError("x", "", 0)
            ):
                result = _load_cache()
        self.assertEqual(result, {})

    def test_save_cache_writes_and_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_config = pathlib.Path(tmp) / "review_pr.json"
            with patch("moa.commands.review_token.CONFIG_FILE", fake_config):
                _save_cache({"token": "mytoken", "api_url": "https://api.github.com"})
                loaded = _load_cache()

        self.assertEqual(loaded["token"], "mytoken")
        self.assertEqual(loaded["api_url"], "https://api.github.com")

    def test_save_cache_merges_existing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_config = pathlib.Path(tmp) / "review_pr.json"
            with patch("moa.commands.review_token.CONFIG_FILE", fake_config):
                _save_cache({"token": "tok1", "api_url": "https://a.example.com"})
                # Update only the token; api_url should be preserved
                _save_cache({"token": "tok2"})
                loaded = _load_cache()

        self.assertEqual(loaded["token"], "tok2")
        self.assertEqual(loaded["api_url"], "https://a.example.com")

    def test_main_uses_cached_token_when_no_env(self) -> None:
        out = StringIO()
        env_backup = {
            k: os.environ.pop(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL") if k in os.environ
        }
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
                patch(
                    "moa.commands.review_pr._load_cache",
                    return_value={"token": "cached_tok", "api_url": "https://cached.example.com"},
                ),
            ):
                code = main(["owner", "repo", "5"])
        finally:
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="owner",
            repo="repo",
            pull_request=5,
            token="cached_tok",
            api_url="https://cached.example.com",
            copilot_review=False,
            model=DEFAULT_MODEL,
            extra_prompts=None,
        )

    def test_main_prefers_project_cached_token_when_no_env(self) -> None:
        out = StringIO()
        env_keys = ("GITHUB_TOKEN", "GITHUB_API_URL")
        env_backup = {k: os.environ.get(k) for k in env_keys}
        for k in env_keys:
            os.environ.pop(k, None)
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
                patch(
                    "moa.commands.review_pr._load_cache",
                    return_value={
                        "token": "classic_tok",
                        "project_tokens": {"owner/repo": "project_tok"},
                        "api_url": "https://cached.example.com",
                    },
                ),
            ):
                code = main(["owner", "repo", "5"])
        finally:
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="owner",
            repo="repo",
            pull_request=5,
            token="project_tok",
            api_url="https://cached.example.com",
            copilot_review=False,
            model=DEFAULT_MODEL,
            extra_prompts=None,
        )

    def test_main_uses_classic_token_when_project_cached_token_missing(self) -> None:
        out = StringIO()
        env_keys = ("GITHUB_TOKEN", "GITHUB_API_URL")
        env_backup = {k: os.environ.get(k) for k in env_keys}
        for k in env_keys:
            os.environ.pop(k, None)
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
                patch(
                    "moa.commands.review_pr._load_cache",
                    return_value={
                        "token": "classic_tok",
                        "project_tokens": {"other/repo": "project_tok"},
                        "api_url": "https://cached.example.com",
                    },
                ),
            ):
                code = main(["owner", "repo", "5"])
        finally:
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="owner",
            repo="repo",
            pull_request=5,
            token="classic_tok",
            api_url="https://cached.example.com",
            copilot_review=False,
            model=DEFAULT_MODEL,
            extra_prompts=None,
        )

    def test_main_copilot_review_does_not_fallback_to_classic_cached_token(self) -> None:
        out = StringIO()
        err = StringIO()
        env_keys = ("GITHUB_TOKEN", "GITHUB_API_URL")
        env_backup = {k: os.environ.get(k) for k in env_keys}
        for k in env_keys:
            os.environ.pop(k, None)
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
                patch("sys.stderr", err),
                patch(
                    "moa.commands.review_pr._load_cache",
                    return_value={
                        "token": "classic_tok",
                        "project_tokens": {"other/repo": "project_tok"},
                    },
                ),
            ):
                code = main(["--copilot-review", "owner", "repo", "5"])
        finally:
            for k, v in env_backup.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.assertEqual(code, 1)
        mocked.assert_not_called()
        self.assertIn("required for --copilot-review", err.getvalue())

    def test_main_save_flag_persists_values(self) -> None:
        out = StringIO()
        env_backup = {
            k: os.environ.pop(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL") if k in os.environ
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_config = pathlib.Path(tmp) / "review_pr.json"
                with (
                    patch(
                        "moa.commands.review_pr.review_pull_request",
                        return_value="# review",
                    ),
                    patch("sys.stdout", out),
                    patch("moa.commands.review_token.CONFIG_FILE", fake_config),
                ):
                    code = main(
                        [
                            "--token",
                            "saved_tok",
                            "--api-url",
                            "https://ghe.example.com/api/v3",
                            "--save",
                            "owner",
                            "repo",
                            "3",
                        ]
                    )
                saved = json.loads(fake_config.read_text())
        finally:
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        self.assertEqual(saved["token"], "saved_tok")
        self.assertEqual(saved["api_url"], "https://ghe.example.com/api/v3")
        self.assertEqual(saved["project_tokens"]["owner/repo"], "saved_tok")

    # ------------------------------------------------------------------
    # User caching
    # ------------------------------------------------------------------

    def test_resolve_positional_argv_injects_user_for_two_positionals(self) -> None:
        result = _resolve_positional_argv(["myrepo", "42"], "alice")
        self.assertEqual(result, ["alice", "myrepo", "42"])

    def test_resolve_positional_argv_no_inject_for_three_positionals(self) -> None:
        result = _resolve_positional_argv(["owner", "myrepo", "42"], "alice")
        self.assertEqual(result, ["owner", "myrepo", "42"])

    def test_resolve_positional_argv_no_inject_when_user_is_none(self) -> None:
        result = _resolve_positional_argv(["myrepo", "42"], None)
        self.assertEqual(result, ["myrepo", "42"])

    def test_resolve_positional_argv_handles_flags(self) -> None:
        # --token and its value must not be counted as positionals.
        result = _resolve_positional_argv(["--token", "tok", "myrepo", "42"], "alice")
        self.assertEqual(result, ["--token", "tok", "alice", "myrepo", "42"])

    def test_extract_owner_repo_skips_flag_values(self) -> None:
        result = _extract_owner_repo(
            ["--token", "tok", "--prompt", "check this", "owner", "repo", "42"]
        )
        self.assertEqual(result, ("owner", "repo"))

    def test_extract_owner_repo_returns_none_when_missing_positionals(self) -> None:
        result = _extract_owner_repo(["--token", "tok"])
        self.assertEqual(result, (None, None))

    def test_main_uses_cached_user_as_owner(self) -> None:
        out = StringIO()
        env_backup = {
            k: os.environ.pop(k)
            for k in ("GITHUB_TOKEN", "GITHUB_API_URL", "GITHUB_USER")
            if k in os.environ
        }
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
                patch(
                    "moa.commands.review_pr._load_cache",
                    return_value={"user": "cached_user"},
                ),
            ):
                # Only repo and pull_request provided; owner should come from cache.
                code = main(["myrepo", "7"])
        finally:
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="cached_user",
            repo="myrepo",
            pull_request=7,
            token=None,
            api_url="https://api.github.com",
            copilot_review=False,
            model=DEFAULT_MODEL,
            extra_prompts=None,
        )

    def test_main_save_flag_persists_user(self) -> None:
        out = StringIO()
        env_backup = {
            k: os.environ.pop(k)
            for k in ("GITHUB_TOKEN", "GITHUB_API_URL", "GITHUB_USER")
            if k in os.environ
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fake_config = pathlib.Path(tmp) / "review_pr.json"
                with (
                    patch(
                        "moa.commands.review_pr.review_pull_request",
                        return_value="# review",
                    ),
                    patch("sys.stdout", out),
                    patch("moa.commands.review_token.CONFIG_FILE", fake_config),
                ):
                    code = main(
                        [
                            "--user",
                            "myname",
                            "--save",
                            "myname",
                            "repo",
                            "1",
                        ]
                    )
                saved = json.loads(fake_config.read_text())
        finally:
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        self.assertEqual(saved["user"], "myname")

    def test_save_cache_includes_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_config = pathlib.Path(tmp) / "review_pr.json"
            with patch("moa.commands.review_token.CONFIG_FILE", fake_config):
                _save_cache(
                    {"token": "tok", "api_url": "https://api.github.com", "user": "alice"}
                )
                loaded = _load_cache()

        self.assertEqual(loaded["user"], "alice")

    # ------------------------------------------------------------------
    # Multi-turn session (--prompt)
    # ------------------------------------------------------------------

    def test_send_chat_request_returns_content(self) -> None:
        messages = [{"role": "user", "content": "Hi"}]
        with patch(
            "moa.commands.copilot_models._send_copilot_prompts",
            new=AsyncMock(return_value=["Hello!"]),
        ) as mocked_send:
            result = _send_chat_request(messages, "mytoken")
        self.assertEqual(result, "Hello!")
        self.assertEqual(mocked_send.call_args.args[:2], (["Hi"], "mytoken"))
        self.assertIsNone(mocked_send.call_args.kwargs["model"])

    def test_send_copilot_prompts_omits_model_when_none(self) -> None:
        with (
            patch(
                "moa.commands.copilot_models._send_session_prompt",
                new=AsyncMock(return_value="Hello!"),
            ),
            patch("moa.commands.copilot_models.CopilotClient") as mocked_client,
        ):
            mocked_client.return_value.__aenter__ = AsyncMock(
                return_value=mocked_client.return_value
            )
            mocked_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mocked_session = mocked_client.return_value.create_session.return_value
            mocked_client.return_value.create_session = AsyncMock(return_value=mocked_session)
            mocked_session.__aenter__ = AsyncMock(return_value=mocked_session)
            mocked_session.__aexit__ = AsyncMock(return_value=False)

            result = run(_send_copilot_prompts(["Hi"], "mytoken", model=None))

        self.assertEqual(result, ["Hello!"])
        self.assertNotIn("model", mocked_client.return_value.create_session.await_args.kwargs)

    def test_send_copilot_prompts_retries_with_fallback_model(self) -> None:
        with (
            patch(
                "moa.commands.copilot_models._send_session_prompt",
                new=AsyncMock(return_value="Hello!"),
            ),
            patch("moa.commands.copilot_models.CopilotClient") as mocked_client,
        ):
            mocked_client.return_value.__aenter__ = AsyncMock(
                return_value=mocked_client.return_value
            )
            mocked_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mocked_session = mocked_client.return_value.create_session.return_value
            mocked_client.return_value.create_session = AsyncMock(
                side_effect=[
                    CopilotSessionError(
                        f"{NO_MODEL_AVAILABLE_MESSAGE_PREFIX} "
                        "Check policy enablement under GitHub Settings > Copilot",
                        error_type="session",
                        status_code=400,
                    ),
                    mocked_session,
                ]
            )
            mocked_session.__aenter__ = AsyncMock(return_value=mocked_session)
            mocked_session.__aexit__ = AsyncMock(return_value=False)

            result = run(_send_copilot_prompts(["Hi"], "mytoken", model=None))

        self.assertEqual(result, ["Hello!"])
        self.assertEqual(mocked_client.return_value.create_session.call_count, 2)
        first_call, second_call = mocked_client.return_value.create_session.await_args_list
        self.assertNotIn("model", first_call.kwargs)
        self.assertEqual(second_call.kwargs["model"], FALLBACK_MODEL)

    def test_call_copilot_review_no_extra_prompts_single_call(self) -> None:
        with patch(
            "moa.commands.review_pr._send_copilot_prompts",
            new=AsyncMock(return_value=["Initial review."]),
        ) as mocked_send:
            result = _call_copilot_review("## PR", "tok")
        self.assertEqual(result, "Initial review.")
        self.assertEqual(mocked_send.call_args.args[:2], (["## PR"], "tok"))

    def test_call_copilot_review_with_extra_prompts_multi_turn(self) -> None:
        with patch(
            "moa.commands.review_pr._send_copilot_prompts",
            new=AsyncMock(return_value=["Initial review.", "Follow-up answer."]),
        ) as mocked_send:
            result = _call_copilot_review("## PR", "tok", extra_prompts=["Focus on security."])
        self.assertIn("Initial review.", result)
        self.assertIn("Follow-up answer.", result)
        self.assertIn("Focus on security.", result)
        self.assertEqual(mocked_send.call_args.args[:2], (["## PR", "Focus on security."], "tok"))

    def test_main_prompt_flag_passed_to_review(self) -> None:
        out = StringIO()
        env_backup = {
            k: os.environ.pop(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL") if k in os.environ
        }
        os.environ["GITHUB_TOKEN"] = "tok"
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
                patch("moa.commands.review_pr._load_cache", return_value={}),
            ):
                code = main(
                    [
                        "--copilot-review",
                        "--prompt",
                        "What are the security implications?",
                        "--prompt",
                        "Any performance concerns?",
                        "owner",
                        "repo",
                        "12",
                    ]
                )
        finally:
            os.environ.pop("GITHUB_TOKEN", None)
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        call_kwargs = mocked.call_args.kwargs
        self.assertEqual(
            call_kwargs["extra_prompts"],
            ["What are the security implications?", "Any performance concerns?"],
        )

    def test_main_gh_flag_fetches_token(self) -> None:
        out = StringIO()
        env_backup = {
            k: os.environ.pop(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL") if k in os.environ
        }
        try:
            with (
                patch(
                    "moa.commands.review_pr.review_pull_request",
                    return_value="# review",
                ) as mocked,
                patch("sys.stdout", out),
                patch("moa.commands.review_pr._load_cache", return_value={}),
                patch(
                    "moa.commands.review_pr._fetch_token_from_gh_cli",
                    return_value="ghp_from_cli",
                ),
            ):
                code = main(["--gh", "owner", "repo", "12"])
        finally:
            os.environ.update(env_backup)

        self.assertEqual(code, 0)
        mocked.assert_called_once_with(
            owner="owner",
            repo="repo",
            pull_request=12,
            token="ghp_from_cli",
            api_url="https://api.github.com",
            copilot_review=False,
            model=DEFAULT_MODEL,
            extra_prompts=None,
        )

    def test_main_gh_and_token_are_mutually_exclusive(self) -> None:
        env_backup = {
            k: os.environ.pop(k) for k in ("GITHUB_TOKEN", "GITHUB_API_URL") if k in os.environ
        }
        try:
            with (patch("moa.commands.review_pr._load_cache", return_value={}),):
                with self.assertRaises(SystemExit) as ctx:
                    main(["--gh", "--token", "explicit_tok", "owner", "repo", "12"])
        finally:
            os.environ.update(env_backup)
        self.assertEqual(ctx.exception.code, 2)
