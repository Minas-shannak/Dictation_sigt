"""One-time clean start for the audio-based pipeline (PLAN.md section 13).

The caption-based version's data doesn't fit the audio transcripts (different
sentences), so on the first start of this version it is backed up, then cleared:

- here, on the server: the old caption cache (cache/<videoId>.json);
- in the browser, by the page itself: saved progress, stats, missed words, recent
  videos and offsets (localStorage). The page sends a copy here first
  (save_browser_backup) and only clears its records once that copy is stored.

Nothing is deleted before its copy is written and verified. The data-format version
in DATA_VERSION_FILE makes the server part run once.

    python -m services.migration --dry-run          # show what would be backed up and cleared
    python -m services.migration --restore DIR      # put the backed-up cache files back

Restoring the browser part is described in the README ("Restoring the old data").
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import config
from services.transcript_cache import write_json_atomic

log = logging.getLogger(__name__)

README_TEXT = """Backup made before the first start of the audio-based version (PLAN.md section 13).

cache/                       the old caption cache files, byte for byte (see manifest.json for SHA-256)
localStorage-*.json          the browser's saved data (progress, stats, missed words, recent videos,
                             offsets, sound and theme settings), as {"items": {key: value}}

To go back to the caption-based version, see "Restoring the old data" in README.md.
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state() -> Dict:
    try:
        return json.loads(Path(config.DATA_VERSION_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def old_cache_files() -> List[Path]:
    """Caption-cache files of the old version: cache/<id>.json with videoId and lines."""
    found = []
    for path in sorted(Path(config.CACHE_DIR).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "videoId" in data and "lines" in data and "cache_format" not in data:
            found.append(path)
    return found


def plan() -> Dict:
    """What run() would do, without changing anything."""
    state = _state()
    done = state.get("data_format_version", 0) >= config.DATA_FORMAT_VERSION
    files = [] if done else old_cache_files()
    return {
        "needed": not done,
        "data_format_version": state.get("data_format_version", 0),
        "backup_dir": state.get("backup_dir"),
        "files": [{"path": str(p), "size": p.stat().st_size, "sha256": _sha256(p)} for p in files],
    }


def _new_backup_dir() -> Path:
    path = Path(config.BACKUP_DIR) / ("pre-audio-pipeline-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    path.mkdir(parents=True, exist_ok=False)
    (path / "README.txt").write_text(README_TEXT, encoding="utf-8")
    return path


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(config.BASE_DIR))
    except ValueError:
        return str(path)


def run() -> Dict:
    """Back up and clear the old caption cache once. Returns the stored state."""
    state = _state()
    if state.get("data_format_version", 0) >= config.DATA_FORMAT_VERSION:
        return state
    files = old_cache_files()
    backup = None
    if files:
        backup = _new_backup_dir()
        (backup / "cache").mkdir()
        manifest = []
        for src in files:
            dest = backup / "cache" / src.name
            shutil.copy2(src, dest)
            digest = _sha256(src)
            if _sha256(dest) != digest:
                raise RuntimeError(f"Backup copy of {src} does not match; nothing was cleared")
            manifest.append({"file": f"cache/{src.name}", "original": _relative(src),
                             "size": src.stat().st_size, "sha256": digest})
        write_json_atomic(backup / "manifest.json", {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reason": "clean start for the audio-based transcription pipeline (PLAN.md section 13)",
            "files": manifest})
        # Every copy is verified; only now are the originals removed.
        for src in files:
            src.unlink()
        log.info("Backed up %d old caption-cache files to %s and removed them: %s",
                 len(files), backup, ", ".join(p.name for p in files))
    else:
        log.info("No old caption-cache files to back up")
    state = {"data_format_version": config.DATA_FORMAT_VERSION, "migrated_at": time.time(),
             "backup_dir": _relative(backup) if backup else None}
    write_json_atomic(Path(config.DATA_VERSION_FILE), state)
    return state


def save_browser_backup(items: Dict[str, str], origin: str) -> str:
    """Store the page's localStorage copy in the backup folder; returns its path (relative)."""
    if not isinstance(items, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in items.items()):
        raise ValueError("items must map strings to strings")
    state = _state()
    backup = Path(config.BASE_DIR) / state["backup_dir"] if state.get("backup_dir") else None
    if backup is None or not backup.is_dir():
        backup = _new_backup_dir()
        state["backup_dir"] = _relative(backup)
        state.setdefault("data_format_version", 0)
        write_json_atomic(Path(config.DATA_VERSION_FILE), state)
    path = backup / ("localStorage-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
    write_json_atomic(path, {"origin": origin, "saved_at": datetime.now().isoformat(timespec="seconds"),
                             "items": items})
    log.info("Saved the browser's old data (%d keys from %s) to %s", len(items), origin, path)
    return _relative(path)


def browser_backup_path(name: str) -> Optional[Path]:
    """A saved localStorage backup by file name (for restoring), or None."""
    state = _state()
    if not state.get("backup_dir") or "/" in name or "\\" in name or not name.startswith("localStorage-"):
        return None
    path = Path(config.BASE_DIR) / state["backup_dir"] / name
    return path if path.is_file() else None


def restore_cache(backup_dir: str) -> List[str]:
    """Copy the backed-up caption-cache files back into cache/, checking their SHA-256."""
    backup = Path(backup_dir)
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    restored = []
    Path(config.CACHE_DIR).mkdir(parents=True, exist_ok=True)
    for entry in manifest["files"]:
        src = backup / entry["file"]
        if _sha256(src) != entry["sha256"]:
            raise RuntimeError(f"{src} does not match its manifest checksum; not restored")
        dest = Path(config.CACHE_DIR) / src.name
        shutil.copy2(src, dest)
        restored.append(dest.name)
    log.info("Restored %d caption-cache files from %s", len(restored), backup)
    return restored


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.migration", description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="show what would be backed up and cleared")
    group.add_argument("--restore", metavar="BACKUP_DIR", help="copy the backed-up cache files back into cache/")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.dry_run:
        p = plan()
        if not p["needed"]:
            print(f"Already migrated (data format {p['data_format_version']}); backup: {p['backup_dir']}")
            return 0
        print(f"Would back up and then remove {len(p['files'])} old caption-cache files into "
              f"{_relative(Path(config.BACKUP_DIR))}/pre-audio-pipeline-<date-time>/cache/:")
        for f in p["files"]:
            print(f"  {f['path']}  ({f['size']} bytes, sha256 {f['sha256'][:12]}…)")
        print("The browser backs up and clears its own old records on its first visit (see README).")
        return 0
    for name in restore_cache(args.restore):
        print("restored", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
