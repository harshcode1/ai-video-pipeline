"""ImageProvider abstraction. Pick one with config.IMAGE_PROVIDER; build it
with make_provider().

  FalProvider     -- DEFAULT. FLUX.2 Turbo on fal.ai. 2048x1152, seeded,
                     ~$0.019/image.
  GeminiProvider  -- Fallback / quality benchmark. Up to 4K, no seed,
                     ~5-8x the price.

This module is deliberately over-abstracted for its size. Two providers died
inside the same month (Pollinations went paid; Google deleted its free image
tier), so the swap cost matters more here than anywhere else in the pipeline.

Every provider classifies failures into fail-fast (bad key, no credit,
malformed request) vs. worth-retrying (rate limit, 5xx). That distinction is
the whole point: a render is a multi-hour unattended job, so retrying a
deterministic failure six times just wastes minutes and buries the reason.

PollinationsProvider and CloudflareWorkersAIProvider below are DEAD CODE kept
as a record of what was tried and why it was rejected.
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

import cache
import config


class ImageProvider(abc.ABC):
    @abc.abstractmethod
    def fetch(self, prompt: str, out_path: Path) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def fingerprint(self) -> dict:
        """Every setting that can change the picture this provider returns for a
        given prompt (model, size, seed, ...) -- but never the API key. Part of
        the image's cache key, so changing a setting re-generates instead of
        silently reusing pictures made with the old one."""
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
        self.api_key = api_key or os.environ.get(config.GEMINI_API_KEY_ENV, "").strip()
        if not self.api_key:
            raise MissingAPIKeyError(
                f"No image API key found. Set the {config.GEMINI_API_KEY_ENV} environment "
                f"variable to a key from https://aistudio.google.com/apikey\n"
                f"  PowerShell (this session):  $env:{config.GEMINI_API_KEY_ENV} = 'your-key'\n"
                f"  PowerShell (permanent):     setx {config.GEMINI_API_KEY_ENV} \"your-key\""
            )
        self._last_request_time = 0.0
        self.mime_type = config.IMAGE_MIME_TYPE
        self._tried_mime_types = {self.mime_type}

    def fingerprint(self) -> dict:
        return {
            "provider": "gemini",
            "endpoint": config.IMAGE_ENDPOINT,
            "model": config.IMAGE_MODEL,
            "aspect_ratio": config.IMAGE_ASPECT_RATIO,
            "image_size": config.IMAGE_SIZE,
            "mime_type": config.IMAGE_MIME_TYPE,   # no seed: the API exposes none
        }

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
                f"The image API rejected the key in {config.GEMINI_API_KEY_ENV} as invalid. "
                f"Get a fresh one at https://aistudio.google.com/apikey then run: "
                f'setx {config.GEMINI_API_KEY_ENV} "your-key" (and open a NEW terminal). '
                f"Detail: {message[:200]}"
            )
        if resp.status_code in (401, 403):
            raise MissingAPIKeyError(
                f"Image API rejected the key (HTTP {resp.status_code}). Check that "
                f"{config.GEMINI_API_KEY_ENV} holds a valid key from "
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


def _extract_fal_image(payload) -> str | None:
    """Return the first image reference from a fal response.

    Shape is {"images": [{"url": ...}], "seed": N, "timings": {...}}. With
    sync_mode the url is a `data:` URI carrying the bytes inline; otherwise
    it's a CDN link that still has to be downloaded.
    """
    if not isinstance(payload, dict):
        return None
    for item in payload.get("images") or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            return item["url"]
    return None


def _fal_error_message(resp) -> str:
    """fal reports errors as {"detail": "..."} for auth/runtime problems and
    {"detail": [{"loc": [...], "msg": ...}, ...]} for schema validation."""
    try:
        payload = resp.json()
    except Exception:
        return resp.text[:300]
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail[:400]
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in (item.get("loc") or []))
                parts.append(f"{loc}: {item.get('msg', '')}".strip(": "))
        if parts:
            return "; ".join(parts)[:400]
    return resp.text[:300]


class FalProvider(ImageProvider):
    """Black Forest Labs FLUX.2 Turbo, hosted on fal.ai. The default.

    Chosen over Gemini on two counts, not one: ~8x cheaper (~$1.30 vs $10.42
    for a 69-scene video), and it exposes a SEED, which restores the
    cross-scene style consistency that was lost when Pollinations died.

    Generates 2048x1152 -- true 16:9 and the largest this model allows. That
    is above the 1920x1080 output, so nothing is invented; only the last part
    of each zoom crops in past 1:1 (see ZOOM_MAX in config.py).
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get(config.FAL_API_KEY_ENV, "").strip()
        if not self.api_key:
            raise MissingAPIKeyError(
                f"No image API key found. Set the {config.FAL_API_KEY_ENV} environment "
                f"variable to a key from https://fal.ai/dashboard/keys\n"
                f"  PowerShell (this session):  $env:{config.FAL_API_KEY_ENV} = 'your-key'\n"
                f"  PowerShell (permanent):     setx {config.FAL_API_KEY_ENV} \"your-key\""
            )
        self._last_request_time = 0.0

    def fingerprint(self) -> dict:
        return {
            "provider": "fal",
            "endpoint": config.FAL_ENDPOINT,
            "width": config.FAL_IMAGE_WIDTH,
            "height": config.FAL_IMAGE_HEIGHT,
            "output_format": config.FAL_OUTPUT_FORMAT,
            "guidance_scale": config.FAL_GUIDANCE_SCALE,
            "safety_checker": config.FAL_ENABLE_SAFETY_CHECKER,
            "seed": config.IMAGE_SEED,
        }

    def _throttle(self):
        wait = config.IMAGE_MIN_INTERVAL_SECONDS - (time.time() - self._last_request_time)
        if wait > 0:
            time.sleep(wait)

    def _check_response(self, resp) -> None:
        if resp.status_code == 200:
            return
        message = _fal_error_message(resp)
        low = message.lower()

        if resp.status_code in (401, 403):
            raise MissingAPIKeyError(
                f"fal.ai rejected the key in {config.FAL_API_KEY_ENV} (HTTP "
                f"{resp.status_code}). Check it at https://fal.ai/dashboard/keys, then: "
                f'setx {config.FAL_API_KEY_ENV} "your-key" (and open a NEW terminal). '
                f"Detail: {message[:200]}"
            )
        # 402 is the explicit out-of-credit signal; some deployments phrase it
        # as a 403 mentioning balance. Either way, waiting cannot fix it.
        if resp.status_code == 402 or "balance" in low or "insufficient" in low:
            raise ImageQuotaError(
                f"fal.ai reports no remaining balance, so retrying will not help. "
                f"Top up at https://fal.ai/dashboard/billing -- a 69-scene video costs "
                f"about $1.30 at the current settings. Detail: {message[:200]}"
            )
        if resp.status_code == 422:
            # Schema validation -- deterministic, and the body names the field.
            raise ImageRequestRejectedError(
                f"fal.ai rejected the request (HTTP 422, will not retry). "
                f"Check the image_size/format settings in config.py -- {message}"
            )
        if resp.status_code == 429:
            raise RuntimeError(f"HTTP 429 rate limited: {message[:200]}")
        raise RuntimeError(f"HTTP {resp.status_code}: {message[:300]}")

    def _resolve_bytes(self, url: str) -> bytes:
        """A data: URI carries the image inline; anything else is a CDN link.
        Note fal's CDN links expire, so download immediately -- never cache the
        URL and fetch it later."""
        if url.startswith("data:"):
            _, _, encoded = url.partition(",")
            return base64.b64decode(encoded)
        dl = requests.get(url, timeout=300)
        dl.raise_for_status()
        return dl.content

    def fetch(self, prompt: str, out_path: Path) -> None:
        headers = {"Authorization": f"Key {self.api_key}",
                   "Content-Type": "application/json"}
        body = {
            "prompt": prompt,
            # Explicit pixels, never a named preset -- see the note in config.py.
            "image_size": {"width": config.FAL_IMAGE_WIDTH,
                           "height": config.FAL_IMAGE_HEIGHT},
            "num_images": 1,
            "output_format": config.FAL_OUTPUT_FORMAT,
            "sync_mode": config.FAL_SYNC_MODE,
            "guidance_scale": config.FAL_GUIDANCE_SCALE,
            "enable_safety_checker": config.FAL_ENABLE_SAFETY_CHECKER,
            "seed": config.IMAGE_SEED,
        }

        last_err = None
        for attempt in range(1, config.IMAGE_MAX_RETRIES + 1):
            self._throttle()
            self._last_request_time = time.time()
            try:
                resp = requests.post(config.FAL_ENDPOINT, json=body, headers=headers, timeout=300)
                self._check_response(resp)

                url = _extract_fal_image(resp.json())
                if not url:
                    raise RuntimeError(f"no image in response. Body starts: {resp.text[:300]}")
                data = self._resolve_bytes(url)
                if len(data) < 5000:
                    raise RuntimeError(f"suspiciously small image ({len(data)}B)")
                out_path.write_bytes(data)
                return
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


PROVIDERS = {
    "fal": FalProvider,
    "gemini": GeminiProvider,
}


def make_provider(name: str | None = None) -> ImageProvider:
    """Build the provider named by config.IMAGE_PROVIDER.

    The indirection exists because this is the part of the pipeline that keeps
    breaking for reasons outside our control -- two providers died in the same
    month. Swapping is a one-line config change, not a code change.
    """
    name = (name or config.IMAGE_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"Unknown IMAGE_PROVIDER {name!r} in config.py. "
            f"Valid options: {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[name]()


def image_key(prompt: str, provider: ImageProvider) -> str:
    """Cache key for one picture: the full prompt (including the style suffix)
    plus every provider setting that can change the result."""
    return cache.short_hash(cache.STAGE_VERSIONS["image"], prompt, provider.fingerprint())


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
