"""Shared GitHub Models API helpers."""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any
from urllib import request

from .review_token import CONFIG_FILE

MODELS_API_URL = "https://models.inference.ai.azure.com"
DEFAULT_MODEL = "openai/gpt-4o-mini"
LOGS_DIR = CONFIG_FILE.parent / "logs"


def _send_chat_request(
    messages: list[dict[str, str]],
    token: str,
    model: str = DEFAULT_MODEL,
    models_url: str = MODELS_API_URL,
    command_name: str = "review-pr",
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
    try:
        _log_copilot_request_and_answer(payload, result, command_name=command_name)
    except OSError:
        pass
    return content


def _log_copilot_request_and_answer(
    payload: dict[str, Any],
    result: dict[str, Any],
    logs_dir: pathlib.Path = LOGS_DIR,
    now: datetime.datetime | None = None,
    command_name: str = "review-pr",
) -> None:
    """Logs Copilot request/answer JSON payloads to timestamped files."""
    if now is None:
        now = datetime.datetime.now()
    log_folder = (
        logs_dir / f"{now:%Y}" / f"{now:%m}" / f"week-{now.isocalendar().week:02d}" / command_name
    )
    log_folder.mkdir(parents=True, exist_ok=True)
    timestamp = f"{now:%Y-%m-%d_%H-%M-%S}"
    (log_folder / f"{timestamp}_request.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    (log_folder / f"{timestamp}_answer.json").write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )
