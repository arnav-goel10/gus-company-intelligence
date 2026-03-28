from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        request_timeout_seconds: int | None = 180,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.request_timeout_seconds = (
            request_timeout_seconds if request_timeout_seconds and request_timeout_seconds > 0 else None
        )

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
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI response was not valid JSON: {text}") from exc

    def _poll_response(self, response: dict[str, Any]) -> dict[str, Any]:
        response_id = str(response.get("id", "")).strip()
        if not response_id:
            raise RuntimeError("OpenAI background response did not return an id.")
        status = str(response.get("status", ""))
        while status in {"queued", "in_progress"}:
            response = self._request("GET", f"/responses/{response_id}")
            status = str(response.get("status", ""))
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
            raise RuntimeError(
                f"OpenAI request failed: {method} {url} -> {exc.code} {detail}"
            ) from exc
