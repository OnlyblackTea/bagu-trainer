"""Platform-side engine orchestration.

This package is the *only* place Paper Platform reaches prar-agent, and it does so
exclusively over the engine's out-of-process HTTP/WS API via :mod:`prar_bridge`.
It never imports engine internals — the GPL boundary (design §6.6, NOTICE) that
``contracts/test_boundary.py`` enforces.
"""

from .prar_bridge import (
    ENGINE_PINNED_TAG,
    BridgeTransportError,
    ContractPendingError,
    D1ForbiddenError,
    EngineAPIError,
    EngineWSError,
    PrarBridge,
    PrarBridgeError,
)
from .session_pool import AssessmentKey, PooledSession, SessionPool

__all__ = [
    "ENGINE_PINNED_TAG",
    "AssessmentKey",
    "BridgeTransportError",
    "ContractPendingError",
    "D1ForbiddenError",
    "EngineAPIError",
    "EngineWSError",
    "PooledSession",
    "PrarBridge",
    "PrarBridgeError",
    "SessionPool",
]
