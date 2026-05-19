"""Local files review command line."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from urllib.error import HTTPError, URLError

from .review_pr import CONFIG_FILE, DEFAULT_MODEL, _call_copilot_review, _load_cache, _save_cache


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
    extra_prompts: list[str] | None = None,
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
        ai_text = _call_copilot_review(
            markdown, token, model, extra_prompts=extra_prompts, command_name="review-local"
        )
        markdown = f"{markdown}\n\n## Copilot Review\n\n{ai_text}"
    return markdown


def _build_parser(token_default: str | None = None) -> argparse.ArgumentParser:
    """Build the argument parser for the ``review-local`` command.

    :param token_default: Default value for the ``--token`` argument
        (e.g. resolved from environment variables or cache file).
    :return: Configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        prog="review-local",
        description="Reviews local files and prints markdown.",
    )
    parser.add_argument("files", nargs="+", metavar="file", help="Local files to review")
    parser.add_argument(
        "--token",
        default=token_default,
        help=(
            "GitHub personal access token. "
            "Resolution order: flag > GITHUB_TOKEN env var > cached value "
            f"({CONFIG_FILE}) > unauthenticated."
        ),
    )
    parser.add_argument(
        "--save",
        action="store_true",
        default=False,
        help=(
            f"Save the resolved --token to {CONFIG_FILE} so it is used automatically "
            "in future invocations. The file is created with owner-only read "
            "permissions (0600)."
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
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Print progress information to stderr.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Command line entrypoint for local file reviews."""
    if argv is None:
        argv = sys.argv[1:]
    cache = _load_cache()
    token_default = os.environ.get("GITHUB_TOKEN") or cache.get("token") or None
    parser = _build_parser(token_default=token_default)
    args = parser.parse_args(argv)
    if args.save and args.token:
        _save_cache({"token": args.token})
    if args.verbose:
        print(f"review-local: reading {len(args.files)} file(s)...", file=sys.stderr)
    try:
        markdown = review_local_files(
            files=args.files,
            copilot_review=args.copilot_review,
            token=args.token,
            model=args.model,
            extra_prompts=args.extra_prompts,
        )
    except (HTTPError, URLError, OSError, UnicodeDecodeError, ValueError) as e:
        print(f"Unable to review local files ({type(e).__name__}).", file=sys.stderr)
        return 1
    if args.verbose:
        print("review-local: done.", file=sys.stderr)
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
