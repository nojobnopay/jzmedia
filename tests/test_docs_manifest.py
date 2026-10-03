"""Documentation provenance must not turn a metadata refresh into a recapture."""
from datetime import date
import hashlib
import json

import pytest

from scripts import capture_docs_manifest as manifest


@pytest.fixture
def asset_tree(tmp_path, monkeypatch):
    assets = tmp_path / "docs/assets"
    assets.mkdir(parents=True)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/package.json").write_text('{"version":"0.19.0"}')
    monkeypatch.setattr(manifest, "ROOT", tmp_path)
    monkeypatch.setattr(manifest, "ASSETS", assets)
    monkeypatch.setattr(manifest.shutil, "which", lambda name: None)
    monkeypatch.setattr(manifest.subprocess, "check_output", lambda *args, **kwargs: "abc123\n")

    def create(file, content=b"original"):
        path = assets / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"file": file, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
                "verified_at": "2026-09-28"}

    def save(entries, **extra):
        (assets / "manifest.json").write_text(json.dumps({"schema_version": 1, "assets": entries, **extra}))

    return assets, create, save


def test_refresh_preserves_original_dates_and_migrates_only_implicit_sources(asset_tree):
    _, create, save = asset_tree
    old = create("screenshots/demo-subtitles.webp")
    external = {**create("screenshots/ai-settings-go.webp"), "source": "mock API", "source_commit": "explicit"}
    save([old, external], source="isolated application", source_commit="old", app_version="0.18.0",
         recording_script="scripts/capture_docs.py", source_state="historical worktree")
    result = manifest.main()
    by_file = {entry["file"]: entry for entry in result["assets"]}
    assert result["provenance_scope"] == "per_asset"
    assert "source_commit" not in result
    assert by_file[old["file"]]["verified_at"] == "2026-09-28"
    assert by_file[old["file"]]["source_commit"] == "old"
    assert by_file[external["file"]] == external
    assert result["unverified_assets"] == []
    assert manifest.main() == result


def test_uncaptured_changes_are_reported_without_rewriting_capture_evidence(asset_tree):
    assets, create, save = asset_tree
    old = create("screenshots/demo-subtitles.webp")
    missing = create("screenshots/ai-settings-go.webp")
    save([old, missing], provenance_scope="per_asset")
    (assets / old["file"]).write_bytes(b"changed")
    (assets / missing["file"]).unlink()
    new = create("screenshots/player-info.webp")
    result = manifest.main()
    assert result["assets"] == [old, missing]
    assert {entry["file"] for entry in result["unverified_assets"]} == {old["file"], missing["file"], new["file"]}
    assert all("verified_at" not in entry for entry in result["unverified_assets"])


@pytest.mark.parametrize("content", [b"original", b"changed"])
def test_explicit_recapture_records_today_even_when_result_is_identical(asset_tree, content):
    assets, create, save = asset_tree
    old = create("screenshots/demo-subtitles.webp")
    save([old])
    (assets / old["file"]).write_bytes(content)
    result = manifest.main(captured={old["file"]})
    entry = result["assets"][0]
    assert entry["captured_at"] == entry["verified_at"] == date.today().isoformat()
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert len(entry["source_sha256"]) == 64
    assert entry["source_commit"] == "abc123"
    assert result["unverified_assets"] == []


def test_concept_review_preserves_generation_date_and_requires_matching_content(asset_tree):
    assets, create, save = asset_tree
    old = create("diagrams/libraries.svg")
    save([old])
    entry = manifest.main(reviewed={old["file"]: "Current media/video library schema"})["assets"][0]
    assert entry["verified_at"] == "2026-09-28"
    assert entry["reviewed_at"] == date.today().isoformat()
    assert "captured_at" not in entry
    (assets / old["file"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="文件与清单一致"):
        manifest.main(reviewed={old["file"]: "Current schema"})
