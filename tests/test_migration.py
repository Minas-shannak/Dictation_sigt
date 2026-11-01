"""services.migration: one-time backup and clearing of the caption-based version's data."""

import hashlib
import json
from pathlib import Path

import pytest

import app as app_module
import config
from services import migration

OLD = {"videoId": "arj7oStGLkU", "lang": "en", "source": "manual",
       "lines": [{"id": 0, "start": 12.645, "duration": 3.255, "text": "So in college, I was a government major,"}]}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backup")
    monkeypatch.setattr(config, "DATA_VERSION_FILE", tmp_path / "data_version.json")
    (tmp_path / "cache" / "asr").mkdir(parents=True)
    for vid in ("arj7oStGLkU", "cpbARmnt_nU"):
        (tmp_path / "cache" / f"{vid}.json").write_text(json.dumps(dict(OLD, videoId=vid), indent=1))
    # New-format cache entries and unrelated files are never touched.
    (tmp_path / "cache" / "asr" / "yt-x__k.json").write_text('{"cache_format": 1}')
    (tmp_path / "cache" / "notes.json").write_text('{"something": "else"}')
    return tmp_path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_dry_run_lists_the_old_cache_and_changes_nothing(root):
    before = {p.name: digest(p) for p in (root / "cache").glob("*.json")}
    p = migration.plan()
    assert p["needed"] is True
    assert sorted(Path(f["path"]).name for f in p["files"]) == ["arj7oStGLkU.json", "cpbARmnt_nU.json"]
    assert {p.name: digest(p) for p in (root / "cache").glob("*.json")} == before
    assert not (root / "backup").exists() and not (root / "data_version.json").exists()


def test_run_backs_up_verifies_then_clears_once(root):
    originals = {name: (root / "cache" / name).read_bytes() for name in ("arj7oStGLkU.json", "cpbARmnt_nU.json")}
    state = migration.run()
    backup = root / state["backup_dir"]
    assert state["data_format_version"] == config.DATA_FORMAT_VERSION
    for name, data in originals.items():
        assert (backup / "cache" / name).read_bytes() == data       # byte-for-byte copy
        assert not (root / "cache" / name).exists()                 # then removed
    manifest = json.loads((backup / "manifest.json").read_text())
    assert sorted(f["file"] for f in manifest["files"]) == ["cache/arj7oStGLkU.json", "cache/cpbARmnt_nU.json"]
    assert all(f["sha256"] == hashlib.sha256(originals[Path(f["file"]).name]).hexdigest() for f in manifest["files"])
    assert (backup / "README.txt").exists()
    assert (root / "cache" / "asr" / "yt-x__k.json").exists() and (root / "cache" / "notes.json").exists()
    # It runs once: a second start does nothing, even if an old-format file appears again.
    (root / "cache" / "arj7oStGLkU.json").write_text(json.dumps(OLD))
    assert migration.run() == state
    assert (root / "cache" / "arj7oStGLkU.json").exists()
    assert len(list((root / "backup").iterdir())) == 1


def test_fresh_install_just_records_the_version(root):
    for p in (root / "cache").glob("*.json"):
        if p.name != "notes.json":
            p.unlink()
    state = migration.run()
    assert state["backup_dir"] is None and not (root / "backup").exists()


def test_nothing_is_deleted_if_a_copy_does_not_match(root, monkeypatch):
    real = migration._sha256
    monkeypatch.setattr(migration, "_sha256", lambda p: "bad" if "backup" in str(p) else real(p))
    with pytest.raises(RuntimeError):
        migration.run()
    assert (root / "cache" / "arj7oStGLkU.json").exists() and (root / "cache" / "cpbARmnt_nU.json").exists()
    assert not (root / "data_version.json").exists()  # it will try again next start


def test_browser_backup_goes_next_to_the_cache_backup_and_restores(root):
    state = migration.run()
    items = {"dictation:video:cpbARmnt_nU": '{"lineIndex":102}', "dictation:sound": '{"muted":false,"volume":6}'}
    rel = migration.save_browser_backup(items, "http://localhost:8000")
    assert rel.startswith(state["backup_dir"])
    saved = json.loads((root / rel).read_text())
    assert saved["items"] == items and saved["origin"] == "http://localhost:8000"
    assert migration.browser_backup_path(Path(rel).name) == root / rel
    assert migration.browser_backup_path("../data_version.json") is None


def test_browser_backup_without_cache_backup_creates_a_folder(root):
    for p in (root / "cache").glob("*.json"):
        p.unlink()
    migration.run()
    rel = migration.save_browser_backup({"dictation:video:abc": "{}"}, "http://localhost:8000")
    assert (root / rel).exists() and json.loads((root / "data_version.json").read_text())["backup_dir"] in rel


@pytest.mark.parametrize("items", [None, ["a"], {"a": 1}])
def test_browser_backup_rejects_bad_data(root, items):
    with pytest.raises(ValueError):
        migration.save_browser_backup(items, "x")


def test_restore_puts_the_cache_back_byte_for_byte(root):
    before = {p.name: digest(p) for p in (root / "cache").glob("*.json") if p.name != "notes.json"}
    state = migration.run()
    restored = migration.restore_cache(str(root / state["backup_dir"]))
    assert sorted(restored) == sorted(before)
    assert {name: digest(root / "cache" / name) for name in before} == before


def test_routes(root, monkeypatch):
    c = app_module.app.test_client()
    assert c.get("/api/config").get_json() == {"pad_before": 0.4, "pad_after": 0.5, "margin_default": 0.5,
                                                 "margin_max": 1.5, "data_format_version": 2}
    assert c.get("/api/migration").get_json()["needed"] is True
    migration.run()
    r = c.post("/api/migration/browser-backup", json={"items": {"dictation:sound": "{}"}, "origin": "http://localhost:8000"})
    assert r.status_code == 200
    name = Path(r.get_json()["path"]).name
    assert c.get(f"/api/migration/browser-backup/{name}").get_json()["items"] == {"dictation:sound": "{}"}
    assert c.get("/api/migration/browser-backup/nope.json").status_code == 404
    assert c.post("/api/migration/browser-backup", json={"items": "x"}).status_code == 400
