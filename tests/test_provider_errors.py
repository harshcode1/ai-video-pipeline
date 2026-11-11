"""Retry vs fail-fast, for both providers. Transient problems (rate limit, 5xx,
network) are retried with exponential backoff; deterministic ones (bad key, no
credit, rejected request) stop immediately with the reason. No network."""
import types

import pytest

import config
import images
from images import (FalProvider, GeminiProvider, ImageQuotaError, ImageRequestRejectedError,
                    MissingAPIKeyError)
from test_images import IMAGE_B64, FakeResponse, google_error

DATA_URI = "data:image/jpeg;base64," + IMAGE_B64
FAL_OK = FakeResponse(200, {"images": [{"url": DATA_URI}]})
GEMINI_OK = FakeResponse(200, {"outputs": [{"image": {"data": IMAGE_B64}}]})


class Posts:
    """Stands in for `requests`: replays responses/exceptions, records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1
        item = self.responses.pop(0) if self.responses else FakeResponse(500, {})
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff sleeps instead of waiting."""
    recorded = []
    import time
    monkeypatch.setattr(images, "time", types.SimpleNamespace(
        sleep=recorded.append, time=time.time, monotonic=time.monotonic))
    return recorded


def fal(monkeypatch, responses):
    posts = Posts(responses)
    monkeypatch.setattr(images, "requests", posts)
    return FalProvider(api_key="k"), posts


def gemini(monkeypatch, responses):
    posts = Posts(responses)
    monkeypatch.setattr(images, "requests", posts)
    return GeminiProvider(api_key="k"), posts


def fetch(provider, tmp_path):
    out = tmp_path / "img.jpg"
    provider.fetch("a prompt", out)
    return out


# ================================================================= fal: fail fast

@pytest.mark.parametrize("status,payload,exc", [
    (400, {"detail": "Bad request"}, ImageRequestRejectedError),
    (422, {"detail": [{"loc": ["body", "image_size"], "msg": "too large"}]}, ImageRequestRejectedError),
    (401, {"detail": "Invalid key"}, MissingAPIKeyError),
    (403, {"detail": "Forbidden"}, MissingAPIKeyError),
    (402, {"detail": "Payment required"}, ImageQuotaError),
])
def test_fal_deterministic_failures_stop_after_one_request(monkeypatch, tmp_path, sleeps, status, payload, exc):
    provider, posts = fal(monkeypatch, [FakeResponse(status, payload)] * 6)
    with pytest.raises(exc):
        fetch(provider, tmp_path)
    assert posts.calls == 1
    assert sleeps == []                                           # never backed off


@pytest.mark.parametrize("status", [402, 403, 400])
def test_fal_out_of_credit_is_reported_as_credit_not_as_a_bad_key(monkeypatch, tmp_path, sleeps, status):
    provider, posts = fal(monkeypatch, [FakeResponse(status, {"detail": "Insufficient balance"})])
    with pytest.raises(ImageQuotaError, match="no remaining balance"):
        fetch(provider, tmp_path)
    assert posts.calls == 1


def test_fal_validation_error_keeps_the_field_name(monkeypatch, tmp_path, sleeps):
    provider, _ = fal(monkeypatch, [FakeResponse(422, {"detail": [
        {"loc": ["body", "image_size", "width"], "msg": "must be <= 2048"}]})])
    with pytest.raises(ImageRequestRejectedError, match="image_size.width"):
        fetch(provider, tmp_path)


def test_fal_400_message_is_kept(monkeypatch, tmp_path, sleeps):
    provider, _ = fal(monkeypatch, [FakeResponse(400, {"detail": "prompt is empty"})])
    with pytest.raises(ImageRequestRejectedError, match="prompt is empty"):
        fetch(provider, tmp_path)


# ================================================================= fal: retried

@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_fal_transient_status_is_retried_then_succeeds(monkeypatch, tmp_path, sleeps, status):
    provider, posts = fal(monkeypatch, [FakeResponse(status, {"detail": "try later"}), FAL_OK])
    out = fetch(provider, tmp_path)
    assert posts.calls == 2 and out.stat().st_size > 5000
    assert len(sleeps) == 1


def test_fal_network_errors_are_retried(monkeypatch, tmp_path, sleeps):
    provider, posts = fal(monkeypatch, [ConnectionError("reset"), TimeoutError("slow"), FAL_OK])
    fetch(provider, tmp_path)
    assert posts.calls == 3


def test_fal_gives_up_after_the_configured_number_of_attempts(monkeypatch, tmp_path, sleeps):
    provider, posts = fal(monkeypatch, [FakeResponse(503, {"detail": "down"})] * 20)
    with pytest.raises(RuntimeError, match="failed after"):
        fetch(provider, tmp_path)
    assert posts.calls == config.IMAGE_MAX_RETRIES


def test_backoff_grows_exponentially_with_bounded_jitter(monkeypatch, tmp_path, sleeps):
    provider, _ = fal(monkeypatch, [FakeResponse(503, {})] * 20)
    with pytest.raises(RuntimeError):
        fetch(provider, tmp_path)
    base = config.IMAGE_BACKOFF_BASE_SECONDS
    assert len(sleeps) == config.IMAGE_MAX_RETRIES
    for attempt, slept in enumerate(sleeps, start=1):
        low = base * 2 ** (attempt - 1)
        assert low <= slept <= low + 2.0, (attempt, slept)


# ================================================================= gemini

@pytest.mark.parametrize("status,payload,exc", [
    (400, google_error(400, "INVALID_ARGUMENT", "BAD_FIELD", "bad aspect ratio"), ImageRequestRejectedError),
    (400, google_error(400, "INVALID_ARGUMENT", "API_KEY_INVALID", "API key not valid"), MissingAPIKeyError),
    (401, google_error(401, "UNAUTHENTICATED", "X", "no"), MissingAPIKeyError),
    (403, google_error(403, "PERMISSION_DENIED", "X", "no"), MissingAPIKeyError),
    (429, google_error(429, "RESOURCE_EXHAUSTED", "RATE", "quota exceeded for free_tier",
                       [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                         "violations": [{"quotaValue": "0"}]}]), ImageQuotaError),
])
def test_gemini_deterministic_failures_stop_after_one_request(monkeypatch, tmp_path, sleeps, status, payload, exc):
    provider, posts = gemini(monkeypatch, [FakeResponse(status, payload)] * 6)
    with pytest.raises(exc):
        fetch(provider, tmp_path)
    assert posts.calls == 1 and sleeps == []


@pytest.mark.parametrize("status,payload", [
    (429, google_error(429, "RESOURCE_EXHAUSTED", "RATE", "slow down")),
    (500, google_error(500, "INTERNAL", "X", "oops")),
    (503, google_error(503, "UNAVAILABLE", "X", "busy")),
])
def test_gemini_transient_failures_are_retried(monkeypatch, tmp_path, sleeps, status, payload):
    provider, posts = gemini(monkeypatch, [FakeResponse(status, payload), GEMINI_OK])
    fetch(provider, tmp_path)
    assert posts.calls == 2 and len(sleeps) == 1
