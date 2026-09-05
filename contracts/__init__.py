"""Contract + boundary tests that gate every prar-agent submodule bump.

* ``test_boundary.py`` — the GPL-boundary guard (no platform module imports engine
  internals; engine wire paths stay confined to the single adapter). Stdlib-only.
* ``test_prar_contract.py`` — binds the real v0.1.0 HTTP surface and the D1 /
  PENDING tripwires. Requires the ``dev`` extra (pytest, respx) and core httpx.
"""
