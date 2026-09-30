"""Errors raised while handling API requests, independent of Flask."""

from __future__ import annotations


class ApiInputError(Exception):
    """Invalid client input; rendered as ``400`` with field-level codes."""

    def __init__(
        self,
        message: str,
        fields: dict[str, str] | None = None,
        *,
        code: str = "invalid_request",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.fields = fields or {}
        self.code = code


class MarketDataUnavailable(Exception):
    """The market or listing data store could not be read; rendered as ``503``."""
