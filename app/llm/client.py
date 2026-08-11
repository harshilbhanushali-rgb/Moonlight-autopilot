from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Generic, TypeVar

import httpx
from openai import (
    APIConnectionError,
    AsyncOpenAI,
    ContentFilterFinishReasonError,
    LengthFinishReasonError,
)
from pydantic import BaseModel, ValidationError

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


@dataclass
class LLMMessage:
    role: str
    content: str


@dataclass
class StructuredLLMResponse(Generic[ResponseModelT]):
    """A schema-validated response plus the raw text it came from.

    `content` is kept for explainability — it is persisted as the step's
    raw_response so a stored row can always be traced back to what the model
    actually said.

    `usage` is the gateway's token accounting when it sent any, and None
    otherwise (the stub never has it). Nothing in the batch path reads it — it
    exists so cost/latency questions like the reasoning_effort A/B can be
    answered with measurements instead of estimates.
    """

    parsed: ResponseModelT
    content: str
    usage: object | None = None


class StructuredOutputError(Exception):
    """The gateway could not return output conforming to the requested schema.

    Raised for a refusal, a response truncated by the token limit, a
    content-filter stop, or (in the stub) a canned response that doesn't
    validate. Callers in app.domain translate this into LLMOutputError so the
    step is recorded as failed and retried — never substitute a fallback.
    """

    def __init__(self, detail: str, raw_content: str = ""):
        self.detail = detail
        self.raw_content = raw_content
        super().__init__(detail)


class StubLLMClient:
    """Deterministic canned-response client for tests and offline development.

    Canned responses stay raw JSON strings and are validated through the same
    Pydantic model the real gateway is constrained by, so a test fixture that
    doesn't match the schema fails the same way a bad live response would.

    `complete_structured` is `async` to match the real client's interface — the
    domain layer awaits it, so a sync stub would not be substitutable.
    """

    def __init__(self, responses: dict[str, str]):
        self._responses = responses
        self.calls: list[RecordedCall] = []

    async def complete_structured(
        self,
        *,
        messages: list[LLMMessage],
        response_model: type[ResponseModelT],
        response_key: str | None = None,
        generation_name: str | None = None,
    ) -> StructuredLLMResponse[ResponseModelT]:
        self.calls.append(RecordedCall(messages=messages, response_key=response_key))
        raw = self._responses[response_key]
        try:
            parsed = response_model.model_validate_json(raw)
        except ValidationError as exc:
            raise StructuredOutputError(
                f"canned response does not match {response_model.__name__}: {exc}",
                raw_content=raw,
            ) from exc
        return StructuredLLMResponse(parsed=parsed, content=raw)


@dataclass
class RecordedCall:
    messages: list[LLMMessage]
    response_key: str | None


class OpenAICompatibleLLMClient:
    """Client for the internal LLM gateway (OpenAI-compatible /chat/completions
    interface, e.g. proxying Gemini). Owns its own retry loop on timeout
    rather than the SDK's (max_retries=0 on the underlying client) so each
    attempt gets a fresh generation_id under one stable trace_id, matching
    this gateway's tracing convention used elsewhere at Joveo.

    Every call is schema-constrained: there is no unconstrained completion
    method, because an unconstrained call against this gateway intermittently
    returns ```json-fenced output that no strict parser accepts.

    Async because the analyser runs several calls concurrently — one coroutine
    per call-under-analysis, each still stepping through its four analyser
    steps in order. The underlying `AsyncOpenAI` client is bound to whichever
    event loop first uses it, so do not share one instance across loops; build
    it inside the loop that will use it (see app/services/batch/run.py).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
        environment: str = "dev",
        trace_name: str = "moonlight_autopilot",
        temperature: float = 0.0,
        reasoning_effort: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._environment = environment
        self._trace_name = trace_name
        self._temperature = temperature
        self._reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=http_client,
            max_retries=0,
        )

    async def aclose(self) -> None:
        """Releases the underlying HTTP connection pool.

        Worth calling explicitly: the batch entrypoint builds a client per run
        inside its own event loop, and a pool left open when that loop closes
        logs unraisable-exception noise on shutdown.
        """
        await self._client.close()

    async def complete_structured(
        self,
        *,
        messages: list[LLMMessage],
        response_model: type[ResponseModelT],
        response_key: str | None = None,
        generation_name: str | None = None,
    ) -> StructuredLLMResponse[ResponseModelT]:
        generation_name = generation_name or response_key or "unknown"
        trace_id = str(uuid.uuid4())

        # Gemini's thinking level travels as `reasoning_effort`; the native
        # thinking_config/thinking_level shapes are silently dropped by this
        # gateway, so don't be tempted to send them in extra_body.
        extra_kwargs = {}
        if self._reasoning_effort is not None:
            extra_kwargs["reasoning_effort"] = self._reasoning_effort

        last_error: APIConnectionError | None = None
        for _ in range(self._max_retries + 1):
            try:
                completion = await self._client.chat.completions.parse(
                    model=self._model,
                    messages=[{"role": m.role, "content": m.content} for m in messages],
                    response_format=response_model,
                    temperature=self._temperature,
                    timeout=self._timeout_seconds,
                    **extra_kwargs,
                    extra_body={
                        "metadata": {
                            "trace_name": self._trace_name,
                            "trace_id": trace_id,
                            "generation_id": str(uuid.uuid4()),
                            "generation_name": generation_name,
                            "environment": self._environment,
                        }
                    },
                )
            except APIConnectionError as exc:
                # APIConnectionError, not just APITimeoutError (which subclasses
                # it). A bare "Connection error." with no timeout involved was
                # observed hitting several concurrent requests at once and then
                # succeeding immediately on a repeat, so it is transient — but it
                # used to escape this loop and fail the step on the first blip
                # with no retry at all. Concurrency makes that more likely simply
                # by having more requests in flight, and because the analyser's
                # circuit breaker counts connection failures as evidence the
                # gateway is down, a few simultaneous blips could abandon a whole
                # nightly batch.
                #
                # APIStatusError is deliberately still NOT retried here: it does
                # not subclass APIConnectionError, and an HTTP 5xx means the
                # gateway answered, so it belongs on the per-step failure path
                # where an operator can see it.
                last_error = exc
                continue
            except LengthFinishReasonError as exc:
                # Not retried in-client: at temperature 0 a retry truncates
                # identically. Fail the step so it's visible and the caller's
                # retry/dead-letter policy decides.
                raise StructuredOutputError(
                    "response truncated by the token limit before the schema was satisfied"
                ) from exc
            except ContentFilterFinishReasonError as exc:
                raise StructuredOutputError("response stopped by the content filter") from exc

            message = completion.choices[0].message
            raw_content = message.content or ""

            refusal = getattr(message, "refusal", None)
            if refusal:
                raise StructuredOutputError(f"model refused: {refusal}", raw_content=raw_content)
            if message.parsed is None:
                raise StructuredOutputError(
                    "gateway returned no schema-conforming output", raw_content=raw_content
                )

            return StructuredLLMResponse(
                parsed=message.parsed,
                content=raw_content,
                usage=getattr(completion, "usage", None),
            )

        raise last_error
