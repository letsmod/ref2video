"""fal: upload to signed storage, submit to the queue, poll, fetch the result.

Two fal-specific traps are handled here. Uploads are a two-step dance - ask for
a signed URL, then PUT the bytes to it without an auth header. And the queue's
status and result URLs drop the model id's sub-path, so
`bytedance/seedance-2.5/reference-to-video` is polled at
`bytedance/seedance-2.5/requests/{id}`. Rather than rebuild those URLs we use
the ones the submit response hands back.
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

from ..errors import R2VError
from .base import (GenerationResult, POLL_INTERVAL_S, POLL_TIMEOUT_S, Provider,
                   UPLOAD_TIMEOUT_S, http_error_detail)

QUEUE_BASE = "https://queue.fal.run"
STORAGE_INITIATE = "https://rest.fal.ai/storage/upload/initiate"


class FalProvider(Provider):
    key = "fal"
    label = "fal"
    env_var = "FAL_KEY"
    key_example = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:xxxxxxxxxxxx"
    key_url = "https://fal.ai/dashboard/keys"
    reports_cost = False

    def _headers(self) -> dict:
        return {"Authorization": f"Key {self.api_key}"}

    # -- uploads ------------------------------------------------------------ #

    def upload(self, path: Path, mime_type: str) -> str:
        """Initiate a signed upload, PUT the bytes, return the public file URL."""
        try:
            # fal used to accept storage_type=gcs; it now 400s with
            # "Invalid storage type". The SDKs and docs use fal-cdn-v3.
            initiated = requests.post(
                STORAGE_INITIATE,
                params={"storage_type": "fal-cdn-v3"},
                headers={**self._headers(), "Content-Type": "application/json",
                         "Accept": "application/json"},
                json={"file_name": path.name, "content_type": mime_type},
                timeout=120,
            )
        except requests.RequestException as exc:
            raise R2VError(f"Could not start the fal upload for {path.name}: {exc}") from exc

        if not initiated.ok:
            raise R2VError(
                f"fal refused the upload of {path.name} with HTTP {initiated.status_code}: "
                f"{http_error_detail(initiated)}"
            )
        body = initiated.json()
        upload_url, file_url = body.get("upload_url"), body.get("file_url")
        if not upload_url or not file_url:
            raise R2VError(f"fal returned no upload URL for {path.name}: {initiated.text[:300]}")

        try:
            # The signed URL carries its own credentials; adding ours breaks it.
            put = requests.put(upload_url, data=path.read_bytes(),
                               headers={"Content-Type": mime_type}, timeout=UPLOAD_TIMEOUT_S)
        except requests.RequestException as exc:
            raise R2VError(f"Upload of {path.name} to fal storage failed: {exc}") from exc
        if put.status_code >= 400:
            raise R2VError(
                f"Upload of {path.name} failed with HTTP {put.status_code}: {put.text[:300]}"
            )
        return file_url

    # -- generation --------------------------------------------------------- #

    def generate(self, offering, quality: str, payload: dict, log) -> GenerationResult:
        model = offering.endpoint_slug(quality)
        submit_url = f"{QUEUE_BASE}/{model}"
        log(f"POST {submit_url}")
        try:
            response = requests.post(
                submit_url,
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload, timeout=120,
            )
        except requests.RequestException as exc:
            raise R2VError(f"Could not reach fal: {exc}") from exc

        # Any 2xx queued the job, and a queued job is already billable.
        if not response.ok:
            hint = {
                401: " (check that FAL_KEY is set and valid)",
                402: " (insufficient balance on your fal account)",
                404: f" (no model '{model}' - it may have been renamed)",
                422: " (the model rejected these inputs)",
                429: " (rate limited or at your concurrency cap)",
            }.get(response.status_code, "")
            raise R2VError(
                f"fal rejected the request with HTTP {response.status_code}{hint}: "
                f"{http_error_detail(response)}"
            )

        submitted = response.json()
        request_id = submitted.get("request_id")
        status_url = submitted.get("status_url")
        response_url = submitted.get("response_url")
        if not status_url or not response_url:
            raise R2VError(f"fal did not return queue URLs: {response.text[:300]}")

        log(f"Queued as {request_id}; polling until it finishes...")
        self._poll(status_url, log)

        try:
            result = requests.get(response_url, headers=self._headers(), timeout=120)
        except requests.RequestException as exc:
            raise R2VError(f"Could not fetch the fal result: {exc}") from exc
        if result.status_code != 200:
            raise R2VError(
                f"fal returned HTTP {result.status_code} for the finished job: "
                f"{http_error_detail(result)}"
            )

        body = result.json()
        if body.get("error"):
            raise R2VError(f"fal reported the job failed: {body.get('error')}")
        video = body.get("video")
        url = video.get("url") if isinstance(video, dict) else video
        if not isinstance(url, str):
            raise R2VError(f"fal returned no video URL: {str(body)[:300]}")

        log("Downloading the result...")
        units = result.headers.get("X-Fal-Billable-Units") or response.headers.get(
            "X-Fal-Billable-Units")
        return GenerationResult(
            video_bytes=self.download(url),
            cost_usd=None,   # fal reports billable units, not dollars
            cost_source="",
            request_id=request_id,
            extra={"billable_units": units} if units else {},
        )

    def _poll(self, status_url: str, log) -> None:
        deadline = time.time() + POLL_TIMEOUT_S
        last_state = None
        while True:
            try:
                response = requests.get(status_url, headers=self._headers(), timeout=60)
            except requests.RequestException as exc:
                raise R2VError(f"Lost contact with fal while polling: {exc}") from exc
            if response.status_code >= 400:
                raise R2VError(
                    f"fal returned HTTP {response.status_code} while polling: "
                    f"{http_error_detail(response)}"
                )

            status = response.json()
            state = status.get("status")
            if status.get("error"):
                raise R2VError(f"fal reported the job failed: {status.get('error')}")
            if state == "COMPLETED":
                return
            if state != last_state:
                position = status.get("queue_position")
                suffix = f" (position {position})" if position is not None else ""
                log(f"  {state}{suffix}")
                last_state = state
            if time.time() > deadline:
                raise R2VError(
                    f"Gave up waiting after {POLL_TIMEOUT_S // 60} minutes. The job may still "
                    f"finish and be billed - check https://fal.ai/dashboard/requests"
                )
            time.sleep(POLL_INTERVAL_S)
