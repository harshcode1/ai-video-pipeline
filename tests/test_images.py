"""Plain-assert unit tests for image-API error classification (no pytest).
Run: python tests/test_images.py

These exist because the failure modes here are expensive and invisible. Every
case below is a real bug that shipped: a bad key answered with 400 (not 401)
and got retried six times; a rejected mime type had its explanation thrown
away by raise_for_status(); a zero quota was treated as a transient
rate-limit. Each one costs minutes of a multi-hour render and reports the
wrong cause, so the classifier is tested against real response bodies rather
than trusted.

No network and no ffmpeg needed -- requests.post is stubbed.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
import images
from images import (GeminiProvider, ImageQuotaError, ImageRequestRejectedError,
                    MissingAPIKeyError, _error_details, _extract_image_bytes,
                    _is_zero_quota, get_image)

import base64

IMAGE_B64 = base64.b64encode(b"\xff\xd8\xff" + b"x" * 6000).decode()


class FakeResponse:
    def __init__(self, status_code, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def google_error(code, status, reason, message, extra_details=None):
    """The array-wrapped error envelope this endpoint actually returns."""
    details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": reason, "domain": "googleapis.com"}]
    details.extend(extra_details or [])
    return [{"error": {"code": code, "message": message,
                       "status": status, "details": details}}]


class FakeRequests:
    """Stands in for the `requests` module inside images.py."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        return self.responses.pop(0) if self.responses else FakeResponse(500, {})


def make_provider(responses):
    fake = FakeRequests(responses)
    images.requests = fake
    return GeminiProvider(api_key="test-key"), fake


# --- _error_details -------------------------------------------------------

def test_error_details_parses_array_wrapped_envelope():
    """Verified live: this endpoint wraps errors in a single-element ARRAY."""
    resp = FakeResponse(400, google_error(
        400, "INVALID_ARGUMENT", "API_KEY_INVALID",
        "API key not valid. Please pass a valid API key."))
    status, reason, message = _error_details(resp)
    assert status == "INVALID_ARGUMENT", status
    assert reason == "API_KEY_INVALID", reason
    assert "not valid" in message, message


def test_error_details_parses_bare_object_envelope():
    resp = FakeResponse(400, {"error": {"status": "INVALID_ARGUMENT",
                                        "message": "bad field", "details": []}})
    status, reason, message = _error_details(resp)
    assert (status, reason, message) == ("INVALID_ARGUMENT", "", "bad field")


def test_error_details_survives_non_json_body():
    resp = FakeResponse(502, None, text="<html>gateway error</html>")
    status, reason, message = _error_details(resp)
    assert (status, reason) == ("", "")
    assert "gateway" in message


def test_error_details_survives_empty_array():
    resp = FakeResponse(400, [], text="[]")
    status, reason, message = _error_details(resp)
    assert (status, reason) == ("", ""), (status, reason)
    assert message == "[]", message


# --- _is_zero_quota -------------------------------------------------------

def test_zero_quota_detected_from_quota_value():
    resp = FakeResponse(429, google_error(
        429, "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED", "Quota exceeded.",
        [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
          "violations": [{"quotaMetric": "generate_requests", "quotaValue": "0"}]}]))
    assert _is_zero_quota(resp) is True


def test_ordinary_rate_limit_is_not_zero_quota():
    resp = FakeResponse(429, google_error(
        429, "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED",
        "Resource has been exhausted (e.g. check your quota).",
        [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
          "violations": [{"quotaMetric": "generate_requests", "quotaValue": "60"}]}]))
    assert _is_zero_quota(resp) is False


# --- fetch() classification ----------------------------------------------

def test_invalid_key_400_fails_fast_without_retrying():
    """The bug this catches: a bad key returns 400, not 401/403, so the old
    `if status in (401, 403)` check never fired and the key error was retried
    six times and then reported as something else."""
    provider, fake = make_provider([FakeResponse(400, google_error(
        400, "INVALID_ARGUMENT", "API_KEY_INVALID",
        "API key not valid. Please pass a valid API key."))])
    try:
        provider.fetch("a cat", Path("unused.jpg"))
        assert False, "should have raised MissingAPIKeyError"
    except MissingAPIKeyError as e:
        assert "aistudio.google.com" in str(e), e
    assert len(fake.calls) == 1, f"retried a deterministic failure: {len(fake.calls)} calls"


def test_zero_quota_fails_fast_with_billing_advice():
    provider, fake = make_provider([FakeResponse(429, google_error(
        429, "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED", "Quota exceeded.",
        [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
          "violations": [{"quotaValue": "0"}]}]))])
    try:
        provider.fetch("a cat", Path("unused.jpg"))
        assert False, "should have raised ImageQuotaError"
    except ImageQuotaError as e:
        assert "billing" in str(e).lower(), e
    assert len(fake.calls) == 1, f"waiting cannot fix a zero quota: {len(fake.calls)} calls"


def test_generic_400_fails_fast_and_keeps_the_reason():
    """raise_for_status() used to discard the body -- which is exactly the line
    that says what the API disliked."""
    provider, fake = make_provider([FakeResponse(400, google_error(
        400, "INVALID_ARGUMENT", "INVALID_ARGUMENT",
        "Invalid value at 'response_format.image_size'. Supported values: 1K, 2K, 4K"))])
    try:
        provider.fetch("a cat", Path("unused.jpg"))
        assert False, "should have raised ImageRequestRejectedError"
    except ImageRequestRejectedError as e:
        assert "Supported values" in str(e), e
    assert len(fake.calls) == 1, len(fake.calls)


def test_rejected_mime_type_falls_back_to_the_other_one(tmp_path=None):
    """The docs list both image/jpeg and image/png as accepted; a live run saw
    a 400 for image/png. Rather than bet on either, fall back automatically."""
    out = config.BUILD_DIR / "_test_mime_fallback.bin"
    provider, fake = make_provider([
        FakeResponse(400, google_error(
            400, "INVALID_ARGUMENT", "INVALID_ARGUMENT",
            "Invalid value at 'response_format.mime_type'. Supported values: image/jpeg")),
        FakeResponse(200, {"output_image": {"data": IMAGE_B64}}),
    ])
    try:
        provider.fetch("a cat", out)
        assert len(fake.calls) == 2, f"expected one retry, got {len(fake.calls)}"
        assert fake.calls[0]["response_format"]["mime_type"] == "image/jpeg"
        assert fake.calls[1]["response_format"]["mime_type"] == "image/png"
        assert provider.mime_type == "image/png"
        assert out.stat().st_size > 5000
    finally:
        if out.exists():
            out.unlink()


def test_mime_fallback_gives_up_instead_of_looping_forever():
    """Both candidates rejected must terminate, not spin."""
    rejection = lambda: FakeResponse(400, google_error(
        400, "INVALID_ARGUMENT", "INVALID_ARGUMENT",
        "Invalid value at 'response_format.mime_type'."))
    provider, fake = make_provider([rejection() for _ in range(10)])
    try:
        provider.fetch("a cat", Path("unused.jpg"))
        assert False, "should have raised ImageRequestRejectedError"
    except ImageRequestRejectedError:
        pass
    assert len(fake.calls) == len(config.IMAGE_MIME_TYPES), len(fake.calls)


def test_transient_rate_limit_is_retried_then_succeeds():
    out = config.BUILD_DIR / "_test_transient.bin"
    provider, fake = make_provider([
        FakeResponse(429, google_error(429, "RESOURCE_EXHAUSTED",
                                       "RATE_LIMIT_EXCEEDED", "Too many requests.")),
        FakeResponse(200, {"output_image": {"data": IMAGE_B64}}),
    ])
    try:
        provider.fetch("a cat", out)
        assert len(fake.calls) == 2, len(fake.calls)
        assert out.stat().st_size > 5000
    finally:
        if out.exists():
            out.unlink()


def test_server_error_is_retried():
    out = config.BUILD_DIR / "_test_5xx.bin"
    provider, fake = make_provider([
        FakeResponse(503, None, text="upstream unavailable"),
        FakeResponse(200, {"output_image": {"data": IMAGE_B64}}),
    ])
    try:
        provider.fetch("a cat", out)
        assert len(fake.calls) == 2, len(fake.calls)
    finally:
        if out.exists():
            out.unlink()


def test_request_body_matches_the_documented_shape():
    out = config.BUILD_DIR / "_test_body.bin"
    provider, fake = make_provider([FakeResponse(200, {"output_image": {"data": IMAGE_B64}})])
    try:
        provider.fetch("a fox in fog", out)
        body = fake.calls[0]
        assert body["model"] == config.IMAGE_MODEL
        assert body["input"] == [{"type": "text", "text": "a fox in fog"}]
        rf = body["response_format"]
        assert rf["type"] == "image"
        assert rf["aspect_ratio"] == config.IMAGE_ASPECT_RATIO
        assert rf["image_size"] == config.IMAGE_SIZE
        assert rf["image_size"] == rf["image_size"].upper(), "uppercase K is mandatory"
    finally:
        if out.exists():
            out.unlink()


# --- _extract_image_bytes -------------------------------------------------

def test_extract_handles_several_response_shapes():
    shapes = [
        {"output_image": {"data": IMAGE_B64}},
        {"output": [{"type": "image", "data": IMAGE_B64}]},
        {"candidates": [{"content": {"parts": [{"inlineData": {"data": IMAGE_B64}}]}}]},
        {"data": [{"b64_json": IMAGE_B64}]},
        [{"result": {"nested": {"deeper": {"image_bytes": IMAGE_B64}}}}],
    ]
    for shape in shapes:
        got = _extract_image_bytes(shape)
        assert got and len(got) > 5000, f"failed on {str(shape)[:60]}"


def test_extract_returns_none_for_error_payloads():
    assert _extract_image_bytes(google_error(400, "X", "Y", "nope")) is None
    assert _extract_image_bytes({"data": "short"}) is None


# --- cache resolution -----------------------------------------------------

def test_cached_image_is_reused_across_file_extensions():
    """The output format changed from png to jpeg. A cache keyed on the current
    extension alone would silently re-buy every image in the video."""
    legacy = config.IMAGES_DIR / "99.png"
    if legacy.exists():
        return  # don't touch a real cached image
    legacy.write_bytes(b"\x89PNG" + b"x" * 6000)
    scene = {"index": 99, "timestamp": "00:99", "image_prompt": "unused",
             "image_file": f"build/images/99{config.IMAGE_FILE_EXT}"}

    class ExplodingProvider:
        def fetch(self, prompt, out_path):
            raise AssertionError("re-fetched an image that was already cached")

    try:
        got = get_image(scene, ExplodingProvider())
        assert got == legacy, got
        assert scene["image_file"] == "build/images/99.png", scene["image_file"]
    finally:
        legacy.unlink()


def run_all():
    time.sleep = lambda *_: None          # no real backoff waits
    config.IMAGE_MIN_INTERVAL_SECONDS = 0.0  # no real throttle
    real_requests = images.requests
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    try:
        for t in tests:
            try:
                t()
                print(f"  PASS {t.__name__}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL {t.__name__}: {e}")
    finally:
        images.requests = real_requests
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
