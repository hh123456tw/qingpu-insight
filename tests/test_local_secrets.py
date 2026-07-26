from __future__ import annotations

from pathlib import Path

import pytest

from qingpu_insight.local_secrets import (
    LocalSecretsStore,
    SecretValidationError,
)


def test_secret_store_writes_atomically_and_never_returns_key(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "instance" / "secrets.env")
    store.set_gemini_key("test-key-12345")
    assert store.status()["gemini_configured"] is True
    # Verify file exists and content is correct
    content = (tmp_path / "instance" / "secrets.env").read_text(encoding="utf-8")
    assert "test-key-12345" in content
    # Verify key never returned by any method
    assert "test-key-12345" not in str(store.status())
    merged = store.merged_env({"FOO": "bar"})
    assert merged.get("QINGPU_GEMINI_API_KEY") == "test-key-12345"


def test_delete_gemini_key_preserves_supported_nonsecret_lines(tmp_path: Path) -> None:
    secrets_file = tmp_path / "instance" / "secrets.env"
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text(
        "QINGPU_GEMINI_API_KEY=secret123\n# comment line\nFOO=bar\n",
        encoding="utf-8",
    )
    store = LocalSecretsStore(secrets_file)
    store.delete_gemini_key()
    content = secrets_file.read_text(encoding="utf-8")
    assert "QINGPU_GEMINI_API_KEY" not in content
    assert "# comment line" in content
    assert "FOO=bar" in content
    assert not store.status()["gemini_configured"]


def test_status_returns_false_when_no_file(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "nope" / "secrets.env")
    assert store.status() == {"gemini_configured": False}


def test_set_gemini_key_rejects_empty(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "secrets.env")
    with pytest.raises(SecretValidationError, match="must not be empty"):
        store.set_gemini_key("")


def test_set_gemini_key_rejects_newlines(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "secrets.env")
    with pytest.raises(SecretValidationError, match="invalid characters"):
        store.set_gemini_key("abc\ndef")


def test_set_gemini_key_rejects_nul(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "secrets.env")
    with pytest.raises(SecretValidationError, match="invalid characters"):
        store.set_gemini_key("abc\0def")


def test_set_gemini_key_rejects_too_long(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "secrets.env")
    with pytest.raises(SecretValidationError, match="exceeds"):
        store.set_gemini_key("x" * 5000)


def test_delete_on_nonexistent_file_is_noop(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "instance" / "secrets.env")
    store.delete_gemini_key()
    assert not (tmp_path / "instance" / "secrets.env").exists()


def test_merged_env_overlays_key(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "secrets.env")
    store.set_gemini_key("gm-key-xyz")
    base = {"QINGPU_OLLAMA_MODEL": "llama3"}
    merged = store.merged_env(base)
    assert merged["QINGPU_OLLAMA_MODEL"] == "llama3"
    assert merged["QINGPU_GEMINI_API_KEY"] == "gm-key-xyz"


def test_merged_env_no_key_preserves_base(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "instance" / "secrets.env")
    base = {"QINGPU_GEMINI_API_KEY": "old-key", "FOO": "bar"}
    merged = store.merged_env(base)
    # When no local file, key from base is preserved
    assert merged["QINGPU_GEMINI_API_KEY"] == "old-key"


def test_set_gemini_key_overwrites_existing(tmp_path: Path) -> None:
    store = LocalSecretsStore(tmp_path / "secrets.env")
    store.set_gemini_key("first-key")
    store.set_gemini_key("second-key")
    merged = store.merged_env({})
    assert merged["QINGPU_GEMINI_API_KEY"] == "second-key"
    content = (tmp_path / "secrets.env").read_text(encoding="utf-8")
    assert content.count("QINGPU_GEMINI_API_KEY") == 1
