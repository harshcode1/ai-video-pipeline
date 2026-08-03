"""ImageProvider abstraction. PollinationsProvider is the live, free,
keyless impl. CloudflareWorkersAIProvider is a fallback stub for when
Pollinations is down (has no SLA and will flake)."""
import abc
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


class PollinationsProvider(ImageProvider):
    def __init__(self):
        self._last_request_time = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_request_time
        wait = config.POLLINATIONS_MIN_INTERVAL_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)

    def fetch(self, prompt: str, out_path: Path) -> None:
        encoded = quote(prompt, safe="")
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?model={config.IMAGE_MODEL}&seed={config.IMAGE_SEED}&nologo=true"
            f"&width={config.IMAGE_WIDTH}&height={config.IMAGE_HEIGHT}"
        )

        last_err = None
        for attempt in range(1, config.IMAGE_MAX_RETRIES + 1):
            self._throttle()
            self._last_request_time = time.time()
            try:
                resp = requests.get(url, timeout=180)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "image" not in content_type or len(resp.content) < 5000:
                    raise RuntimeError(
                        f"suspicious response: content-type={content_type!r} size={len(resp.content)}B"
                    )
                out_path.write_bytes(resp.content)
                return
            except Exception as e:
                last_err = e
                sleep_s = config.IMAGE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 2)
                print(f"    Image attempt {attempt}/{config.IMAGE_MAX_RETRIES} failed ({e}); retrying in {sleep_s:.1f}s")
                time.sleep(sleep_s)
        raise RuntimeError(f"Image fetch failed after {config.IMAGE_MAX_RETRIES} attempts: {last_err}")


class CloudflareWorkersAIProvider(ImageProvider):
    """STUB fallback for when Pollinations is unavailable.

    Model: @cf/black-forest-labs/flux-1-schnell on Cloudflare Workers AI
    (free tier includes a monthly neuron allotment -- no credit card for
    the free tier, but requires a Cloudflare account + API token).

    To activate: set CF_ACCOUNT_ID / CF_API_TOKEN, implement fetch() below
    using POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell,
    then swap the provider instance in render.py.
    """

    def __init__(self, account_id: str | None = None, api_token: str | None = None):
        raise NotImplementedError("CloudflareWorkersAIProvider is a stub. Implement if Pollinations reliability becomes a problem.")

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
