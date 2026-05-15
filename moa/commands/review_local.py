"""Local files review command line."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from urllib.error import HTTPError, URLError

from .review_pr import DEFAULT_MODEL, _call_copilot_review


def build_local_files_review_markdown(contents: dict[str, str]) -> str:
    """Builds a markdown report for local files."""
    lines = [
        "# Local Files Review",
        "",
        "## Summary",
        f"- **Files reviewed:** {len(contents)}",
        "",
        "## Files",
    ]
    for name, text in contents.items():
        lines.extend(
            [
                f"### `{name}`",
                "",
                "```",
                text,
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def review_local_files(
    files: list[str],
    copilot_review: bool = False,
    token: str | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    """Reviews local files by building a markdown report and optional AI feedback."""
    if not files:
        raise ValueError("No local files were provided.")
    contents: dict[str, str] = {}
    for name in files:
        path = pathlib.Path(name)
        if not path.exists() or not path.is_file():
            raise ValueError(f"Unable to read local file '{name}'.")
        contents[name] = path.read_text(encoding="utf-8")
    markdown = build_local_files_review_markdown(contents)
    if copilot_review:
        if not token:
            raise ValueError(
                "A GitHub token (--token or GITHUB_TOKEN env var) "
                "is required for --copilot-review."
            )
        ai_text = _call_copilot_review(markdown, token, model)
        markdown = f"{markdown}\n\n## Copilot Review\n\n{ai_text}"
    return markdown


def main(argv: list[str] | None = None) -> int:
    """Command line entrypoint for local file reviews."""
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(description="Reviews local files and prints markdown.")
    parser.add_argument("files", nargs="+", help="Local files to review")
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN") or None,
        help=(
            "GitHub personal access token. "
            "Resolution order: flag > GITHUB_TOKEN env var > unauthenticated."
        ),
    )
    parser.add_argument(
        "--copilot-review",
        action="store_true",
        default=False,
        help=(
            "Use GitHub Copilot (via GitHub Models API) to generate an AI review. "
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
        markdown = review_local_files(
            files=args.files,
            copilot_review=args.copilot_review,
            token=args.token,
            model=args.model,
        )
    except (HTTPError, URLError, OSError, UnicodeDecodeError, ValueError) as e:
        print(f"Unable to review local files ({type(e).__name__}).", file=sys.stderr)
        return 1
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
