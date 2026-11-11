"""Images are cached by a hash of the prompt + provider settings, never by scene
number, and written atomically. No network: providers here are fakes."""
import config
from conftest import scene
from images import ImageProvider, get_image, image_key


class FakeImages(ImageProvider):
    def __init__(self, seed=1):
        self.seed = seed
        self.calls = []

    def fingerprint(self):
        return {"provider": "fake-img", "seed": self.seed}

    def fetch(self, prompt, out_path):
        self.calls.append((prompt, str(out_path)))
        out_path.write_bytes(f"IMAGE-OF:{prompt}".encode() * 50)

    @property
    def prompts(self):
        return [c[0] for c in self.calls]


def fetch_all(scenes, provider):
    return [get_image(s, provider) for s in scenes]


THREE = [scene(1, prompt="a foggy harbour"), scene(2, prompt="a sinking ship"), scene(3, prompt="an empty sea")]


def test_files_are_named_by_hash_not_by_index(project):
    provider = FakeImages()
    paths = fetch_all([dict(s) for s in THREE], provider)
    assert len(provider.calls) == 3
    for p, s in zip(paths, THREE):
        assert p.parent == config.IMAGES_DIR
        assert p.name == f"{image_key(s['image_prompt'], provider)}{config.IMAGE_FILE_EXT}"
    assert not (config.IMAGES_DIR / f"01{config.IMAGE_FILE_EXT}").exists()


def test_scene_records_where_its_image_is(project):
    s = dict(THREE[0])
    path = get_image(s, FakeImages())
    assert s["image_file"] == path.relative_to(config.ROOT).as_posix()
    assert s["image_key"] == path.stem


def test_second_run_makes_zero_fetches(project):
    provider = FakeImages()
    fetch_all([dict(s) for s in THREE], provider)
    provider.calls.clear()
    fetch_all([dict(s) for s in THREE], provider)
    assert provider.calls == []


def test_editing_one_prompt_refetches_only_that_image(project):
    provider = FakeImages()
    fetch_all([dict(s) for s in THREE], provider)
    provider.calls.clear()
    edited = [dict(THREE[0]), dict(THREE[1], image_prompt="a sinking ship, stormy"), dict(THREE[2])]
    fetch_all(edited, provider)
    assert provider.prompts == ["a sinking ship, stormy"]


def test_inserting_a_scene_at_the_start_refetches_only_the_new_scene(project):
    provider = FakeImages()
    fetch_all([dict(s) for s in THREE], provider)
    provider.calls.clear()
    shifted = [scene(1, prompt="a lighthouse")] + [dict(s, index=s["index"] + 1) for s in THREE]
    fetch_all(shifted, provider)
    assert provider.prompts == ["a lighthouse"]


def test_reordering_scenes_makes_zero_fetches(project):
    provider = FakeImages()
    fetch_all([dict(s) for s in THREE], provider)
    provider.calls.clear()
    fetch_all([dict(THREE[2]), dict(THREE[0]), dict(THREE[1])], provider)
    assert provider.calls == []


def test_changing_a_provider_setting_such_as_the_seed_refetches_everything(project):
    fetch_all([dict(s) for s in THREE], FakeImages(seed=1))
    other = FakeImages(seed=2)
    fetch_all([dict(s) for s in THREE], other)
    assert len(other.calls) == 3


def test_identical_prompts_share_one_file(project):
    provider = FakeImages()
    a, b = fetch_all([scene(1, prompt="same"), scene(2, prompt="same")], provider)
    assert a == b and len(provider.calls) == 1


def test_provider_writes_to_a_temp_path_never_the_final_one(project):
    provider = FakeImages()
    final = get_image(dict(THREE[0]), provider)
    assert ".tmp" in provider.calls[0][1]
    assert provider.calls[0][1] != str(final)


def test_a_crash_mid_fetch_leaves_no_valid_image_and_the_retry_pays_once(project):
    class Crashing(FakeImages):
        def fetch(self, prompt, out_path):
            out_path.write_bytes(b"half an image")
            raise RuntimeError("process killed")

    try:
        get_image(dict(THREE[0]), Crashing())
    except RuntimeError:
        pass
    assert [p for p in config.IMAGES_DIR.iterdir() if ".tmp" not in p.name] == []

    good = FakeImages()
    path = get_image(dict(THREE[0]), good)
    assert len(good.calls) == 1 and path.stat().st_size > 0


def test_a_leftover_temp_file_is_not_mistaken_for_the_image(project):
    provider = FakeImages()
    key = image_key(THREE[0]["image_prompt"], provider)
    junk = config.IMAGES_DIR / f"{key}.1234-abcd.tmp{config.IMAGE_FILE_EXT}"
    junk.write_bytes(b"half-written" * 100)
    path = get_image(dict(THREE[0]), provider)
    assert len(provider.calls) == 1
    assert path.read_bytes().startswith(b"IMAGE-OF:")


def test_old_index_named_files_are_ignored_and_left_alone(project):
    """Builds made before content-hash naming left 01.jpg etc. They must never be
    used (they may belong to different content) and must never be deleted."""
    legacy = config.IMAGES_DIR / f"01{config.IMAGE_FILE_EXT}"
    legacy.write_bytes(b"OLD IMAGE FROM A PREVIOUS SCRIPT" * 100)
    legacy_png = config.IMAGES_DIR / "02.png"
    legacy_png.write_bytes(b"\x89PNG old" * 100)

    provider = FakeImages()
    path = get_image(dict(THREE[0]), provider)
    assert len(provider.calls) == 1                         # fetched fresh, did not reuse 01.jpg
    assert path.read_bytes().startswith(b"IMAGE-OF:")
    assert legacy.read_bytes().startswith(b"OLD IMAGE")      # still there
    assert legacy_png.exists()
