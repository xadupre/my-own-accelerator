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
) -> str:
    base = f"{api_url.rstrip('/')}/repos/{owner}/{repo}/pulls/{pull_request}"
    pr = _fetch_json(base, token)
    if not isinstance(pr, dict):
        raise ValueError("Unexpected pull request payload returned by API.")
    files = _fetch_files(f"{base}/files", token)
    return build_pull_request_review_markdown(pr, files)


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
    args = parser.parse_args(argv)

    try:
        markdown = review_pull_request(
            owner=args.owner,
            repo=args.repo,
            pull_request=args.pull_request,
            token=args.token,
            api_url=args.api_url,
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
