"""Locating and validating the API keys for each provider.

Keys live in a .env file beside the project so a run needs no shell setup, but
a real exported environment variable always wins - that is what makes CI and
one-off overrides work without editing the file.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import R2VError

DEFAULT_ENV_FILE = ".env"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Substrings that mean the .env file was copied but never filled in. Catching
# these locally turns a confusing HTTP 401 into a clear "edit your .env".
PLACEHOLDER_KEY_HINTS = ("your_key", "your-key", "yourkey", "replace", "changeme",
                         "xxxx", "sk_here", "paste")


def load_env_file(explicit_path: Path | None = None) -> Path | None:
    """Read KEY=VALUE lines from a .env file into os.environ.

    Hand-rolled rather than depending on python-dotenv to keep the dependency
    list at one line. Values already present in the real environment win.
    Returns the file that was loaded, or None if there was none to load.
    """
    if explicit_path is not None:
        candidate = explicit_path.expanduser()
        if not candidate.is_file():
            raise R2VError(f"--env-file not found: {candidate}")
        candidates = [candidate]
    else:
        # Look next to the project first so the command works from any directory.
        candidates = [PROJECT_ROOT / DEFAULT_ENV_FILE, Path.cwd() / DEFAULT_ENV_FILE]

    for candidate in candidates:
        if not candidate.is_file():
            continue
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):  # tolerate shell-style .env files
                line = line[len("export "):].lstrip()
            key, separator, value = line.partition("=")
            if not separator:
                continue
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)
        return candidate
    return None


def is_placeholder_key(api_key: str) -> bool:
    """True if the key still looks like the sample value from .env.example."""
    lowered = api_key.lower()
    return any(hint in lowered for hint in PLACEHOLDER_KEY_HINTS)


def has_key(env_var: str) -> bool:
    """True if a usable (non-placeholder) key is present for this variable."""
    value = os.environ.get(env_var, "").strip()
    return bool(value) and not is_placeholder_key(value)


def resolve_api_key(env_var: str, provider_label: str, signup_url: str,
                    example: str, env_file: Path | None) -> str:
    """Return a usable API key for one provider, or explain how to get one."""
    api_key = os.environ.get(env_var, "").strip()
    if api_key and not is_placeholder_key(api_key):
        return api_key

    target = env_file or (PROJECT_ROOT / DEFAULT_ENV_FILE)
    problem = "still holds a placeholder value" if api_key else "is not set"
    raise R2VError(
        f"{provider_label} needs {env_var}, which {problem}.\n"
        f"  Add it to {target}:\n"
        f"      {env_var}={example}\n"
        f"  Get a key from {signup_url}\n"
        f"  (An exported shell variable of the same name takes precedence.)"
    )
