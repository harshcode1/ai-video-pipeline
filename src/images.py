"""ImageProvider abstraction. GeminiProvider is the live impl.

PollinationsProvider is kept below as DEAD CODE for reference only -- it went
paid in Aug 2026 (see the long note in config.py). Do not resurrect it without
re-testing: it returns cached images for previously-seen prompts, so a casual
spot-check can look like it still works while every new prompt fails.
"""
import abc
import base64
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

import config


class ImageProvider(abc.ABC):
    @abc.abstractmethod
    def fetch(self, prompt: str, out_path: Path) -> None:
        raise NotImplementedError


class MissingAPIKeyError(RuntimeError):
    """Raised with a human-readable fix, not a stack trace, when no key is set."""


class ImageRequestRejectedError(RuntimeError):
    """A malformed request (HTTP 400). Deterministic -- retrying cannot help.

    Bypasses the retry loop for the same reason MissingAPIKeyError does: six
    identical failures separated by exponential backoff is ~3 wasted minutes
    and buries the one line of response body that explains the problem.
    """


class ImageQuotaError(RuntimeError):
    """HTTP 429 where the quota for this model is structurally ZERO rather
    than temporarily used up -- i.e. the account has no billing enabled and
    image generation has no free tier (see the note in config.py).

    Distinct from an ordinary rate-limit 429, which IS transient and is still
    retried. Waiting never clears this one, so surface the billing fix instead
    of sleeping through six attempts.
    """


class _SwitchedMimeType(Exception):
    """Internal signal, not a failure: the API rejected the mime type and we
    have another to try. Retried immediately, without consuming an attempt or
    sleeping, since the next request is a different request."""


def _error_details(resp) -> tuple[str, str, str]:
    """Pull (status, reason, message) out of a Google API error body.

    Two shapes have to be handled. This endpoint returns its errors wrapped in
    a single-element JSON ARRAY -- verified live -- while the rest of the API
    returns a bare object. Naive `payload["error"]` works on one and raises on
    the other, so normalize both before reading.
    """
    try:
        payload = resp.json()
    except Exception:
        return "", "", resp.text[:300]
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    if not isinstance(err, dict):
        return "", "", resp.text[:300]
    reason = ""
    for detail in err.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reason = detail["reason"]
            break
    return err.get("status", ""), reason, err.get("message", "") or resp.text[:300]


def _is_zero_quota(resp) -> bool:
    """True when a 429 means "this account may never call this model" rather
    than "slow down". A QuotaFailure violation reports the limit it hit, and an
    account with no billing reports that limit as exactly 0."""
    compact = re.sub(r"\s+", "", resp.text.lower())
    if '"quotavalue":"0"' in compact or '"quota_limit_value":"0"' in compact:
        return True
    return "freetier" in compact or "billing" in compact


def _extract_image_bytes(payload) -> bytes | None:
    """Pull the base64 image out of the response.

    The REST response shape for the /interactions endpoint is not documented
    (Google's examples only show the SDK accessor `interaction.output_image.data`),
    so rather than hard-coding one guessed path we walk the JSON for the first
    plausible base64 image blob. This survives the shape differing from, or
    changing out from under, the docs.
    """
    key_names = ("data", "b64_json", "bytes", "image_bytes", "inline_data", "inlineData")

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in key_names and isinstance(v, str) and len(v) > 1000:
                    try:
                        return base64.b64decode(v, validate=True)
                    except Exception:
                        pass
                found = walk(v)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    return walk(payload)


class GeminiProvider(ImageProvider):
    """Google Gemini image generation -- free tier, key from Google AI Studio.

    Generates natively at 16:9 / 4K (3840x2160), which is why the render no
    longer needs Real-ESRGAN: the source already exceeds the 1080p output.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get(config.IMAGE_API_KEY_ENV, "").strip()
        if not self.api_key:
            raise MissingAPIKeyError(
                f"No image API key found. Set the {config.IMAGE_API_KEY_ENV} environment "
                f"variable to a key from https://aistudio.google.com/apikey\n"
                f"  PowerShell (this session):  $env:{config.IMAGE_API_KEY_ENV} = 'your-key'\n"
                f"  PowerShell (permanent):     setx {config.IMAGE_API_KEY_ENV} \"your-key\""
            )
        self._last_request_time = 0.0
        self.mime_type = config.IMAGE_MIME_TYPE
        self._tried_mime_types = {self.mime_type}

    def _throttle(self):
        wait = config.IMAGE_MIN_INTERVAL_SECONDS - (time.time() - self._last_request_time)
        if wait > 0:
            time.sleep(wait)

    def _switch_mime_type(self) -> bool:
        """Move to the next candidate mime type. Returns False once exhausted."""
        alt = next((m for m in config.IMAGE_MIME_TYPES if m not in self._tried_mime_types), None)
        if alt is None:
            return False
        print(f"    Image API rejected mime_type={self.mime_type!r}; retrying as {alt!r}")
        self.mime_type = alt
        self._tried_mime_types.add(alt)
        return True

    def _check_response(self, resp) -> None:
        """Classify a non-200 into fail-fast vs. worth-retrying.

        The distinction matters a lot here: a render is a multi-hour job, and
        six exponential-backoff retries of a deterministic failure both waste
        three minutes and discard the response body that says what to fix.
        """
        if resp.status_code == 200:
            return

        status, reason, message = _error_details(resp)
        low = message.lower()

        # VERIFIED LIVE: this endpoint answers a bad key with 400
        # INVALID_ARGUMENT / API_KEY_INVALID, NOT 401 or 403. Checking only for
        # 401/403 (as this code used to) meant the single most common setup
        # mistake fell through to the generic retry path and was reported as an
        # unexplained failure six attempts later.
        if reason == "API_KEY_INVALID" or "api key not valid" in low:
            raise MissingAPIKeyError(
                f"The image API rejected the key in {config.IMAGE_API_KEY_ENV} as invalid. "
                f"Get a fresh one at https://aistudio.google.com/apikey then run: "
                f'setx {config.IMAGE_API_KEY_ENV} "your-key" (and open a NEW terminal). '
                f"Detail: {message[:200]}"
            )
        if resp.status_code in (401, 403):
            raise MissingAPIKeyError(
                f"Image API rejected the key (HTTP {resp.status_code}). Check that "
                f"{config.IMAGE_API_KEY_ENV} holds a valid key from "
                f"https://aistudio.google.com/apikey -- detail: {message[:300]}"
            )

        if resp.status_code == 429:
            if _is_zero_quota(resp):
                raise ImageQuotaError(
                    f"This account's quota for {config.IMAGE_MODEL} is zero, so waiting will "
                    f"not help. Image generation has no free tier as of Aug 2026 -- billing "
                    f"must be enabled on the Google Cloud project behind the key "
                    f"(https://aistudio.google.com/apikey -> the key's project -> enable "
                    f"billing). New accounts get $300 of credit, ~28 videos' worth. "
                    f"Detail: {message[:200]}"
                )
            raise RuntimeError(f"HTTP 429 rate limited: {message[:200]}")

        if resp.status_code == 400:
            # A 400 is deterministic: it will fail identically all six times.
            # raise_for_status() would also throw away the body explaining why
            # -- which is how the image/png vs image/jpeg mime mismatch stayed
            # invisible for a full retry cycle.
            if "mime" in low and self._switch_mime_type():
                raise _SwitchedMimeType()
            raise ImageRequestRejectedError(
                f"Image API rejected the request (HTTP 400, will not retry). "
                f"status={status or '?'} reason={reason or '?'} -- {message[:400]}"
            )

        raise RuntimeError(f"HTTP {resp.status_code}: {message[:300]}")

    def fetch(self, prompt: str, out_path: Path) -> None:
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

        last_err = None
        attempt = 0
        while attempt < config.IMAGE_MAX_RETRIES:
            attempt += 1
            body = {
                "model": config.IMAGE_MODEL,
                "input": [{"type": "text", "text": prompt}],
                "response_format": {
                    "type": "image",
                    "mime_type": self.mime_type,
                    "aspect_ratio": config.IMAGE_ASPECT_RATIO,
                    "image_size": config.IMAGE_SIZE,
                },
            }
            self._throttle()
            self._last_request_time = time.time()
            try:
                resp = requests.post(config.IMAGE_ENDPOINT, json=body, headers=headers, timeout=300)
                self._check_response(resp)

                data = _extract_image_bytes(resp.json())
                if not data or len(data) < 5000:
                    raise RuntimeError(
                        f"no usable image in response (got {len(data or b'')}B). "
                        f"Body starts: {resp.text[:300]}"
                    )
                out_path.write_bytes(data)
                return
            except _SwitchedMimeType:
                # Not a failure and not the same request -- retry at once and
                # don't spend one of the allotted attempts on it.
                attempt -= 1
                continue
            except (MissingAPIKeyError, ImageRequestRejectedError, ImageQuotaError):
                raise
            except Exception as e:
                last_err = e
                sleep_s = config.IMAGE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 2)
                print(f"    Image attempt {attempt}/{config.IMAGE_MAX_RETRIES} failed ({e}); retrying in {sleep_s:.1f}s")
                time.sleep(sleep_s)
        raise RuntimeError(f"Image fetch failed after {config.IMAGE_MAX_RETRIES} attempts: {last_err}")


class PollinationsProvider(ImageProvider):
    """DEAD as of Aug 2026 -- kept only to document what was tried.

    Two independent reasons not to bring this back (both verified live):
      * It bills now. New prompts return 402 PAYMENT_REQUIRED with a zero
        balance, and `flux` is gone from the anonymous model list.
      * Even when it worked it force-capped output at 1024x576 no matter what
        was requested, which was the actual source of the blurry images.

    Deliberately raises instead of running, so nobody wires it back in by
    accident after seeing a cached prompt appear to succeed.
    """

    MODEL = "flux"
    SEED = 424242
    MIN_INTERVAL_SECONDS = 15.0

    def __init__(self):
        raise NotImplementedError(
            "PollinationsProvider is retired: the service went paid and only ever "
            "returned 1024x576. Use GeminiProvider."
        )

    def fetch(self, prompt: str, out_path: Path) -> None:
        # Reference only -- this is the URL shape that used to work.
        encoded = quote(prompt, safe="")
        raise NotImplementedError(
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?model={self.MODEL}&seed={self.SEED}&nologo=true&width=1920&height=1080"
        )


class CloudflareWorkersAIProvider(ImageProvider):
    """STUB -- evaluated as a Pollinations replacement and REJECTED.

    Free tier is genuinely generous (10k neurons/day, ~230 flux images), but
    @cf/black-forest-labs/flux-1-schnell exposes no width/height parameter and
    emits square images. Cropping a square to 16:9 lands right back at
    1024x576 -- i.e. it would fix the outage but not the blurriness, which was
    the whole point. Documented here so it doesn't get re-proposed.
    """

    def __init__(self, account_id: str | None = None, api_token: str | None = None):
        raise NotImplementedError(
            "CloudflareWorkersAIProvider is a rejected stub: square-only output "
            "means no 16:9 resolution gain over the old Pollinations images."
        )

    def fetch(self, prompt: str, out_path: Path) -> None:
        raise NotImplementedError


def get_image(scene: dict, provider: ImageProvider) -> Path:
    out_path = config.ROOT / scene["image_file"]

    # Accept ANY extension for the cached file, not just the configured one.
    # The image format is a config value that has already changed once (png ->
    # jpeg), and a rendered image is expensive -- matching only the current
    # extension would silently re-buy every image in the video after a settings
    # change. Downstream stages read scene["image_file"], so point it at
    # whatever was actually found.
    for candidate in sorted(out_path.parent.glob(f"{scene['index']:02d}.*")):
        if candidate.is_file() and candidate.stat().st_size > 0:
            scene["image_file"] = candidate.relative_to(config.ROOT).as_posix()
            print(f"  [IMG] scene {scene['index']:02d} ({scene['timestamp']}) cached, skipping")
            return candidate

    print(f"  [IMG] scene {scene['index']:02d} ({scene['timestamp']})")
    provider.fetch(scene["image_prompt"], out_path)
    return out_path
