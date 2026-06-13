"""Domain error hierarchy + single source of truth for status mapping.

Every domain error declares the HTTP status (API) and CLI exit code it maps to,
plus a stable ``slug``. The API registers one exception handler for
:class:`EvalGateError` and the CLI runs commands through one helper, so a raised
domain error is translated to a status code in exactly one place — instead of
the per-route ``try/except ... raise HTTPException`` ladders and per-command
``try/except ... return {"_status": N}`` blocks that used to repeat (and drift)
across the API and CLI.

Concrete errors live next to the code that raises them (the repositories);
they subclass :class:`EvalGateError` *and* their original builtin base
(``LookupError`` / ``ValueError`` / ``RuntimeError``) so existing
``except LookupError`` / ``pytest.raises(ValueError)`` call sites keep working.
This module imports nothing from the rest of the package, so the lowest layer
stays cycle-free.
"""

from __future__ import annotations


class EvalGateError(Exception):
    """Base for all EvalGate domain errors.

    Subclasses override the three class attributes; the defaults treat an
    un-annotated error as a generic 400 / exit-1 failure.
    """

    http_status: int = 400
    exit_code: int = 1
    slug: str = "error"

    def payload(self) -> dict[str, str]:
        """``{"error": slug, "detail": message}`` — the shape both the API
        error body and the CLI JSON output use."""
        return {"error": self.slug, "detail": str(self)}
