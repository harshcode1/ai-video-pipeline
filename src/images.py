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

    def _throttle(self):
        wait = config.IMAGE_MIN_INTERVAL_SECONDS - (time.time() - self._last_request_time)
        if wait > 0:
            time.sleep(wait)

    def fetch(self, prompt: str, out_path: Path) -> None:
        body = {
            "model": config.IMAGE_MODEL,
            "input": [{"type": "text", "text": prompt}],
            "response_format": {
                "type": "image",
                "mime_type": config.IMAGE_MIME_TYPE,
                "aspect_ratio": config.IMAGE_ASPECT_RATIO,
                "image_size": config.IMAGE_SIZE,
            },
        }
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

        last_err = None
        for attempt in range(1, config.IMAGE_MAX_RETRIES + 1):
            self._throttle()
            self._last_request_time = time.time()
            try:
                resp = requests.post(config.IMAGE_ENDPOINT, json=body, headers=headers, timeout=300)

                # A bad/unauthorized key will never fix itself -- fail fast with
                # an actionable message instead of burning six slow retries.
                if resp.status_code in (401, 403):
                    raise MissingAPIKeyError(
                        f"Image API rejected the key (HTTP {resp.status_code}). Check that "
                        f"{config.IMAGE_API_KEY_ENV} holds a valid key from "
                        f"https://aistudio.google.com/apikey -- detail: {resp.text[:300]}"
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()

                data = _extract_image_bytes(resp.json())
                if not data or len(data) < 5000:
                    raise RuntimeError(
                        f"no usable image in response (got {len(data or b'')}B). "
                        f"Body starts: {resp.text[:300]}"
                    )
                out_path.write_bytes(data)
                return
            except MissingAPIKeyError:
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
    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"  [IMG] scene {scene['index']:02d} ({scene['timestamp']}) cached, skipping")
        return out_path
    print(f"  [IMG] scene {scene['index']:02d} ({scene['timestamp']})")
    provider.fetch(scene["image_prompt"], out_path)
    return out_path
