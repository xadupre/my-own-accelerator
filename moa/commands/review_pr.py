"""Pull request review command line."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

PAGE_SIZE = 100

MODELS_API_URL = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = "openai/gpt-4o-mini"


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
        ai_text = _call_copilot_review(markdown, token, model)
        markdown = f"{markdown}\n\n## Copilot Review\n\n{ai_text}"
    return markdown


def _call_copilot_review(
    pr_markdown: str,
    token: str,
    model: str = DEFAULT_MODEL,
    models_url: str = MODELS_API_URL,
) -> str:
    """Send the PR summary to the GitHub Models API for an AI-powered review.

    :param pr_markdown: Markdown text describing the pull request.
    :param token: GitHub personal access token with models access.
    :param model: Model identifier accepted by the GitHub Models API.
    :param models_url: Base URL of the GitHub Models API.
    :return: AI-generated review text as a plain string.
    :raises ValueError: If the API returns no choices or empty content.
    :raises urllib.error.HTTPError: If the HTTP request fails.
    """
    url = f"{models_url.rstrip('/')}/chat/completions"
    system_prompt = (
        "You are an expert software engineer and code reviewer. "
        "Review the following pull request summary and provide constructive feedback. "
        "Highlight potential issues, suggest improvements, and note relevant best practices."
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": pr_markdown},
        ],
    }
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reviews a GitHub pull request and prints markdown."
    )
    parser.add_argument("owner", help="GitHub repository owner")
    parser.add_argument("repo", help="GitHub repository name")
    parser.add_argument("pull_request", type=int, help="Pull request number")
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub personal access token. Defaults to the GITHUB_TOKEN environment variable.",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        help=(
            "Base URL of the GitHub API. "
            "Defaults to the GITHUB_API_URL environment variable when set, "
            "otherwise https://api.github.com."
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
    args = parser.parse_args(argv)

    try:
        markdown = review_pull_request(
            owner=args.owner,
            repo=args.repo,
            pull_request=args.pull_request,
            token=args.token,
            api_url=args.api_url,
            copilot_review=args.copilot_review,
            model=args.model,
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
