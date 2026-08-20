"""Segmind: synchronous generation, and the only provider that tells us the bill.

The model endpoint holds the connection open for the whole generation (1-4
minutes) and answers with the finished MP4 as raw bytes, with the amount
actually charged in the `x-cost` response header.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path

import requests

from ..errors import R2VError
from .base import (GENERATION_TIMEOUT_S, UPLOAD_TIMEOUT_S, GenerationResult, Provider,
                   http_error_detail)

API_BASE = "https://api.segmind.com/v1"
UPLOAD_ENDPOINT = "https://workflows-api.segmind.com/upload-asset"


class SegmindProvider(Provider):
    key = "segmind"
    label = "Segmind"
    env_var = "SEGMIND_API_KEY"
    key_example = "SG_xxxxxxxxxxxx"
    key_url = "https://cloud.segmind.com/console/api-keys"
    reports_cost = True

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    # -- uploads ------------------------------------------------------------ #

    def upload(self, path: Path, mime_type: str) -> str:
        """Base64 the file into Segmind Storage and return the asset URL."""
        mime = mimetypes.guess_type(path.name)[0] or mime_type
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        data_url = f"data:{mime};base64,{encoded}"

        try:
            response = requests.post(
                UPLOAD_ENDPOINT, headers=self._headers(),
                json={"data_urls": [data_url]}, timeout=UPLOAD_TIMEOUT_S,
            )
        except requests.RequestException as exc:
            raise R2VError(f"Upload of {path.name} to Segmind failed: {exc}") from exc

        if response.status_code != 200:
            raise R2VError(
                f"Upload of {path.name} failed with HTTP {response.status_code}: "
                f"{http_error_detail(response)}"
            )
        try:
            urls = _extract_uploaded_urls(response.json())
        except ValueError as exc:
            raise R2VError(f"Upload of {path.name} returned non-JSON: {response.text[:300]}") from exc
        if not urls:
            raise R2VError(f"Upload of {path.name} returned no URL: {response.text[:300]}")
        return urls[0]

    # -- generation --------------------------------------------------------- #

    def generate(self, offering, quality: str, payload: dict, log) -> GenerationResult:
        endpoint = f"{API_BASE}/{offering.endpoint_slug(quality)}"
        log(f"POST {endpoint}")
        log("Waiting on a synchronous call; this usually takes 1-4 minutes...")
        try:
            response = requests.post(endpoint, headers=self._headers(), json=payload,
                                     timeout=GENERATION_TIMEOUT_S)
        except requests.Timeout as exc:
            raise R2VError(
                f"Generation timed out after {GENERATION_TIMEOUT_S}s. The job may still "
                f"have been billed; check your Segmind dashboard before retrying."
            ) from exc
        except requests.RequestException as exc:
            raise R2VError(f"Generation request failed: {exc}") from exc

        if response.status_code != 200:
            hint = {
                401: " (check that SEGMIND_API_KEY is set and valid)",
                406: " (insufficient balance - Segmind reserves ~$1.90 up front, "
                     "regardless of what this specific clip costs)",
                429: " (rate limited, retry in a moment)",
            }.get(response.status_code, "")
            raise R2VError(
                f"Generation failed with HTTP {response.status_code}{hint}: "
                f"{http_error_detail(response)}"
            )

        video_bytes = self._unpack(response)
        cost = _parse_billed_cost(response.headers)
        return GenerationResult(
            video_bytes=video_bytes,
            cost_usd=cost,
            cost_source="x-cost header (billed)" if cost is not None else "",
        )

    def _unpack(self, response: requests.Response) -> bytes:
        """Raw MP4 bytes, or follow a URL if the endpoint returned an envelope."""
        if "application/json" not in response.headers.get("content-type", ""):
            if not response.content:
                raise R2VError("Segmind returned an empty body instead of a video.")
            return response.content

        body = response.json()
        video_url = body.get("video_url") or body.get("url") or body.get("output")
        if isinstance(video_url, dict):
            video_url = video_url.get("url")
        if not isinstance(video_url, str):
            raise R2VError(f"Expected video bytes or a URL, got: {json.dumps(body)[:400]}")
        return self.download(video_url)


def _extract_uploaded_urls(body: object) -> list[str]:
    """Pull asset URLs out of an upload-asset response.

    Segmind documents a 'urls' key but the live API answers with 'file_urls',
    so accept either, then fall back to any list of http strings rather than
    failing on an upload that actually succeeded.
    """
    if not isinstance(body, dict):
        return []

    def as_urls(value: object) -> list[str]:
        if isinstance(value, str):
            return [value] if value.startswith("http") else []
        if isinstance(value, list):
            return [i for i in value if isinstance(i, str) and i.startswith("http")]
        return []

    for key in ("file_urls", "urls", "asset_urls", "url"):
        found = as_urls(body.get(key))
        if found:
            return found
    for value in body.values():
        found = as_urls(value)
        if found:
            return found
    return []


def _parse_billed_cost(headers) -> float | None:
    """Pull the real billed amount out of the response headers.

    `x-cost` is the documented header; the aliases are checked too because
    header naming has drifted across Segmind's model endpoints before.
    """
    for name in ("x-cost", "x-credits-used", "x-remaining-credits-used"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            return float(str(raw).strip().lstrip("$"))
        except ValueError:
            continue
    return None
