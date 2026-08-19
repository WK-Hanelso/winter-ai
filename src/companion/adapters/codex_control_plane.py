"""Shared host-side Codex authentication helpers."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


def copy_control_plane_auth(source: Path, destination: Path) -> None:
    """Copy only Codex provider authentication into an ephemeral control home."""

    auth = source / "auth.json"
    if auth.is_file() and not auth.is_symlink():
        shutil.copy2(auth, destination / "auth.json")


def control_plane_environment(codex_home: Path) -> dict[str, str]:
    """Keep provider transport variables while excluding ambient secrets."""

    allowed = {
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_FILE",
        "TERM",
        "USER",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
    environment = {
        key: value for key, value in os.environ.items() if key in allowed
    }
    environment["CODEX_HOME"] = str(codex_home)
    return environment
