"""Pull request review command line."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

PAGE_SIZE = 100

MODELS_API_URL = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = "openai/gpt-4o-mini"
CONFIG_FILE = pathlib.Path.home() / ".config" / "moa" / "review_pr.json"


def _load_cache() -> dict[str, str]:
    """Load cached settings (token, api_url, user) from the config file.

    :return: Dictionary with cached values, or an empty dict if the file
        does not exist or cannot be parsed.
    """
    try:
        with CONFIG_FILE.open() as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _resolve_positional_argv(argv: list[str], user: str | None) -> list[str]:
    """Inject *user* as the first positional (owner) when only two positionals are given.

    This lets callers omit ``owner`` when their GitHub username is already cached
    (e.g. ``review-pr my-repo 42`` instead of ``review-pr myname my-repo 42``).

    :param argv: Argument list as would be passed to ``argparse``.
    :param user: GitHub username to inject as ``owner`` when it is absent.
    :return: Possibly modified argument list.
    """
    if user is None:
        return argv
    # Flags that consume the following token as their value.
    VALUE_FLAGS = {"--token", "--api-url", "--model", "--user"}
    positional_indices: list[int] = []
    skip_next = False
    for i, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token in VALUE_FLAGS:
            skip_next = True
        elif token.startswith("-"):
            pass  # boolean flag, consumes no extra token
        else:
            positional_indices.append(i)
    if len(positional_indices) == 2:
        insert_at = positional_indices[0]
        return list(argv[:insert_at]) + [user] + list(argv[insert_at:])
    return argv


def _save_cache(data: dict[str, str]) -> None:
    """Persist settings to the config file with owner-only read permissions.

    Existing keys not present in *data* are preserved.

    :param data: Mapping of keys to save
        (e.g. ``{"token": "...", "api_url": "...", "user": "..."}``)
    """
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_cache()
    existing.update({k: v for k, v in data.items() if v is not None})
    with CONFIG_FILE.open("w") as f:
        json.dump(existing, f, indent=2)
    CONFIG_FILE.chmod(0o600)


def _fetch_json(url: str, token: str | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "moa/review-pr",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers)
    with request.urlopen(req) as response:
        return json.load(response)


def _fetch_files(base_url: str, token: str | None = None) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page = 1
    while True:
        page_url = f"{base_url}?{parse.urlencode({'per_page': PAGE_SIZE, 'page': page})}"
        data = _fetch_json(page_url, token)
        if not isinstance(data, list) or not data:
            break
        if any(not isinstance(item, dict) for item in data):
            raise ValueError("Unexpected pull request file payload returned by API.")
        files.extend(data)
        if len(data) < PAGE_SIZE:
            break
        page += 1
    return files


def build_pull_request_review_markdown(pr: dict[str, Any], files: list[dict[str, Any]]) -> str:
    title = pr.get("title", "")
    state = pr.get("state", "")
    user = pr.get("user", {})
    author = user.get("login", "") if isinstance(user, dict) else ""
    body = (pr.get("body") or "").strip()
    html_url = pr.get("html_url", "")
    changed_files = int(pr.get("changed_files", len(files)))
    raw_additions = pr.get("additions")
    if raw_additions is None:
        raw_additions = sum(int(f.get("additions", 0)) for f in files)
    additions = int(raw_additions)
    raw_deletions = pr.get("deletions")
    if raw_deletions is None:
        raw_deletions = sum(int(f.get("deletions", 0)) for f in files)
    deletions = int(raw_deletions)

    lines = [
        "# Pull Request Review",
        "",
        "## Summary",
        f"- **Title:** {title}",
        f"- **State:** {state}",
        f"- **Author:** {author}",
        f"- **URL:** {html_url}",
        f"- **Files changed:** {changed_files}",
        f"- **Additions/Deletions:** +{additions} / -{deletions}",
        "",
        "## Description",
        body if body else "_No description provided._",
        "",
        "## Changed Files",
    ]
    if not files:
        lines.append("- _No files listed by the API._")
    else:
        lines.extend(
            (
                f"- `{f.get('filename', '')}` "
                f"(+{int(f.get('additions', 0))}/-{int(f.get('deletions', 0))})"
            )
            for f in files
        )
    return "\n".join(lines)


def review_pull_request(
    owner: str,
    repo: str,
    pull_request: int,
    token: str | None = None,
    api_url: str = "https://api.github.com",
    copilot_review: bool = False,
    model: str = DEFAULT_MODEL,
    extra_prompts: list[str] | None = None,
) -> str:
    base = f"{api_url.rstrip('/')}/repos/{owner}/{repo}/pulls/{pull_request}"
    pr = _fetch_json(base, token)
    if not isinstance(pr, dict):
        raise ValueError("Unexpected pull request payload returned by API.")
    files = _fetch_files(f"{base}/files", token)
    markdown = build_pull_request_review_markdown(pr, files)
    if copilot_review:
        if not token:
            raise ValueError(
                "A GitHub token (--token or GITHUB_TOKEN env var) "
                "is required for --copilot-review."
            )
        ai_text = _call_copilot_review(markdown, token, model, extra_prompts=extra_prompts)
        markdown = f"{markdown}\n\n## Copilot Review\n\n{ai_text}"
    return markdown


def _send_chat_request(
    messages: list[dict[str, str]],
    token: str,
    model: str = DEFAULT_MODEL,
    models_url: str = MODELS_API_URL,
) -> str:
    """Send a chat completion request to the GitHub Models API.

    :param messages: List of message dicts with ``role`` and ``content`` keys.
    :param token: GitHub personal access token with models access.
    :param model: Model identifier accepted by the GitHub Models API.
    :param models_url: Base URL of the GitHub Models API.
    :return: Content string from the first choice in the API response.
    :raises ValueError: If the API returns no choices or empty content.
    :raises urllib.error.HTTPError: If the HTTP request fails.
    """
    url = f"{models_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages}
    data = json.dumps(payload).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "moa/review-pr",
    }
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req) as response:
        result = json.load(response)
    choices = result.get("choices", [])
    if not choices:
        raise ValueError("No response choices returned by the AI model.")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if not content:
        raise ValueError("Empty content in AI model response.")
    return content


def _call_copilot_review(
    pr_markdown: str,
    token: str,
    model: str = DEFAULT_MODEL,
    models_url: str = MODELS_API_URL,
    extra_prompts: list[str] | None = None,
) -> str:
    """Send the PR summary to the GitHub Models API for an AI-powered review.

    When *extra_prompts* are provided each prompt is sent as a follow-up
    message in the same conversation session (the assistant reply from the
    previous turn is included in subsequent requests so the model has full
    context).  The returned string contains the initial review and any
    follow-up responses separated by a heading for each prompt.

    :param pr_markdown: Markdown text describing the pull request.
    :param token: GitHub personal access token with models access.
    :param model: Model identifier accepted by the GitHub Models API.
    :param models_url: Base URL of the GitHub Models API.
    :param extra_prompts: Optional list of follow-up prompts to continue the
        conversation after the initial review.
    :return: AI-generated review text as a plain string.
    :raises ValueError: If the API returns no choices or empty content.
    :raises urllib.error.HTTPError: If the HTTP request fails.
    """
    system_prompt = (
        "You are an expert software engineer and code reviewer. "
        "Review the following pull request summary and provide constructive feedback. "
        "Highlight potential issues, suggest improvements, and note relevant best practices."
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": pr_markdown},
    ]
    initial_review = _send_chat_request(messages, token, model, models_url)

    if not extra_prompts:
        return initial_review

    parts = [initial_review]
    messages.append({"role": "assistant", "content": initial_review})
    for prompt in extra_prompts:
        messages.append({"role": "user", "content": prompt})
        reply = _send_chat_request(messages, token, model, models_url)
        parts.append(f"**Prompt:** {prompt}\n\n{reply}")
        messages.append({"role": "assistant", "content": reply})
    return "\n\n---\n\n".join(parts)


def _build_parser(
    token_default: str | None = None,
    api_url_default: str = "https://api.github.com",
    user_default: str | None = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``review-pr`` command.

    :param token_default: Default value for the ``--token`` argument.
    :param api_url_default: Default value for the ``--api-url`` argument.
    :param user_default: Default value for the ``--user`` argument.
    :return: Configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="review-pr",
        description="Reviews a GitHub pull request and prints markdown.",
    )
    parser.add_argument("owner", help="GitHub repository owner")
    parser.add_argument("repo", help="GitHub repository name")
    parser.add_argument("pull_request", type=int, help="Pull request number")
    parser.add_argument(
        "--token",
        default=token_default,
        metavar="TOKEN",
        help=(
            "GitHub personal access token. "
            "Resolution order: flag > GITHUB_TOKEN env var > cached value "
            f"({CONFIG_FILE}). "
            "See the docs/cmds/github_token page for how to obtain one."
        ),
    )
    parser.add_argument(
        "--api-url",
        default=api_url_default,
        metavar="URL",
        help=(
            "Base URL of the GitHub API. "
            "Resolution order: flag > GITHUB_API_URL env var > cached value "
            f"({CONFIG_FILE}) > https://api.github.com."
        ),
    )
    parser.add_argument(
        "--user",
        default=user_default,
        metavar="USERNAME",
        help=(
            "GitHub username of the authenticated user. "
            "Resolution order: flag > GITHUB_USER env var > cached value "
            f"({CONFIG_FILE}). "
            "When set and owner is omitted, the username is used as the repository owner."
        ),
    )
    parser.add_argument(
        "--save",
        action="store_true",
        default=False,
        help=(
            f"Save the resolved --token, --api-url, and --user to {CONFIG_FILE} "
            "so they are used automatically in future invocations. "
            "The file is created with owner-only read permissions (0600)."
        ),
    )
    parser.add_argument(
        "--copilot-review",
        action="store_true",
        default=False,
        help=(
            "Use GitHub Copilot (via GitHub Models API) to generate an AI review of the PR. "
            "Requires a GitHub token with models access."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            f"AI model used for --copilot-review (default: {DEFAULT_MODEL}). "
            "Any model available on the GitHub Models API is accepted."
        ),
    )
    parser.add_argument(
        "--prompt",
        dest="extra_prompts",
        action="append",
        default=None,
        metavar="PROMPT",
        help=(
            "Add a follow-up prompt to the Copilot review session. "
            "The prompt is sent as a continuation of the same conversation so "
            "the model has full context from the initial review. "
            "Can be used multiple times to ask several follow-up questions. "
            "Only meaningful when --copilot-review is also set."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Priority: CLI flag > env var > config file cache > built-in default
    cache = _load_cache()
    token_default = os.environ.get("GITHUB_TOKEN") or cache.get("token") or None
    api_url_default = (
        os.environ.get("GITHUB_API_URL") or cache.get("api_url") or "https://api.github.com"
    )
    user_default = os.environ.get("GITHUB_USER") or cache.get("user") or None

    # Pre-parse to discover the effective --user value before injecting owner.
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--user", default=user_default)
    _pre_args, _ = _pre.parse_known_args(argv)
    effective_user = _pre_args.user

    # Allow omitting owner when the GitHub username is cached / in env.
    argv = _resolve_positional_argv(argv, effective_user)

    parser = _build_parser(
        token_default=token_default,
        api_url_default=api_url_default,
        user_default=user_default,
    )
    args = parser.parse_args(argv)

    if args.save:
        to_save: dict[str, str] = {}
        if args.token:
            to_save["token"] = args.token
        if args.api_url:
            to_save["api_url"] = args.api_url
        if args.user:
            to_save["user"] = args.user
        _save_cache(to_save)

    try:
        markdown = review_pull_request(
            owner=args.owner,
            repo=args.repo,
            pull_request=args.pull_request,
            token=args.token,
            api_url=args.api_url,
            copilot_review=args.copilot_review,
            model=args.model,
            extra_prompts=args.extra_prompts,
        )
    except (HTTPError, URLError, ValueError) as e:
        print(
            (
                f"Unable to review pull request {args.owner}/{args.repo}#{args.pull_request} "
                f"({type(e).__name__}). Check repository access, PR number, "
                "and network/auth settings."
            ),
            file=sys.stderr,
        )
        return 1

    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
