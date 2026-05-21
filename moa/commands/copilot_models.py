"""Shared GitHub Copilot request helpers."""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
from collections.abc import Callable
from typing import Any

from copilot import CopilotClient
from copilot.generated.session_events import (
    AssistantMessageData,
    AssistantUsageData,
    SessionErrorData,
    SessionEvent,
    SessionIdleData,
    SessionModelChangeData,
    SessionStartData,
)
from copilot.session import CopilotSession, PermissionHandler

from .review_token import CONFIG_FILE

MODELS_API_URL = "https://models.inference.ai.azure.com"
DEFAULT_MODEL: str | None = None
FALLBACK_MODEL = "openai/gpt-4.1"
NO_MODEL_AVAILABLE_MESSAGE_PREFIX = "No model available."
LOGS_DIR = CONFIG_FILE.parent / "logs"


class CopilotSessionError(RuntimeError):
    """Structured Copilot session error surfaced by the SDK event stream."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code
        self.url = url


async def _send_copilot_prompts(
    prompts: list[str],
    token: str,
    model: str | None = DEFAULT_MODEL,
    models_url: str = MODELS_API_URL,
    command_name: str = "review-pr",
    system_prompt: str | None = None,
    on_model_used: Callable[[str], None] | None = None,
) -> list[str]:
    try:
        return await _send_copilot_prompts_once(
            prompts,
            token,
            model=model,
            models_url=models_url,
            command_name=command_name,
            system_prompt=system_prompt,
            on_model_used=on_model_used,
        )
    except CopilotSessionError as exc:
        if model is not None or not _is_no_model_available_error(exc):
            raise
        return await _send_copilot_prompts_once(
            prompts,
            token,
            model=FALLBACK_MODEL,
            models_url=models_url,
            command_name=command_name,
            system_prompt=system_prompt,
            on_model_used=on_model_used,
        )


async def _send_copilot_prompts_once(
    prompts: list[str],
    token: str,
    model: str | None = DEFAULT_MODEL,
    models_url: str = MODELS_API_URL,
    command_name: str = "review-pr",
    system_prompt: str | None = None,
    on_model_used: Callable[[str], None] | None = None,
) -> list[str]:
    provider: dict[str, Any] = {
        "type": "openai",
        "wire_api": "completions",
        "base_url": models_url.rstrip("/"),
        "bearer_token": token,
        "headers": {"User-Agent": "moa/review-pr"},
    }
    session_kwargs: dict[str, Any] = {
        "on_permission_request": PermissionHandler.approve_all,
        "provider": provider,
        "available_tools": [],
        "infinite_sessions": {"enabled": False},
    }
    if model:
        session_kwargs["model"] = model
    if system_prompt:
        session_kwargs["system_message"] = {"mode": "replace", "content": system_prompt}

    conversation_messages: list[dict[str, str]] = []

    responses: list[str] = []
    async with CopilotClient() as client:
        session = await client.create_session(**session_kwargs)
        seen_models: set[str] = set()

        def on_session_event(event: SessionEvent) -> None:
            model_name = _extract_model_name_from_event(event)
            if not model_name or model_name in seen_models:
                return
            seen_models.add(model_name)
            if on_model_used:
                on_model_used(model_name)

        unsubscribe = session.on(on_session_event)
        async with session:
            try:
                for prompt in prompts:
                    request_messages: list[dict[str, str]] = []
                    if system_prompt:
                        request_messages.append({"role": "system", "content": system_prompt})
                    request_messages.extend(conversation_messages)
                    request_messages.append({"role": "user", "content": prompt})
                    payload: dict[str, Any] = {"messages": request_messages}
                    if model:
                        payload["model"] = model
                    content = await _send_session_prompt(session, prompt)
                    try:
                        _log_copilot_request_and_answer(
                            payload,
                            {"choices": [{"message": {"content": content}}]},
                            command_name=command_name,
                        )
                    except OSError:
                        pass
                    conversation_messages.extend(
                        (
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": content},
                        )
                    )
                    responses.append(content)
            finally:
                unsubscribe()
    return responses


def _extract_model_name_from_event(event: SessionEvent) -> str | None:
    match event.data:
        case SessionStartData() as data:
            return data.selected_model
        case SessionModelChangeData() as data:
            return data.new_model
        case AssistantUsageData() as data:
            return data.model
    return None


def _is_no_model_available_error(error: CopilotSessionError) -> bool:
    # The SDK exposes status/error type metadata, but not a dedicated stable
    # machine-readable code for the "no default model available" condition.
    # Keep the check constrained to the structured CopilotSessionError and the
    # expected 400 response, then match the current SDK message prefix
    # ("No model available. ...") until the SDK exposes a stable code.
    if error.status_code is None:
        return False
    if error.status_code != 400:
        return False
    return str(error).startswith(NO_MODEL_AVAILABLE_MESSAGE_PREFIX)


async def _send_session_prompt(session: CopilotSession, prompt: str) -> str:
    response_content = ""
    session_error: Exception | None = None
    done = asyncio.Event()

    def on_event(event: SessionEvent) -> None:
        nonlocal response_content, session_error
        match event.data:
            case AssistantMessageData() as assistant_message:
                response_content = assistant_message.content
            case SessionErrorData() as error:
                session_error = CopilotSessionError(
                    error.message,
                    error_type=error.error_type,
                    status_code=error.status_code,
                    url=error.url,
                )
                done.set()
            case SessionIdleData():
                done.set()

    unsubscribe = session.on(on_event)
    try:
        await session.send(prompt)
        await done.wait()
    finally:
        unsubscribe()
    if session_error is not None:
        raise session_error
    if not response_content:
        raise ValueError("Empty content in AI model response.")
    return response_content


def _send_chat_request(
    messages: list[dict[str, str]],
    token: str,
    model: str | None = DEFAULT_MODEL,
    models_url: str = MODELS_API_URL,
    command_name: str = "review-pr",
    on_model_used: Callable[[str], None] | None = None,
    verbose: int = 0,
) -> str:
    """Send a chat completion request via the GitHub Copilot SDK.

    :param messages: List of message dicts with ``role`` and ``content`` keys.
    :param token: GitHub personal access token with models access.
    :param model: Optional model identifier accepted by the configured provider.
        When omitted or ``None``, Copilot chooses the default model automatically
        and falls back to ``openai/gpt-4.1`` if no default model is available.
    :param models_url: Base URL of the OpenAI-compatible provider.
    :return: Content string from the first choice in the API response.
    :raises ValueError: If the API returns no choices or empty content.
    :raises RuntimeError: If the Copilot session reports an error.
    """
    assert token, "Token is mandatory."

    system_prompt = "\n\n".join(
        message["content"]
        for message in messages
        if message.get("role") == "system" and message.get("content")
    )
    prompts = [message["content"] for message in messages if message.get("role") == "user"]
    if verbose:
        print(f"{command_name} with model={model!r}, prompts={', '.join(prompts)}")
    if not prompts:
        raise ValueError("At least one user message is required for a Copilot request.")
    return asyncio.run(
        _send_copilot_prompts(
            prompts,
            token,
            model=model,
            models_url=models_url,
            command_name=command_name,
            system_prompt=system_prompt or None,
            on_model_used=on_model_used,
        )
    )[-1]


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
