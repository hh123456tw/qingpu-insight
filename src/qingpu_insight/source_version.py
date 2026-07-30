from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceVersionProvider:
    """Fixed source version, primarily useful for deterministic tests."""

    commit: str
    dirty: bool

    def read(self) -> SourceVersionProvider:
        return self


class GitSourceVersionProvider:
    """Read Git provenance when a training execution actually starts."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def read(self) -> SourceVersionProvider:
        return read_git_source_version(self._root)


def read_git_source_version(root: Path) -> SourceVersionProvider:
    """Read conservative source provenance without requiring a Git checkout."""
    fallback = SourceVersionProvider(commit="unknown", dirty=True)
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return fallback

    commit = commit_result.stdout.strip()
    valid_commit = re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit)
    if commit_result.returncode != 0 or valid_commit is None:
        return fallback

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return SourceVersionProvider(commit=commit, dirty=True)

    if status_result.returncode != 0:
        return SourceVersionProvider(commit=commit, dirty=True)
    return SourceVersionProvider(commit=commit, dirty=bool(status_result.stdout.strip()))
