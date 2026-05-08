"""Activity wrapping Claude API calls — keeps all non-determinism out of workflow code."""
from datetime import timedelta

import anthropic
from temporalio import activity
from temporalio.exceptions import ApplicationError

from shared.models import CallClaudeInput, ClaudeResponse, ClaudeToolUse
from worker.config import ANTHROPIC_API_KEY


def _serialize_content(content) -> list[dict]:
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
    return result


def _build_client() -> anthropic.AsyncAnthropic:
    # max_retries=0: Temporal owns all retry/backoff, not the client library.
    return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=0)


def _retry_delay_from_headers(error: anthropic.APIStatusError) -> timedelta | None:
    retry_after = None
    try:
        retry_after = error.response.headers.get("retry-after") if error.response else None
    except Exception:
        pass
    if retry_after:
        try:
            return timedelta(seconds=float(retry_after))
        except ValueError:
            return None
    return None


@activity.defn
async def call_claude(input: CallClaudeInput) -> ClaudeResponse:
    client = _build_client()

    kwargs = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4096,
        "system": input.system,
        "messages": input.messages,
    }
    if input.tools:
        kwargs["tools"] = input.tools

    try:
        response = await client.messages.create(**kwargs)
    except anthropic.AuthenticationError as error:
        raise ApplicationError(
            f"Claude authentication failed: {error}",
            type="AuthenticationError",
            non_retryable=True,
        )
    except anthropic.PermissionDeniedError as error:
        raise ApplicationError(
            f"Claude permission denied: {error}",
            type="PermissionDeniedError",
            non_retryable=True,
        )
    except anthropic.BadRequestError as error:
        # Invalid inputs (tool schema issues, bad messages) — retrying won't help.
        raise ApplicationError(
            f"Claude request was rejected: {error}",
            type="BadRequestError",
            non_retryable=True,
        )
    except anthropic.RateLimitError as error:
        delay = _retry_delay_from_headers(error)
        raise ApplicationError(
            f"Claude rate limited: {error}",
            type="RateLimitError",
            next_retry_delay=delay,
        )
    except anthropic.APIStatusError as error:
        # 5xx — let Temporal retry. Client errors below 500 treated as permanent.
        if error.status_code and error.status_code >= 500:
            raise ApplicationError(
                f"Claude server error ({error.status_code}): {error}",
                type="ServerError",
            )
        raise ApplicationError(
            f"Claude client error ({error.status_code}): {error}",
            type="ClientError",
            non_retryable=True,
        )
    except anthropic.APIConnectionError as error:
        # Network hiccup — transient, Temporal retries.
        raise ApplicationError(
            f"Claude connection error: {error}",
            type="ConnectionError",
        )

    serialized = _serialize_content(response.content)
    text = next((block["text"] for block in serialized if block["type"] == "text"), "")
    tool_uses = [
        ClaudeToolUse(id=block["id"], name=block["name"], input=block["input"])
        for block in serialized
        if block["type"] == "tool_use"
    ]

    return ClaudeResponse(
        stop_reason=response.stop_reason,
        text=text,
        content=serialized,
        tool_uses=tool_uses,
    )
