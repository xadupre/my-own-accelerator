"""Token cache and argument helpers shared by review commands."""

from __future__ import annotations

import json
import pathlib
from typing import Any

CONFIG_FILE = pathlib.Path.home() / ".config" / "moa" / "review_pr.json"
VALUE_FLAGS = {"--token", "--api-url", "--model", "--user", "--prompt"}


def _load_cache() -> dict[str, Any]:
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


def _save_cache(data: dict[str, Any]) -> None:
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


def _project_token_cache_key(owner: str, repo: str) -> str:
    """Build cache key for a repository-specific token."""
    return f"{owner}/{repo}"


def _resolve_cached_token(
    cache: dict[str, Any], owner: str | None, repo: str | None
) -> str | None:
    """Resolve cached token with per-project preference and legacy fallback."""
    if owner and repo:
        project_tokens = cache.get("project_tokens")
        if isinstance(project_tokens, dict):
            token = project_tokens.get(_project_token_cache_key(owner, repo))
            if isinstance(token, str) and token:
                return token
    token = cache.get("token")
    return token if isinstance(token, str) and token else None


def _build_project_token_cache(
    cache: dict[str, Any], owner: str, repo: str, token: str
) -> dict[str, str]:
    """Return updated project token map preserving existing entries."""
    existing = cache.get("project_tokens")
    project_tokens = dict(existing) if isinstance(existing, dict) else {}
    project_tokens[_project_token_cache_key(owner, repo)] = token
    return project_tokens


def _resolve_token_origin(
    argv: list[str],
    token: str | None,
    env_token: str | None,
    cache: dict[str, Any],
    owner: str | None = None,
    repo: str | None = None,
) -> tuple[str, str]:
    """Resolve token source and type for verbose CLI reporting."""
    if not token:
        return ("none", "none")
    if "--token" in argv:
        return ("--token", "explicit")
    if env_token and token == env_token:
        return ("GITHUB_TOKEN", "explicit")
    if owner and repo:
        project_tokens = cache.get("project_tokens")
        if isinstance(project_tokens, dict):
            project_token = project_tokens.get(_project_token_cache_key(owner, repo))
            if isinstance(project_token, str) and project_token and token == project_token:
                return (f"{CONFIG_FILE} ({owner}/{repo})", "fine-grained")
    classic_token = cache.get("token")
    if isinstance(classic_token, str) and classic_token and token == classic_token:
        return (str(CONFIG_FILE), "classic")
    return ("unknown", "explicit")


def _extract_owner_repo(argv: list[str]) -> tuple[str | None, str | None]:
    """Extract owner/repo positionals while skipping option values."""
    positionals: list[str] = []
    skip_next = False
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if item in VALUE_FLAGS:
            skip_next = True
            continue
        if item.startswith("-"):
            continue
        positionals.append(item)
        if len(positionals) >= 2:
            break
    if len(positionals) < 2:
        return None, None
    return positionals[0], positionals[1]


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
    positional_indices: list[int] = []
    skip_next = False
    for i, item in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if item in VALUE_FLAGS:
            skip_next = True
        elif item.startswith("-"):
            pass
        else:
            positional_indices.append(i)
    if len(positional_indices) == 2:
        insert_at = positional_indices[0]
        return list(argv[:insert_at]) + [user] + list(argv[insert_at:])
    return argv
