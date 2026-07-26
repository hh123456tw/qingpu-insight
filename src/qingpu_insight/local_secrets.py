from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

_SUPPORTED_KEYS = frozenset({"QINGPU_GEMINI_API_KEY"})

_LINE_RE = re.compile(r"^(QINGPU_GEMINI_API_KEY)=(.+)$", re.ASCII)

_MAX_VALUE_LEN = 4096
_BAD_CHARS = re.compile(r"[\n\0]")


class SecretValidationError(ValueError):
    pass


class LocalSecretsStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def status(self) -> dict[str, bool]:
        configured = self._read_value("QINGPU_GEMINI_API_KEY") is not None
        return {"gemini_configured": configured}

    def set_gemini_key(self, key: str) -> None:
        _validate_value(key)
        lines = self._read_lines()
        new_lines = [ln for ln in lines if not ln.startswith("QINGPU_GEMINI_API_KEY=")]
        new_lines.append(f"QINGPU_GEMINI_API_KEY={key}\n")
        self._write_lines(new_lines)

    def delete_gemini_key(self) -> None:
        lines = self._read_lines()
        new_lines = [ln for ln in lines if not ln.startswith("QINGPU_GEMINI_API_KEY=")]
        if len(new_lines) != len(lines):
            self._write_lines(new_lines)

    def merged_env(self, base: Mapping[str, str]) -> dict[str, str]:
        env = dict(base)
        value = self._read_value("QINGPU_GEMINI_API_KEY")
        if value is not None:
            env["QINGPU_GEMINI_API_KEY"] = value
        return env

    def _read_lines(self) -> list[str]:
        if not self._path.exists():
            return []
        return self._path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

    def _read_value(self, name: str) -> str | None:
        if not self._path.exists():
            return None
        for line in self._path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _LINE_RE.match(line)
            if m and m.group(1) == name:
                return m.group(2)
        return None

    def _write_lines(self, lines: list[str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        tmp.replace(self._path)


def _validate_value(value: str) -> None:
    if not value:
        raise SecretValidationError("value must not be empty")
    if len(value) > _MAX_VALUE_LEN:
        raise SecretValidationError(
            f"value exceeds {_MAX_VALUE_LEN} characters"
        )
    if _BAD_CHARS.search(value):
        raise SecretValidationError("value contains invalid characters")
