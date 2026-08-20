"""The interface every provider adapter implements.

A provider is responsible for four things and nothing else: saying how it is
authenticated, turning a local file into a URL the model can fetch, running a
request to completion, and reporting what it cost. Everything above this layer
(limits, prompts, pricing, logging) is provider-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import requests

from ..errors import R2VError

UPLOAD_TIMEOUT_S = 600
DOWNLOAD_TIMEOUT_S = 600
GENERATION_TIMEOUT_S = 900     # ceiling for a single synchronous HTTP call
POLL_TIMEOUT_S = 1800          # ceiling for a submit-and-poll job
POLL_INTERVAL_S = 3.0


@dataclass
class GenerationResult:
    video_bytes: bytes
    cost_usd: float | None = None
    cost_source: str = ""
    request_id: str | None = None
    extra: dict = field(default_factory=dict)


class Provider:
    """Base class. Subclasses fill in the class attributes and three methods."""

    key: str = ""
    label: str = ""
    env_var: str = ""
    key_example: str = ""
    key_url: str = ""
    # Whether this provider reports the real billed amount back to us.
    reports_cost: bool = False

    def __init__(self, api_key: str):
        self.api_key = api_key

    # -- to implement ------------------------------------------------------- #

    def upload(self, path: Path, mime_type: str) -> str:
        raise NotImplementedError

    def generate(self, offering, quality: str, payload: dict, log) -> GenerationResult:
        raise NotImplementedError

    # -- shared ------------------------------------------------------------- #

    def asset_alive(self, url: str) -> bool:
        """Check a cached upload URL still resolves before reusing it.

        No provider documents how long an uploaded asset lives, so rather than
        guessing a TTL we ask. A cheap HEAD beats re-uploading megabytes, and
        any doubt falls back to re-uploading.
        """
        try:
            response = requests.head(url, timeout=30, allow_redirects=True,
                                     headers=self._asset_headers())
            if response.status_code == 405:  # some CDNs reject HEAD
                response = requests.get(url, timeout=30, stream=True,
                                        headers={**self._asset_headers(),
                                                 "Range": "bytes=0-0"})
                response.close()
            return response.status_code < 400
        except requests.RequestException:
            return False

    def _asset_headers(self) -> dict:
        """Headers needed to reach this provider's own hosted files."""
        return {}

    def download(self, url: str) -> bytes:
        try:
            response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S,
                                    headers=self._asset_headers())
            response.raise_for_status()
        except requests.RequestException as exc:
            raise R2VError(f"Could not download the generated video from {url}: {exc}") from exc
        if not response.content:
            raise R2VError(f"The generated video at {url} was empty.")
        return response.content


def http_error_detail(response: requests.Response) -> str:
    """Best-effort readable error text from any provider's error response."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]

    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body.get("message")
        if isinstance(detail, list):        # fal's validation errors
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    location = ".".join(str(p) for p in item.get("loc", [])[1:])
                    parts.append(f"{location or 'input'}: {item.get('msg', '')}".strip())
            if parts:
                return "; ".join(parts)
        if isinstance(detail, str):
            return detail
        if isinstance(detail, dict):
            return str(detail)
    import json as _json
    return _json.dumps(body)[:500]
