"""Replicate: create a prediction, then poll it to completion.

Two things shape this adapter. `Prefer: wait` exists but is capped at 60
seconds, so it can only ever be an opportunistic head start on a video model -
polling is mandatory. And Replicate reports no cost at all, in the body or the
headers, so the figure we log is always our own estimate from the rate card.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from ..errors import R2VError
from .base import (GenerationResult, POLL_INTERVAL_S, POLL_TIMEOUT_S, Provider,
                   UPLOAD_TIMEOUT_S, http_error_detail)

API_BASE = "https://api.replicate.com/v1"
TERMINAL_STATES = {"succeeded", "failed", "canceled", "aborted"}
MAX_UPLOAD_MB = 100


class ReplicateProvider(Provider):
    key = "replicate"
    label = "Replicate"
    env_var = "REPLICATE_API_TOKEN"
    key_example = "r8_xxxxxxxxxxxx"
    key_url = "https://replicate.com/account/api-tokens"
    reports_cost = False

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _asset_headers(self) -> dict:
        # Replicate-hosted files (including our own uploads) want the token.
        return self._headers()

    # -- uploads ------------------------------------------------------------ #

    def upload(self, path: Path, mime_type: str) -> str:
        """Push the file through the Files API and return its canonical URL.

        The returned `urls.get` value is what models take as input; Replicate
        resolves it internally, so it does not need to be publicly reachable.
        """
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            raise R2VError(
                f"{path.name} is {size_mb:.1f} MB; Replicate's Files API caps uploads "
                f"at {MAX_UPLOAD_MB} MB. Use Segmind or fal for this file, or shrink it."
            )
        try:
            with path.open("rb") as handle:
                response = requests.post(
                    f"{API_BASE}/files", headers=self._headers(),
                    files={"content": (path.name, handle, mime_type)},
                    data={"filename": path.name, "type": mime_type},
                    timeout=UPLOAD_TIMEOUT_S,
                )
        except requests.RequestException as exc:
            raise R2VError(f"Upload of {path.name} to Replicate failed: {exc}") from exc

        if not response.ok:
            raise R2VError(
                f"Upload of {path.name} failed with HTTP {response.status_code}: "
                f"{http_error_detail(response)}"
            )
        url = (response.json().get("urls") or {}).get("get")
        if not url:
            raise R2VError(f"Upload of {path.name} returned no URL: {response.text[:300]}")
        return url

    # -- generation --------------------------------------------------------- #

    def generate(self, offering, quality: str, payload: dict, log) -> GenerationResult:
        model = offering.endpoint_slug(quality)
        log(f"POST {API_BASE}/predictions  ({model})")
        try:
            response = requests.post(
                f"{API_BASE}/predictions",
                headers={**self._headers(), "Content-Type": "application/json",
                         "Prefer": "wait=60"},
                json={"version": model, "input": payload},
                timeout=120,
            )
        except requests.RequestException as exc:
            raise R2VError(f"Could not reach Replicate: {exc}") from exc

        # Any 2xx means the prediction was created and is now billable: 201 is
        # documented, 200 comes back when `Prefer: wait` catches a fast finish,
        # and 202 shows up for queued official models. Treating one of those as
        # a failure abandons a job that still runs and still charges.
        if not response.ok:
            hint = {
                401: " (check that REPLICATE_API_TOKEN is set and valid)",
                402: " (insufficient credit on your Replicate account)",
                404: f" (no model '{model}' - it may have been renamed)",
                422: " (the model rejected these inputs)",
            }.get(response.status_code, "")
            raise R2VError(
                f"Replicate rejected the request with HTTP {response.status_code}{hint}: "
                f"{http_error_detail(response)}"
            )

        prediction = response.json()
        prediction = self._poll(prediction, log)

        output = prediction.get("output")
        url = output if isinstance(output, str) else next(
            (item for item in (output or []) if isinstance(item, str)), None)
        if not url:
            raise R2VError(f"Replicate returned no video URL. Status: {prediction.get('status')}")

        log("Downloading the result (Replicate deletes outputs after an hour)...")
        return GenerationResult(
            video_bytes=self.download(url),
            cost_usd=None,   # Replicate exposes no cost in the API
            cost_source="",
            request_id=prediction.get("id"),
            extra={"metrics": prediction.get("metrics") or {}},
        )

    def _poll(self, prediction: dict, log) -> dict:
        """Poll until terminal, or until the output shows up.

        `Prefer: wait` can hand back a populated `output` while `status` is still
        `processing`, so waiting for a terminal status alone would stall.
        """
        deadline = time.time() + POLL_TIMEOUT_S
        url = (prediction.get("urls") or {}).get("get")
        announced = False

        while prediction.get("status") not in TERMINAL_STATES:
            if prediction.get("output"):
                break
            if not url:
                raise R2VError("Replicate did not return a polling URL for this prediction.")
            if time.time() > deadline:
                raise R2VError(
                    f"Gave up waiting after {POLL_TIMEOUT_S // 60} minutes. The job may still "
                    f"finish and be billed - check https://replicate.com/p/{prediction.get('id')}"
                )
            if not announced:
                log(f"Queued as {prediction.get('id')}; polling until it finishes...")
                announced = True
            time.sleep(POLL_INTERVAL_S)
            try:
                prediction = requests.get(url, headers=self._headers(), timeout=60).json()
            except (requests.RequestException, ValueError) as exc:
                raise R2VError(f"Lost contact with Replicate while polling: {exc}") from exc

        if prediction.get("status") in ("failed", "aborted", "canceled"):
            raise R2VError(
                f"Replicate reported the prediction {prediction.get('status')}: "
                f"{prediction.get('error') or 'no detail given'}"
            )
        return prediction
