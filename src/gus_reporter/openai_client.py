from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

MAX_RETRY_BACKOFF_SECONDS = 60.0


def extract_usage(response: dict[str, Any], model: str) -> dict[str, Any]:
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    return {
        "model": str(response.get("model") or model),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cached_input_tokens": int(input_details.get("cached_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
    }


class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        request_timeout_seconds: int | None = 180,
        request_max_retries: int = 3,
        request_retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.request_timeout_seconds = (
            request_timeout_seconds if request_timeout_seconds and request_timeout_seconds > 0 else None
        )
        self.request_max_retries = max(0, request_max_retries)
        self.request_retry_backoff_seconds = max(0.1, request_retry_backoff_seconds)

    def structured_response(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system_prompt: str,
        user_prompt: str = "",
        user_content: list[dict[str, Any]] | None = None,
        max_output_tokens: int = 4000,
        background: bool = True,
    ) -> dict[str, Any]:
        payload, _usage = self.structured_response_with_usage(
            schema_name=schema_name,
            schema=schema,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_content=user_content,
            max_output_tokens=max_output_tokens,
            background=background,
        )
        return payload

    def structured_response_with_usage(
        self,
        *,
        schema_name: str,
        schema: dict[str, Any],
        system_prompt: str,
        user_prompt: str = "",
        user_content: list[dict[str, Any]] | None = None,
        max_output_tokens: int = 4000,
        background: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Same as structured_response, plus the token usage the API billed."""
        if user_content is None:
            user_content = [{"type": "input_text", "text": user_prompt}]
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": max_output_tokens,
        }
        if background:
            payload["background"] = True
            payload["store"] = True
        response = self._request("POST", "/responses", payload)
        if background:
            response = self._poll_response(response)
        refusal = self._extract_refusal(response)
        if refusal:
            raise RuntimeError(f"OpenAI refused structured request: {refusal}")
        text = self._extract_output_text(response)
        try:
            return json.loads(text), extract_usage(response, self.model)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI response was not valid JSON: {text}") from exc

    def _poll_response(self, response: dict[str, Any]) -> dict[str, Any]:
        response_id = str(response.get("id", "")).strip()
        if not response_id:
            raise RuntimeError("OpenAI background response did not return an id.")
        status = str(response.get("status", ""))
        poll_delay_seconds = 1.0
        while status in {"queued", "in_progress"}:
            time.sleep(poll_delay_seconds)
            response = self._request("GET", f"/responses/{response_id}")
            status = str(response.get("status", ""))
            poll_delay_seconds = min(poll_delay_seconds * 1.5, 5.0)
        if status != "completed":
            raise RuntimeError(
                f"OpenAI response {response_id} finished with status '{status}': "
                f"{response.get('error') or response.get('incomplete_details')}"
            )
        return response

    def _extract_refusal(self, response: dict[str, Any]) -> str:
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    return str(content.get("refusal", "")).strip()
        return ""

    def _extract_output_text(self, response: dict[str, Any]) -> str:
        if isinstance(response.get("output_text"), str) and response["output_text"].strip():
            return response["output_text"]
        chunks: list[str] = []
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                content_type = content.get("type")
                if content_type in {"output_text", "text"} and isinstance(
                    content.get("text"), str
                ):
                    chunks.append(content["text"])
        text = "".join(chunks).strip()
        if not text:
            raise RuntimeError(f"OpenAI response did not include output text: {response}")
        return text

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
        url = f"{self.base_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        for attempt in range(self.request_max_retries + 1):
            request = urllib.request.Request(
                url,
                data=body,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                if self.request_timeout_seconds is None:
                    response_context = urllib.request.urlopen(request)
                else:
                    response_context = urllib.request.urlopen(
                        request,
                        timeout=self.request_timeout_seconds,
                    )
                with response_context as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if self._is_retryable_http_status(exc.code) and attempt < self.request_max_retries:
                    time.sleep(self._backoff_seconds(attempt))
                    continue
                raise RuntimeError(
                    f"OpenAI request failed: {method} {url} -> {exc.code} {detail}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < self.request_max_retries:
                    time.sleep(self._backoff_seconds(attempt))
                    continue
                raise RuntimeError(
                    f"OpenAI request failed: {method} {url} -> {str(exc.reason).strip()}"
                ) from exc

    def _backoff_seconds(self, attempt: int) -> float:
        return min(self.request_retry_backoff_seconds * (2**attempt), MAX_RETRY_BACKOFF_SECONDS)

    def _is_retryable_http_status(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500
