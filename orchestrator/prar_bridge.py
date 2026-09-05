"""The ONLY module in Paper Platform that knows prar-agent's wire API.

Every platform-side call to the engine funnels through here, over the engine's
out-of-process HTTP/WebSocket API. No symbol from prar-agent's Python internals
(``core/*``, ``llm/*``, ``memory/*``, ``tools/*``) is ever imported — that
arm's-length boundary is what keeps the two as separate programs (design §6.6,
NOTICE) and is guarded by ``contracts/test_boundary.py``.

Two disciplines are encoded structurally, not by convention:

* **Stable vs planned.** Methods bound to endpoints that exist in the pinned tag
  (``v0.1.0``) are real. Capabilities that depend on a prar-agent-side contract
  freeze that has NOT happened yet — mentor-mode session, ACTING-without-Executor,
  AI-PR-review entry — raise :class:`ContractPendingError`. They are never
  invented ahead of the engine.
* **D1 (role reversal).** The AI never runs the Executor over the graded business
  code. The engine's ``ws_act`` "execute" path drives the Executor LLM, so it is
  deliberately NOT bridged; :meth:`PrarBridge.run_act_executor` refuses loudly.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import websockets

# Pinned engine tag this contract surface was read from. Bumping it is an
# explicit submodule bump gated by contracts/test_prar_contract.py — never a
# silent sync (design §6.6).
ENGINE_PINNED_TAG = "v0.1.0"


class PrarBridgeError(Exception):
    """Base class for every error raised by the adapter."""


class BridgeTransportError(PrarBridgeError):
    """The engine could not be reached (connection refused, timeout, DNS)."""


class EngineAPIError(PrarBridgeError):
    """The engine returned a non-2xx HTTP response."""

    def __init__(self, method: str, path: str, status_code: int, detail: Any) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{method} {path} -> {status_code}: {detail}")


class EngineWSError(PrarBridgeError):
    """The engine sent a WebSocket ``error`` frame."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"ws error [{code}]: {message}")


class ContractPendingError(PrarBridgeError):
    """A planned engine capability that is NOT frozen in the pinned tag.

    Raised instead of inventing an endpoint. The matching prar-agent contract
    must land, the submodule be bumped, and the contract test updated before this
    becomes a real call.
    """


class D1ForbiddenError(PrarBridgeError):
    """A call that would violate D1 (AI must not act on the graded business code)."""


def _extract_detail(resp: httpx.Response) -> Any:
    try:
        body = resp.json()
    except ValueError:
        return resp.text
    if isinstance(body, dict) and "detail" in body:
        return body["detail"]
    return body


class PrarBridge:
    """Async client for the prar-agent HTTP/WS contract surface (tag v0.1.0)."""

    def __init__(
        self,
        base_url: str,
        *,
        ws_base_url: str | None = None,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._ws_base_url = ws_base_url.rstrip("/") if ws_base_url else None
        self._timeout = timeout
        self._client_obj = client
        self._owns_client = client is None

    # ---- lifecycle -------------------------------------------------------

    async def __aenter__(self) -> PrarBridge:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client_obj is not None and self._owns_client:
            await self._client_obj.aclose()
        self._client_obj = None

    def _client(self) -> httpx.AsyncClient:
        if self._client_obj is None:
            self._client_obj = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client_obj

    def _ws_endpoint(self, path: str) -> str:
        if self._ws_base_url:
            return self._ws_base_url + path
        base = self._base_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return base + path

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            resp = await self._client().request(method, path, json=json_body, params=params)
        except httpx.HTTPError as e:
            raise BridgeTransportError(f"{method} {path} failed: {e}") from e
        if resp.status_code >= 400:
            raise EngineAPIError(method, path, resp.status_code, _extract_detail(resp))
        if resp.status_code == 204:
            return None
        return resp.json()

    # ---- sessions (api/sessions.py) --------------------------------------

    async def create_session(self, *, init_request: str, adapter_id: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/sessions",
            json_body={"init_request": init_request, "adapter_id": adapter_id},
        )

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{session_id}")

    async def get_current_plan(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{session_id}/plan")

    async def list_plans(self, session_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{session_id}/plans")

    async def get_plan_version(self, session_id: str, version: int) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{session_id}/plans/{version}")

    async def answer_decision(
        self, session_id: str, decision_id: str, *, answer: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/sessions/{session_id}/decisions/{decision_id}",
            json_body={"answer": answer},
        )

    async def advance_to_acting(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/sessions/{session_id}/advance-to-acting")

    async def request_rerun(self, session_id: str, *, step_id: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/api/sessions/{session_id}/rerun", json_body={"step_id": step_id}
        )

    async def complete_session(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/sessions/{session_id}/complete")

    async def merge_plan(self, session_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/sessions/{session_id}/merge")

    # ---- comments (api/comments.py) --------------------------------------

    async def create_comment(
        self,
        session_id: str,
        *,
        anchor_id: str,
        plan_version: int,
        quote: str,
        body: str,
        quote_context: str = "",
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/sessions/{session_id}/comments",
            json_body={
                "anchor_id": anchor_id,
                "plan_version": plan_version,
                "quote": quote,
                "quote_context": quote_context,
                "body": body,
            },
        )

    async def list_comments(self, session_id: str, *, plan_version: int) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            f"/api/sessions/{session_id}/comments",
            params={"plan_version": plan_version},
        )

    async def get_comment(self, session_id: str, comment_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/sessions/{session_id}/comments/{comment_id}")

    # ---- adapters (api/adapters.py) --------------------------------------

    async def list_adapters(self, *, only_active: bool = True) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/adapters", params={"only_active": only_active})

    async def get_adapter(self, adapter_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/adapters/{adapter_id}")

    async def create_adapter(
        self,
        *,
        name: str,
        provider: str,
        model: str,
        credentials_env: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/adapters",
            json_body={
                "name": name,
                "provider": provider,
                "model": model,
                "credentials_env": credentials_env or {},
                "params": params or {},
            },
        )

    async def update_adapter(self, adapter_id: str, **fields: Any) -> dict[str, Any]:
        return await self._request("PUT", f"/api/adapters/{adapter_id}", json_body=fields)

    async def delete_adapter(self, adapter_id: str, *, hard: bool = False) -> None:
        await self._request("DELETE", f"/api/adapters/{adapter_id}", params={"hard": hard})

    async def set_default_adapter(self, adapter_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/adapters/{adapter_id}/set-default")

    # ---- providers (api/providers.py) ------------------------------------

    async def list_providers(self) -> dict[str, Any]:
        return await self._request("GET", "/api/providers")

    # ---- memories (api/memories.py) --------------------------------------

    async def create_memory(
        self,
        *,
        kind: str,
        content: str,
        importance: float = 0.5,
        user_id: str | None = None,
        source_session: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "content": content, "importance": importance}
        if user_id is not None:
            body["user_id"] = user_id
        if source_session is not None:
            body["source_session"] = source_session
        return await self._request("POST", "/api/memories", json_body=body)

    async def search_memories(
        self,
        *,
        query: str,
        limit: int = 5,
        kinds: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "limit": limit}
        if kinds is not None:
            body["kinds"] = list(kinds)
        return await self._request("POST", "/api/memories/search", json_body=body)

    async def consolidate_memories(self) -> dict[str, Any]:
        return await self._request("POST", "/api/memories/consolidate")

    # ---- websocket: plan generation (api/ws_plan.py) ---------------------

    async def stream_plan_generation(
        self,
        session_id: str,
        *,
        init_request: str,
        adapter_id: str,
        available_tools: Sequence[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream plan generation for a session.

        Server event sequence: ``plan.start`` -> ``plan.node`` x N -> ``plan.done``.
        An ``error`` frame raises :class:`EngineWSError`. ``available_tools`` is the
        one real v0.1.0 lever for restricting the tool surface at plan time; the
        full mentor-mode whitelist is still PENDING (see below).
        """
        url = self._ws_endpoint(f"/api/ws/sessions/{session_id}/plan")
        payload: dict[str, Any] = {
            "type": "generate",
            "init_request": init_request,
            "adapter_id": adapter_id,
        }
        if available_tools is not None:
            payload["available_tools"] = list(available_tools)

        async with websockets.connect(url) as ws:
            await ws.send(json.dumps(payload))
            while True:
                event = json.loads(await ws.recv())
                if event.get("type") == "error":
                    raise EngineWSError(
                        str(event.get("code", "unknown")), str(event.get("message", ""))
                    )
                yield event
                if event.get("type") == "plan.done":
                    break

    # ---- D1 guard: the Executor path is never bridged --------------------

    async def run_act_executor(self, *args: Any, **kwargs: Any) -> None:
        """Refused. The engine's ``ws_act`` "execute" runs the Executor LLM over the
        plan's steps — i.e. the AI writing/running the graded business code, which
        D1 forbids. Paper Platform never bridges it. In mentor mode the human is the
        actor; the platform stays in the plan/review phases (generate, comment,
        merge) and never advances to an Executor run on ``files_to_change``.
        """
        raise D1ForbiddenError(
            "D1: the AI must never run the Executor over the graded business code; "
            "the ws_act 'execute' path is intentionally not bridged."
        )

    # ---- PENDING: contracts not yet frozen in prar-agent v0.1.0 ----------

    async def open_mentor_session(self, *args: Any, **kwargs: Any) -> None:
        """PENDING. A session mode with write/execute tools disabled for
        ``files_to_change``. Requires a prar-agent-side contract freeze; not in
        v0.1.0. Approximate today by staying in the plan/review phases and passing a
        restricted ``available_tools`` to :meth:`stream_plan_generation`.
        """
        raise ContractPendingError(
            "mentor-mode session (write/execute disabled for files_to_change) is "
            f"PENDING a prar-agent contract freeze; not in {ENGINE_PINNED_TAG}."
        )

    async def advance_to_acting_without_executor(self, *args: Any, **kwargs: Any) -> None:
        """PENDING. ACTING phase that does NOT invoke the Executor LLM (human acts).
        Not in v0.1.0; ``advance-to-acting`` there implies a subsequent Executor run.
        """
        raise ContractPendingError(
            "ACTING-without-Executor session mode is PENDING a prar-agent contract "
            f"freeze; not in {ENGINE_PINNED_TAG}."
        )

    async def submit_for_ai_review(self, *args: Any, **kwargs: Any) -> None:
        """PENDING. AI-PR-review entry (the reviewer role over a submitted diff).
        Not in v0.1.0. Until it lands, AI Review is advisory and produced outside the
        engine's frozen surface; AcceptanceDecision stays the deterministic gate.
        """
        raise ContractPendingError(
            "AI-PR-review entry is PENDING a prar-agent contract freeze; not in "
            f"{ENGINE_PINNED_TAG}."
        )
