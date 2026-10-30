"""Shared fixture paths retain profile boundaries before serving or cleanup."""

from pathlib import Path

import pytest

from scripts.online_fixture import catalog_origin, player_origin, tv
from scripts.online_fixture.media import generate_media, guarded_path


PROFILES = (player_origin, catalog_origin, tv)


@pytest.mark.parametrize("profile", PROFILES, ids=("player", "catalog", "tv"))
def test_profiles_reject_each_others_roots_and_non_temporary_paths(profile, tmp_path):
    for other in PROFILES:
        candidate = tmp_path / (other.ROOT_PREFIX + "guard")
        if other is profile:
            assert profile.fixture_root(candidate) == candidate
        else:
            with pytest.raises(ValueError):
                profile.fixture_root(candidate)
    for candidate in (Path("/data"), tmp_path / "arbitrary", Path("/var/tmp") / (profile.ROOT_PREFIX + "guard")):
        with pytest.raises(ValueError):
            profile.fixture_root(candidate)


@pytest.mark.parametrize("profile", PROFILES, ids=("player", "catalog", "tv"))
def test_profiles_reject_root_ancestor_marker_and_media_symlinks(profile, tmp_path):
    root = tmp_path / (profile.ROOT_PREFIX + "guard")
    root.mkdir()
    linked_root = tmp_path / (profile.ROOT_PREFIX + "linked")
    linked_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        profile.fixture_root(linked_root)

    ancestor = tmp_path / "linked-parent"
    ancestor.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        profile.fixture_root(ancestor / root.name)

    marker = root / profile.MARKER
    target = tmp_path / "marker-data"
    target.write_text(profile.MARKER_TEXT)
    marker.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        profile.fixture_root(root)
    marker.unlink()
    (root / "media").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        profile.fixture_root(root)


def test_catalog_state_uses_shared_profile_and_tv_requires_its_own_marker(tmp_path):
    state_root = tmp_path / (catalog_origin.ROOT_PREFIX + "state-guard")
    assert catalog_origin.fixture_root(state_root) == state_root
    tv_root = tmp_path / (tv.ROOT_PREFIX + "guard")
    tv_root.mkdir()
    (tv_root / catalog_origin.MARKER).write_text(catalog_origin.MARKER_TEXT)
    with pytest.raises(ValueError, match="generate"):
        tv.fixture_root(tv_root, require_media=True)


def test_media_generation_refuses_unscoped_roots_before_creating_files(tmp_path):
    root = tmp_path / "arbitrary-media"
    with pytest.raises(ValueError, match="named /tmp"):
        generate_media(root)
    assert not root.exists()


def test_guarded_path_refuses_ancestor_links_even_when_resolved_destination_is_valid(tmp_path):
    root = tmp_path / "jzmedia-online-catalog-guard"
    root.mkdir()
    link = root / "linked-parent"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        guarded_path(link / "media")
