"""An image's cache key must change whenever the picture it would produce could
change, and must not change for anything that doesn't affect the picture."""
import pytest

import config
import images
from images import FalProvider, GeminiProvider, image_key


def fal():
    return FalProvider(api_key="key-A")


def gemini():
    return GeminiProvider(api_key="key-A")


PROMPT = "a foggy harbour at dawn, oil painting"


def test_same_prompt_same_provider_settings_same_key():
    assert image_key(PROMPT, fal()) == image_key(PROMPT, FalProvider(api_key="key-A"))


def test_api_key_does_not_affect_the_key():
    assert image_key(PROMPT, FalProvider(api_key="one")) == image_key(PROMPT, FalProvider(api_key="two"))


def test_prompt_change_changes_the_key():
    assert image_key(PROMPT, fal()) != image_key(PROMPT + " ", fal())
    assert image_key(PROMPT, fal()) != image_key("a foggy harbour at dusk", fal())


def test_style_suffix_is_part_of_the_prompt_hash():
    assert image_key(PROMPT + ", noir, 35mm", fal()) != image_key(PROMPT, fal())


def test_provider_choice_changes_the_key():
    assert image_key(PROMPT, fal()) != image_key(PROMPT, gemini())


@pytest.mark.parametrize("setting,new_value", [
    ("IMAGE_SEED", 7),
    ("FAL_IMAGE_WIDTH", 1024),
    ("FAL_IMAGE_HEIGHT", 576),
    ("FAL_GUIDANCE_SCALE", 3.5),
    ("FAL_OUTPUT_FORMAT", "png"),
    ("FAL_ENDPOINT", "https://example.invalid/other-model"),
    ("FAL_ENABLE_SAFETY_CHECKER", False),
])
def test_every_fal_setting_that_changes_the_picture_changes_the_key(monkeypatch, setting, new_value):
    before = image_key(PROMPT, fal())
    monkeypatch.setattr(config, setting, new_value)
    assert image_key(PROMPT, fal()) != before


@pytest.mark.parametrize("setting,new_value", [
    ("IMAGE_MODEL", "gemini-other"),
    ("IMAGE_SIZE", "2K"),
    ("IMAGE_ASPECT_RATIO", "4:3"),
    ("IMAGE_ENDPOINT", "https://example.invalid/v2"),
])
def test_every_gemini_setting_that_changes_the_picture_changes_the_key(monkeypatch, setting, new_value):
    before = image_key(PROMPT, gemini())
    monkeypatch.setattr(config, setting, new_value)
    assert image_key(PROMPT, gemini()) != before


def test_gemini_ignores_the_fal_seed_because_it_has_no_seed(monkeypatch):
    before = image_key(PROMPT, gemini())
    monkeypatch.setattr(config, "IMAGE_SEED", 99)
    assert image_key(PROMPT, gemini()) == before


def test_bumping_the_image_stage_version_invalidates_every_key(monkeypatch):
    import cache
    before = image_key(PROMPT, fal())
    monkeypatch.setitem(cache.STAGE_VERSIONS, "image", "2")
    assert image_key(PROMPT, fal()) != before


def test_every_registered_provider_publishes_a_fingerprint():
    for name, cls in images.PROVIDERS.items():
        provider = cls(api_key="k")
        fp = provider.fingerprint()
        assert fp["provider"] == name
