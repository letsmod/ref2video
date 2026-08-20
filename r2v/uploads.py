"""Remembering which local file already lives at which remote URL.

Uploading is the slow part of a run and an uploaded asset is provider-specific -
a Segmind storage URL is useless to fal - so the cache is keyed by provider and
then by a hash of the file's contents.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_UPLOAD_CACHE = Path(".upload_cache.json")
LEGACY_UPLOAD_CACHE = Path(".segmind_upload_cache.json")


def file_fingerprint(path: Path) -> str:
    """SHA-256 of the file contents, used as the cache key.

    Hashing content rather than the path means a renamed or moved file still
    hits the cache, and an edited file misses it automatically.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cache(path: Path) -> dict:
    """Load the provider-keyed cache, migrating the old flat Segmind file if found.

    The v1 tool stored a flat {hash: entry} map that was implicitly Segmind's.
    Rather than throw those uploads away, they are adopted under the 'segmind'
    key the first time the new cache is written.
    """
    if not path.is_file() and LEGACY_UPLOAD_CACHE.is_file():
        legacy = _read_json(LEGACY_UPLOAD_CACHE)
        if legacy:
            return {"segmind": legacy}

    cache = _read_json(path)
    # Defend against a v1 file having been copied to the new name.
    if cache and all(isinstance(v, dict) and "url" in v for v in cache.values()):
        return {"segmind": cache}
    return cache


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}  # a damaged cache costs an upload, never the run


def save_cache(path: Path, cache: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # the cache is an optimisation; never fail a run over it


def cache_lookup(cache: dict, provider: str, fingerprint: str) -> str | None:
    entry = cache.get(provider, {}).get(fingerprint)
    if isinstance(entry, dict):
        return entry.get("url")
    return None


def cache_store(cache: dict, provider: str, fingerprint: str, path: Path, url: str) -> None:
    cache.setdefault(provider, {})[fingerprint] = {
        "url": url,
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def cache_forget(cache: dict, provider: str, fingerprint: str) -> None:
    cache.get(provider, {}).pop(fingerprint, None)
