"""TTSProvider abstraction. EdgeTTSProvider is the live, free, keyless impl.
AzureTTSProvider is a stub for the later swap to Azure Neural TTS (same
voice names, real <mstts:express-as> emotion styles, ~$0.32/video)."""
import abc
import asyncio
import random
import time
from pathlib import Path

import edge_tts

import cache
import config


def resolved_prosody(emotion: str | None) -> dict:
    """The rate/volume/pitch actually sent to the voice for this emotion, with
    defaults filled in. The cache key uses this, so editing a prosody value in
    config.py re-synthesizes exactly the scenes that use it."""
    prosody = config.EMOTION_PROSODY.get(emotion, config.DEFAULT_PROSODY)
    return {"rate": prosody.get("rate", "+0%"),
            "volume": prosody.get("volume", "+0%"),
            "pitch": prosody.get("pitch", "+0Hz")}


class TTSProvider(abc.ABC):
    @abc.abstractmethod
    def synthesize(self, text: str, emotion: str | None, out_path: Path) -> None:
        """Synthesize `text` with prosody derived from `emotion` to out_path (mp3)."""
        raise NotImplementedError

    @abc.abstractmethod
    def fingerprint(self) -> dict:
        """Provider settings that change the audio (e.g. the voice). Part of the cache key."""
        raise NotImplementedError


def speech_key(text: str, emotion: str | None, provider: TTSProvider) -> str:
    """Cache key for one line of synthesized speech. Deliberately excludes any
    pause: adding silence must not force a pointless re-synthesis."""
    return cache.short_hash(cache.STAGE_VERSIONS["tts"], text, resolved_prosody(emotion),
                            provider.fingerprint())


class EdgeTTSProvider(TTSProvider):
    def __init__(self, voice: str = config.TTS_VOICE):
        self.voice = voice

    def fingerprint(self) -> dict:
        return {"provider": "edge-tts", "voice": self.voice}

    def synthesize(self, text: str, emotion: str | None, out_path: Path) -> None:
        prosody = resolved_prosody(emotion)
        rate, volume, pitch = prosody["rate"], prosody["volume"], prosody["pitch"]

        last_err = None
        for attempt in range(1, config.TTS_MAX_RETRIES + 1):
            try:
                communicate = edge_tts.Communicate(
                    text, self.voice, rate=rate, volume=volume, pitch=pitch
                )
                asyncio.run(communicate.save(str(out_path)))
                size = out_path.stat().st_size if out_path.exists() else 0
                if size < 500:
                    raise RuntimeError(f"suspiciously small audio output: {size} bytes")
                return
            except Exception as e:
                last_err = e
                sleep_s = config.TTS_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1)
                print(f"    TTS attempt {attempt}/{config.TTS_MAX_RETRIES} failed ({e}); retrying in {sleep_s:.1f}s")
                if out_path.exists():
                    out_path.unlink()
                time.sleep(sleep_s)
        raise RuntimeError(f"TTS failed after {config.TTS_MAX_RETRIES} attempts for {out_path}: {last_err}")


class AzureTTSProvider(TTSProvider):
    """STUB -- swap target for commercial licensing + real emotion styles.

    Same voice names as edge-tts (hi-IN-MadhurNeural). Adds SSML
    <mstts:express-as style="..."> for real emotion control instead of the
    rate/volume/pitch approximation used here, at ~$0.32/video.

    To activate: pip install azure-cognitiveservices-speech, set
    AZURE_SPEECH_KEY / AZURE_SPEECH_REGION, implement synthesize() below,
    then swap the provider instance in render.py -- no other file changes.
    """

    def __init__(self, voice: str = config.TTS_VOICE, speech_key: str | None = None, region: str | None = None):
        raise NotImplementedError("AzureTTSProvider is a stub. Implement when swapping off edge-tts.")

    def fingerprint(self) -> dict:
        raise NotImplementedError

    def synthesize(self, text: str, emotion: str | None, out_path: Path) -> None:
        raise NotImplementedError
