"""Gantz core engine.

Pure, GUI-free implementations of every file operation used by the Gantz
desktop app: scanning, bulk renaming, text search/replace, zip archives and
undo.  The module deliberately has **no third-party imports and no network
access**, so the whole tool keeps working on a disconnected machine.

The GUI (``gantz_gui.py``) is a thin layer on top of these functions; the
unit tests in ``tests/test_core.py`` exercise them directly.
"""

from __future__ import annotations

import codecs
import json
import stat as stat_module
import os
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

APP_NAME = "Gantz"
APP_VERSION = "2.0.0"
APP_TAGLINE = "Offline file toolkit"

#: Extensions we treat as "text" for the *Text files only* filter and for the
#: extension picker.  Search & replace never relies on this list alone - it
#: also sniffs the bytes of every candidate file before touching it.
TEXT_EXTENSIONS = frozenset(
    {
        ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
        ".json", ".jsonl", ".ndjson", ".xml", ".html", ".htm", ".xhtml",
        ".css", ".scss", ".less", ".js", ".jsx", ".ts", ".tsx", ".vue",
        ".py", ".pyw", ".bat", ".cmd", ".ps1", ".psm1", ".sh", ".zsh",
        ".ini", ".cfg", ".conf", ".properties", ".env", ".yaml", ".yml",
        ".toml", ".sql", ".rtf", ".srt", ".vtt", ".tex", ".svg",
        ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".hpp",
        ".cs", ".go", ".rs", ".php", ".rb", ".pl", ".lua", ".swift",
        ".gradle", ".gitignore", ".editorconfig", ".dockerfile", ".make",
        ".mk", ".cmake", ".diff", ".patch",
    }
)

#: Directories that are never worth walking into (VCS metadata, caches, ...).
SKIP_DIR_NAMES = frozenset(
    {
        ".git", ".svn", ".hg", ".gantz", "__pycache__", ".mypy_cache",
        ".pytest_cache", ".ruff_cache", "node_modules", ".venv", "venv",
        "env", ".tox", ".idea", ".vscode", "$RECYCLE.BIN",
        "System Volume Information", ".Trash", ".Trashes",
    }
)

JUNK_NAMES = frozenset({"Thumbs.db", "desktop.ini", ".DS_Store", ".localized"})
JUNK_PREFIXES = ("__MACOSX", "._")

MAX_TEXT_BYTES = 32 * 1024 * 1024  # 32 MB safety cap for search & replace
_FILE_ATTRIBUTE_HIDDEN = 0x2
_ZIP_JUNK_PARTS = {"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"}

Progress = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class CancelledError(RuntimeError):
    """Raised when the user cancels a long-running operation."""


def _noop_progress(done: int, total: int, message: str = "") -> None:
    """Default progress sink."""


# ---------------------------------------------------------------------------
# Paths, formatting and small helpers
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """Return the per-user folder that stores undo history.

    ``GANTZ_DATA_DIR`` overrides the location (used by the tests and by
    portable installs).  The directory is created on demand.
    """
    override = os.environ.get("GANTZ_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def human_size(num_bytes: float) -> str:
    """Format a byte count the way a file manager does."""
    if num_bytes < 0:
        return "-"
    if num_bytes < 1024:
        return f"{int(num_bytes)} B"
    units = ["KB", "MB", "GB", "TB", "PB"]
    value = float(num_bytes)
    for unit in units:
        value /= 1024.0
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{num_bytes} B"


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, sec = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def natural_key(value: str) -> list[tuple[int, object]]:
    """Sort key that orders ``file2`` before ``file10`` like Explorer does."""
    parts = re.split(r"(\d+)", value.casefold())
    return [(0, int(part)) if part.isdigit() else (1, part) for part in parts]


def sort_files(files: Iterable["FileInfo"], sort_by: str = "name") -> list["FileInfo"]:
    """Return *files* ordered by one of the supported sort modes."""
    files = list(files)
    key = (sort_by or "name").lower()
    if key in ("name", "natural", "name_asc"):
        return sorted(files, key=lambda f: natural_key(f.name))
    if key in ("name_desc", "natural_desc"):
        return sorted(files, key=lambda f: natural_key(f.name), reverse=True)
    if key == "date":
        return sorted(files, key=lambda f: (f.mtime, natural_key(f.name)))
    if key == "date_desc":
        return sorted(files, key=lambda f: (f.mtime, natural_key(f.name)), reverse=True)
    if key == "size":
        return sorted(files, key=lambda f: (f.size, natural_key(f.name)))
    if key == "size_desc":
        return sorted(files, key=lambda f: (f.size, natural_key(f.name)), reverse=True)
    raise ValueError(f"Unknown sort mode: {sort_by}")


def parse_extensions(text: str) -> set[str] | None:
    """Turn ``".txt, md; .csv"`` into ``{".txt", ".md", ".csv"}``.

    An empty string or ``*`` means "all files" and returns ``None``.
    """
    text = (text or "").strip()
    if not text or text == "*":
        return None
    result: set[str] = set()
    for chunk in re.split(r"[\s,;|]+", text):
        chunk = chunk.strip()
        if not chunk or chunk == "*":
            continue
        if chunk.lower().startswith("*."):
            chunk = chunk[1:]
        if not chunk.startswith("."):
            chunk = "." + chunk
        result.add(chunk.casefold())
    return result or None


def split_compound_suffix(name: str) -> tuple[str, str]:
    """Split ``archive.tar.gz`` into ``("archive", ".tar.gz")``.

    Plain ``Path.suffix`` would return ``.gz`` and turn the file into
    ``prefix.gz``.  Known compound archive suffixes are kept intact.
    """
    lowered = name.casefold()
    for compound in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst", ".tar.lz4"):
        if lowered.endswith(compound):
            return name[: -len(compound)], name[-len(compound):]
    suffix = Path(name).suffix
    if suffix and suffix != name:
        return name[: -len(suffix)], suffix
    return name, ""


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_component(name: str, replacement: str = "_") -> str:
    """Make *name* safe to use as a file or folder name on Windows.

    Characters that are illegal on Windows are replaced, reserved device
    names are prefixed and trailing dots/spaces are stripped.
    """
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', replacement, name or "")
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned:
        return ""
    stem = cleaned.split(".")[0].upper().strip()
    if stem in _WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def _temp_path_for(path: Path) -> Path:
    return path.with_name(f".gantz-tmp-{uuid.uuid4().hex}{path.suffix}")


def _check_cancel(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise CancelledError("Operation cancelled")


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    path: Path
    size: int
    mtime: float
    ext: str

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_text(self) -> bool:
        return self.ext.casefold() in TEXT_EXTENSIONS

    @property
    def modified(self) -> str:
        try:
            return time.strftime("%d %b %Y  %H:%M", time.localtime(self.mtime))
        except (OSError, OverflowError, ValueError):
            return "-"


def _is_hidden(path: Path, name: str) -> bool:
    if name.startswith("."):
        return True
    if os.name == "nt":
        try:
            attrs = getattr(path.stat(), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(attrs & _FILE_ATTRIBUTE_HIDDEN)
    return False


def scan_files(
    root: str | Path,
    recursive: bool = True,
    extensions: set[str] | None = None,
    include_hidden: bool = False,
    skip_dirs: frozenset[str] = SKIP_DIR_NAMES,
    progress: Progress = _noop_progress,
) -> list[FileInfo]:
    """List files under *root*, optionally filtered by extension.

    Unreadable folders are skipped instead of aborting the scan, which makes
    the tool usable on whole drives.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    found: list[FileInfo] = []

    def add(entry_path: Path) -> None:
        name = entry_path.name
        if not include_hidden and _is_hidden(entry_path, name):
            return
        ext = entry_path.suffix.casefold()
        if extensions is not None and ext not in extensions:
            return
        try:
            stat = entry_path.stat()
        except OSError:
            return
        if not _stat_is_file(stat):
            return
        found.append(FileInfo(path=entry_path, size=stat.st_size, mtime=stat.st_mtime, ext=ext))

    if recursive:
        skip_folded = {name.casefold() for name in skip_dirs}
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            current = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if d.casefold() not in skip_folded
                and (include_hidden or not d.startswith("."))
            )
            for name in filenames:
                add(current / name)
            if len(found) % 500 == 0:
                progress(len(found), 0, f"Scanning... {len(found):,} files")
    else:
        try:
            entries = list(os.scandir(root))
        except OSError:
            return []
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    add(Path(entry.path))
            except OSError:
                continue

    return sorted(found, key=lambda f: natural_key(f.name))


def _stat_is_file(info: os.stat_result) -> bool:
    return stat_module.S_ISREG(info.st_mode)


def looks_like_text(data: bytes) -> bool:
    """Cheap binary sniffing used before search & replace edits a file."""
    if not data:
        return True
    head = data[:8192]
    if head.startswith(codecs.BOM_UTF8) or head.startswith(codecs.BOM_UTF16_LE) or head.startswith(
        codecs.BOM_UTF16_BE
    ):
        return True
    if b"\x00" in head:
        return False
    # Reject files with a high ratio of control characters.
    text_bytes = sum(1 for byte in head if byte in (9, 10, 13) or 32 <= byte <= 126 or byte >= 128)
    return text_bytes / len(head) >= 0.85


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode *data* preserving the original encoding name for round-trips."""
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        return data.decode("utf-32"), "utf-32"
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16"), "utf-16"
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("File is not readable as text")


def _encode_text(text: str, encoding: str) -> tuple[bytes, str]:
    try:
        return text.encode(encoding), encoding
    except (UnicodeEncodeError, LookupError):
        return text.encode("utf-8"), "utf-8"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".gantz-{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
        try:
            shutil.copymode(path, temp)
        except OSError:
            pass
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Operation results / undo manifests
# ---------------------------------------------------------------------------


@dataclass
class OperationResult:
    """Aggregate outcome of one user-visible operation."""

    label: str = ""
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_written: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest_id: str = ""
    cancelled: bool = False
    duration: float = 0.0
    extra: dict = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.failed += 1
        if len(self.errors) < 200:
            self.errors.append(message)

    def warn(self, message: str) -> None:
        if len(self.warnings) < 200:
            self.warnings.append(message)

    def merge(self, other: "OperationResult") -> None:
        self.ok += other.ok
        self.skipped += other.skipped
        self.failed += other.failed
        self.bytes_written += other.bytes_written
        self.cancelled = self.cancelled or other.cancelled
        self.errors.extend(other.errors[: max(0, 200 - len(self.errors))])
        self.warnings.extend(other.warnings[: max(0, 200 - len(self.warnings))])

    @property
    def changed(self) -> bool:
        return self.ok > 0

    def summary(self) -> str:
        bits = [f"{self.ok:,} done"]
        if self.skipped:
            bits.append(f"{self.skipped:,} skipped")
        if self.failed:
            bits.append(f"{self.failed:,} failed")
        if self.bytes_written:
            bits.append(human_size(self.bytes_written))
        if self.duration:
            bits.append(format_duration(self.duration))
        return " | ".join(bits)


def _manifest_root() -> Path:
    root = data_dir() / "undo"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _new_manifest_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _save_manifest(manifest_id: str, label: str, operations: list[dict]) -> str:
    """Persist an undo manifest. Returns the id (empty when nothing to undo)."""
    if not operations:
        return ""
    doc = {
        "id": manifest_id,
        "app": APP_NAME,
        "version": APP_VERSION,
        "created": time.time(),
        "created_text": time.strftime("%d %b %Y %H:%M:%S"),
        "label": label,
        "operations": operations,
    }
    path = _manifest_root() / f"{manifest_id}.json"
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    _prune_manifests()
    return manifest_id


def _prune_manifests(keep: int = 25) -> None:
    root = _manifest_root()
    manifests = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in manifests[keep:]:
        _remove_manifest_files(stale)


def _remove_manifest_files(manifest_path: Path) -> None:
    backup_dir = manifest_path.with_suffix("")
    try:
        if backup_dir.is_dir():
            shutil.rmtree(backup_dir, ignore_errors=True)
        manifest_path.unlink(missing_ok=True)
    except OSError:
        pass


def list_manifests() -> list[dict]:
    """Return saved undo manifests, newest first (light metadata only)."""
    items: list[dict] = []
    for path in _manifest_root().glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append(
            {
                "id": doc.get("id", path.stem),
                "label": doc.get("label", "Operation"),
                "created": float(doc.get("created", path.stat().st_mtime)),
                "created_text": doc.get("created_text", ""),
                "count": len(doc.get("operations", [])),
                "path": str(path),
            }
        )
    return sorted(items, key=lambda item: item["created"], reverse=True)


def can_undo() -> bool:
    return bool(list_manifests())


def _undo_operation(op: dict) -> None:
    kind = op.get("op")
    if kind in ("rename", "move"):
        source = Path(op["to"])
        target = Path(op["from"])
        if not source.exists():
            raise FileNotFoundError(f"Missing: {source}")
        if target.exists():
            raise FileExistsError(f"Already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
    elif kind in ("copy", "create", "zip"):
        path = Path(op["path"] if kind != "copy" else op["to"])
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
        else:
            path.unlink(missing_ok=True)
    elif kind == "restore":
        path = Path(op["path"])
        backup = Path(op["backup"])
        if not backup.is_file():
            raise FileNotFoundError(f"Backup missing: {backup}")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, path)
    elif kind == "mkdir":
        path = Path(op["path"])
        try:
            path.rmdir()
        except OSError:
            pass
    else:
        raise ValueError(f"Unknown undo operation: {kind!r}")


def undo_last(
    progress: Progress = _noop_progress,
    should_cancel: CancelCheck | None = None,
) -> OperationResult | None:
    """Reverse the most recent Gantz operation. Returns ``None`` if empty."""
    started = time.perf_counter()
    manifests = list_manifests()
    if not manifests:
        return None

    meta = manifests[0]
    manifest_path = Path(meta["path"])
    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Undo history is unreadable: {exc}") from exc

    operations = doc.get("operations", [])
    result = OperationResult(label=f"Undo: {doc.get('label', 'operation')}")
    total = len(operations)
    for index, op in enumerate(reversed(operations)):
        _check_cancel(should_cancel)
        try:
            _undo_operation(op)
            result.ok += 1
        except Exception as exc:  # keep undoing the rest
            result.fail(f"{Path(op.get('path') or op.get('to') or '?').name}: {exc}")
        progress(index + 1, total, f"Undoing {index + 1:,}/{total:,}")

    result.duration = time.perf_counter() - started
    if result.failed == 0:
        _remove_manifest_files(manifest_path)
    return result


# ---------------------------------------------------------------------------
# Bulk rename
# ---------------------------------------------------------------------------

NUMBERING_MODES = ("all", "after_first", "none")


@dataclass
class RenameItem:
    source: Path
    target: Path
    status: str = "ready"  # ready | exists | duplicate | unchanged | invalid
    note: str = ""

    @property
    def target_name(self) -> str:
        return self.target.name


@dataclass
class RenamePlan:
    prefix: str
    numbering: str
    items: list[RenameItem] = field(default_factory=list)

    @property
    def ready_count(self) -> int:
        return sum(1 for item in self.items if item.status == "ready")

    @property
    def conflict_count(self) -> int:
        return sum(1 for item in self.items if item.status in ("duplicate", "exists", "invalid"))

    @property
    def unchanged_count(self) -> int:
        return sum(1 for item in self.items if item.status == "unchanged")


def build_rename_plan(
    files: Sequence[FileInfo],
    prefix: str,
    numbering: str = "after_first",
    start: int = 1,
    padding: int = 0,
    separator: str = "",
    sort_by: str = "name",
) -> RenamePlan:
    """Create a rename preview without touching the filesystem.

    ``numbering`` is one of ``all`` (every file gets a number), ``after_first``
    (``yori.ext``, ``yori1.ext``, ... - the original Gantz behaviour) or
    ``none``.
    """
    clean_prefix = sanitize_component(prefix)
    if not clean_prefix:
        raise ValueError("Base name cannot be empty")
    if numbering not in NUMBERING_MODES:
        raise ValueError(f"Unknown numbering mode: {numbering}")
    start = max(0, int(start))
    padding = max(0, min(int(padding), 12))
    clean_sep = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", separator or "")

    ordered = sort_files(files, sort_by)
    plan = RenamePlan(prefix=clean_prefix, numbering=numbering)
    number = start

    for index, info in enumerate(ordered):
        needs_number = numbering == "all" or (numbering == "after_first" and index > 0)
        if needs_number:
            digits = str(number).zfill(padding) if padding else str(number)
            base = f"{clean_prefix}{clean_sep}{digits}"
            number += 1
        else:
            base = clean_prefix

        _stem, suffix = split_compound_suffix(info.path.name)
        target = info.path.with_name(base + suffix)
        status = "ready"
        note = ""
        if target == info.path:
            status = "unchanged"
            note = "Already named correctly"
        elif not base:
            status = "invalid"
            note = "Empty name"
        plan.items.append(RenameItem(source=info.path, target=target, status=status, note=note))

    # Targets must be unique...
    seen: dict[str, int] = {}
    for item in plan.items:
        key = str(item.target).casefold()
        seen[key] = seen.get(key, 0) + 1
    for item in plan.items:
        if seen[str(item.target).casefold()] > 1 and item.status in ("ready", "exists"):
            item.status = "duplicate"
            item.note = "Two files would get the same name"

    # ...and they may only collide with sources that are themselves moving away.
    moving = {
        str(item.source).casefold()
        for item in plan.items
        if item.status in ("ready", "exists") and item.target != item.source
    }
    for item in plan.items:
        if item.status != "ready":
            continue
        try:
            exists = item.target.exists()
        except OSError:
            exists = False
        if exists and str(item.target).casefold() not in moving:
            item.status = "exists"
            item.note = "A file with this name already exists"

    return plan


def _apply_in_place_renames(
    pairs: list[tuple[Path, Path]],
    result: OperationResult,
    progress: Progress,
    should_cancel: CancelCheck | None,
    operations: list[dict],
    backup_dir: Path,
    overwrite: bool,
) -> None:
    """Rename *pairs* in place.

    When no target collides with another source the rename is done directly
    (one syscall per file).  Only genuine name swaps fall back to the slower
    two-phase temporary-name strategy.
    """
    total = len(pairs)
    source_keys = {str(source).casefold() for source, _target in pairs}
    needs_staging = any(
        str(target).casefold() in source_keys
        for source, target in pairs
        if source != target
    )

    if not needs_staging:
        for index, (source, target) in enumerate(pairs):
            _check_cancel(should_cancel)
            try:
                if target.exists():
                    if not overwrite:
                        result.skipped += 1
                        result.warn(f"Skipped {source.name}: {target.name} already exists")
                        continue
                    if target.is_dir():
                        raise IsADirectoryError(f"{target} is a folder")
                    backup_path = _backup_file(target, backup_dir)
                    operations.append(
                        {"op": "restore", "path": str(target), "backup": str(backup_path)}
                    )
                    target.unlink()
                os.replace(source, target)
                result.ok += 1
                operations.append({"op": "rename", "from": str(source), "to": str(target)})
            except Exception as exc:
                result.fail(f"{source.name}: {exc}")
            progress(index + 1, total, f"Renaming {index + 1:,}/{total:,}")
        return

    staged: list[tuple[Path, Path, Path]] = []  # source, temp, target
    try:
        for index, (source, target) in enumerate(pairs):
            _check_cancel(should_cancel)
            temp = _temp_path_for(source)
            os.replace(source, temp)
            staged.append((source, temp, target))
            progress(index + 1, total * 2, f"Staging {index + 1:,}/{total:,}")

        for index, (source, temp, target) in enumerate(staged):
            _check_cancel(should_cancel)
            try:
                if target.exists():
                    if not overwrite:
                        os.replace(temp, source)
                        result.skipped += 1
                        result.warn(f"Skipped {source.name}: {target.name} already exists")
                        continue
                    if target.is_dir():
                        raise IsADirectoryError(f"{target} is a folder")
                    backup_path = _backup_file(target, backup_dir)
                    operations.append(
                        {"op": "restore", "path": str(target), "backup": str(backup_path)}
                    )
                    target.unlink()
                os.replace(temp, target)
                result.ok += 1
                operations.append({"op": "rename", "from": str(source), "to": str(target)})
            except Exception as exc:
                try:
                    os.replace(temp, source)
                except OSError:
                    pass
                result.fail(f"{source.name}: {exc}")
            progress(total + index + 1, total * 2, f"Renaming {index + 1:,}/{total:,}")
    except CancelledError:
        for source, temp, _target in reversed(staged):
            if temp.exists():
                try:
                    os.replace(temp, source)
                except OSError:
                    pass
        raise


def apply_rename_plan(
    plan: RenamePlan,
    mode: str = "rename",
    destination: str | Path | None = None,
    overwrite: bool = False,
    progress: Progress = _noop_progress,
    should_cancel: CancelCheck | None = None,
) -> OperationResult:
    """Apply a :class:`RenamePlan`.

    ``mode`` is ``rename`` (in place), ``copy`` or ``move`` (into
    *destination*).  Every successful change is written to the undo history.
    """
    if mode not in ("rename", "copy", "move"):
        raise ValueError(f"Unknown mode: {mode}")

    started = time.perf_counter()
    manifest_id = _new_manifest_id()
    operations: list[dict] = []
    result = OperationResult(label=f"Rename to '{plan.prefix}'")
    result.manifest_id = manifest_id
    result.extra["overwrite"] = overwrite

    selected = [
        item for item in plan.items
        if item.status == "ready" or (overwrite and item.status == "exists")
    ]
    result.skipped += len(plan.items) - len(selected)
    dest_dir = Path(destination).expanduser() if destination else None

    try:
        if mode in ("copy", "move"):
            if dest_dir is None:
                raise ValueError("Choose an output folder for copy/move mode")
            dest_dir.mkdir(parents=True, exist_ok=True)

        if mode == "rename" or (
            mode == "move"
            and dest_dir is not None
            and selected
            and all(item.source.parent.resolve() == dest_dir.resolve() for item in selected)
        ):
            pairs = [(item.source, item.target) for item in selected]
            _apply_in_place_renames(
                pairs,
                result,
                progress,
                should_cancel,
                operations,
                backup_dir=_manifest_root() / manifest_id / "backup",
                overwrite=overwrite,
            )
        elif mode == "copy":
            assert dest_dir is not None
            total = len(selected)
            for index, item in enumerate(selected):
                _check_cancel(should_cancel)
                target = dest_dir / item.target.name
                try:
                    if target.exists():
                        if not overwrite:
                            result.skipped += 1
                            result.warn(f"Skipped {item.source.name}: {target.name} already exists")
                            continue
                        if target.is_dir():
                            raise IsADirectoryError(f"{target} is a folder")
                        backup_path = _backup_file(target, _manifest_root() / manifest_id / "backup")
                        operations.append(
                            {"op": "restore", "path": str(target), "backup": str(backup_path)}
                        )
                    shutil.copy2(item.source, target)
                    result.ok += 1
                    result.bytes_written += item.source.stat().st_size
                    operations.append({"op": "copy", "to": str(target)})
                except Exception as exc:
                    result.fail(f"{item.source.name}: {exc}")
                progress(index + 1, total, f"Copying {index + 1:,}/{total:,}")
        elif mode == "move":
            assert dest_dir is not None
            total = len(selected)
            for index, item in enumerate(selected):
                _check_cancel(should_cancel)
                target = dest_dir / item.target.name
                try:
                    if target.resolve() == item.source.resolve():
                        result.skipped += 1
                        continue
                    if target.exists():
                        if not overwrite:
                            result.skipped += 1
                            result.warn(f"Skipped {item.source.name}: {target.name} already exists")
                            continue
                        if target.is_dir():
                            raise IsADirectoryError(f"{target} is a folder")
                        backup_path = _backup_file(target, _manifest_root() / manifest_id / "backup")
                        operations.append(
                            {"op": "restore", "path": str(target), "backup": str(backup_path)}
                        )
                        target.unlink()
                    shutil.move(str(item.source), str(target))
                    result.ok += 1
                    operations.append({"op": "move", "from": str(item.source), "to": str(target)})
                except Exception as exc:
                    result.fail(f"{item.source.name}: {exc}")
                progress(index + 1, total, f"Moving {index + 1:,}/{total:,}")
    except CancelledError:
        result.cancelled = True

    result.duration = time.perf_counter() - started
    result.manifest_id = _save_manifest(manifest_id, result.label, operations)
    if result.ok == 0:
        result.label = f"{result.label} (nothing changed)"
    return result


# ---------------------------------------------------------------------------
# Search & replace
# ---------------------------------------------------------------------------


@dataclass
class MatchPreview:
    line_number: int
    before: str
    after: str


@dataclass
class ReplaceItem:
    file: FileInfo
    matches: int = 0
    previews: list[MatchPreview] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.matches > 0


@dataclass
class ReplacePlan:
    find: str
    replace: str
    case_sensitive: bool = False
    regex: bool = False
    whole_word: bool = False
    items: list[ReplaceItem] = field(default_factory=list)

    @property
    def total_matches(self) -> int:
        return sum(item.matches for item in self.items)

    @property
    def matching_files(self) -> int:
        return sum(1 for item in self.items if item.matches and not item.error)

    def compile(self) -> re.Pattern[str]:
        return compile_search(self.find, self.regex, self.case_sensitive, self.whole_word)


def compile_search(
    find: str, regex: bool = False, case_sensitive: bool = False, whole_word: bool = False
) -> re.Pattern[str]:
    if not find:
        raise ValueError("Search text cannot be empty")
    pattern = find if regex else re.escape(find)
    if whole_word:
        pattern = rf"(?<!\w)(?:{pattern})(?!\w)"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(pattern, flags)


def _preview_line(text: str, limit: int = 240) -> str:
    collapsed = text.replace("\t", "    ").strip()
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def build_replace_plan(
    files: Sequence[FileInfo],
    find: str,
    replace: str = "",
    case_sensitive: bool = False,
    regex: bool = False,
    whole_word: bool = False,
    max_bytes: int = MAX_TEXT_BYTES,
    preview_limit: int = 3,
    progress: Progress = _noop_progress,
    should_cancel: CancelCheck | None = None,
) -> ReplacePlan:
    """Scan *files* for matches and build a preview without writing anything."""
    pattern = compile_search(find, regex, case_sensitive, whole_word)
    plan = ReplacePlan(
        find=find,
        replace=replace,
        case_sensitive=case_sensitive,
        regex=regex,
        whole_word=whole_word,
    )
    total = len(files)
    for index, info in enumerate(files):
        _check_cancel(should_cancel)
        item = ReplaceItem(file=info)
        plan.items.append(item)
        try:
            if info.size > max_bytes:
                item.error = f"Larger than {human_size(max_bytes)} - skipped"
                continue
            data = info.path.read_bytes()
            if not looks_like_text(data):
                item.error = "Binary file - skipped"
                continue
            text, _encoding = decode_bytes(data)
            matches = sum(1 for _ in pattern.finditer(text))
            item.matches = matches
            if matches and preview_limit:
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if len(item.previews) >= preview_limit:
                        break
                    replaced, count = pattern.subn(replace, line)
                    if count:
                        item.previews.append(
                            MatchPreview(
                                line_number=line_number,
                                before=_preview_line(line),
                                after=_preview_line(replaced),
                            )
                        )
        except (OSError, UnicodeError) as exc:
            item.error = str(exc)
        if index % 25 == 0 or index == total - 1:
            progress(index + 1, total, f"Scanning {index + 1:,}/{total:,} files")
    return plan


def _backup_file(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{uuid.uuid4().hex[:10]}-{sanitize_component(path.name) or 'file'}"
    shutil.copy2(path, target)
    return target


def apply_replace_plan(
    plan: ReplacePlan,
    backup: bool = True,
    progress: Progress = _noop_progress,
    should_cancel: CancelCheck | None = None,
) -> OperationResult:
    """Apply a :class:`ReplacePlan`, keeping copies for undo."""
    started = time.perf_counter()
    pattern = plan.compile()
    manifest_id = _new_manifest_id()
    backup_dir = _manifest_root() / manifest_id / "backup"
    operations: list[dict] = []
    result = OperationResult(label=f"Replace '{plan.find}'")

    candidates = [item for item in plan.items if item.ok]
    total = len(candidates)
    for index, item in enumerate(candidates):
        _check_cancel(should_cancel)
        try:
            data = item.file.path.read_bytes()
            text, encoding = decode_bytes(data)
            new_text, count = pattern.subn(plan.replace, text)
            if count == 0 or new_text == text:
                result.skipped += 1
                continue
            encoded, used_encoding = _encode_text(new_text, encoding)
            backup_path = _backup_file(item.file.path, backup_dir) if backup else None
            _atomic_write(item.file.path, encoded)
            result.ok += 1
            result.bytes_written += len(encoded)
            if used_encoding != encoding:
                result.warn(
                    f"{item.file.name}: saved as {used_encoding} "
                    f"(characters did not fit {encoding})"
                )
            if backup_path is not None:
                operations.append(
                    {
                        "op": "restore",
                        "path": str(item.file.path),
                        "backup": str(backup_path),
                    }
                )
        except Exception as exc:
            result.fail(f"{item.file.name}: {exc}")
        progress(index + 1, total, f"Replacing {index + 1:,}/{total:,}")

    result.duration = time.perf_counter() - started
    result.manifest_id = _save_manifest(manifest_id, result.label, operations)
    return result


# ---------------------------------------------------------------------------
# Zip archives
# ---------------------------------------------------------------------------


@dataclass
class ZipEntry:
    name: str
    size: int
    compressed: int
    is_dir: bool
    encrypted: bool
    date: str


@dataclass
class ZipInfoSummary:
    path: Path
    entries: list[ZipEntry] = field(default_factory=list)
    error: str = ""

    @property
    def file_count(self) -> int:
        return sum(1 for entry in self.entries if not entry.is_dir)

    @property
    def total_size(self) -> int:
        return sum(entry.size for entry in self.entries if not entry.is_dir)

    @property
    def encrypted(self) -> bool:
        return any(entry.encrypted for entry in self.entries)


def read_zip(path: str | Path) -> ZipInfoSummary:
    """Read a zip's central directory (fast - nothing is extracted)."""
    path = Path(path)
    summary = ZipInfoSummary(path=path)
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                summary.entries.append(
                    ZipEntry(
                        name=info.filename,
                        size=info.file_size,
                        compressed=info.compress_size,
                        is_dir=info.is_dir(),
                        encrypted=bool(info.flag_bits & 0x1),
                        date="%04d-%02d-%02d %02d:%02d"
                        % (
                            info.date_time[0],
                            info.date_time[1],
                            info.date_time[2],
                            info.date_time[3],
                            info.date_time[4],
                        ),
                    )
                )
    except (OSError, zipfile.BadZipFile) as exc:
        summary.error = str(exc)
    return summary


def _is_junk_zip_name(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    if any(part in _ZIP_JUNK_PARTS for part in parts):
        return True
    return any(part.startswith("._") for part in parts)


def _safe_member_parts(name: str) -> list[str] | None:
    """Split a zip member name into safe path components.

    Returns ``None`` for entries that try to escape the destination
    ("zip slip") or use absolute paths.
    """
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
        return None
    parts: list[str] = []
    for part in normalised.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(sanitize_component(part))
    return parts or None


def _is_within(path: Path, root: Path) -> bool:
    """True when *path* stays inside *root* (guards against zip-slip)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _missing_parents(target: Path, root: Path) -> list[Path]:
    """Folders between *root* and *target* that do not exist yet, outermost first."""
    missing: list[Path] = []
    parent = target.parent
    while parent != root and parent != parent.parent:
        if parent.exists():
            break
        missing.append(parent)
        parent = parent.parent
    missing.reverse()
    return missing


def _extract_one(
    zip_path: Path,
    root: Path,
    manifest_id: str,
    overwrite: bool,
    skip_junk: bool,
    password: str,
    operations: list[dict],
    created_dirs: set[str],
    progress: Progress,
    progress_base: int,
    progress_total: int,
    should_cancel: CancelCheck | None,
) -> OperationResult:
    result = OperationResult(label=f"Extract {zip_path.name}")
    pwd = password.encode("utf-8") if password else None
    backup_dir = _manifest_root() / manifest_id / "backup"
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if any(info.flag_bits & 0x1 for info in infos) and not pwd:
            raise ValueError("Archive is password protected - enter the password")

        files = [
            info
            for info in infos
            if not info.is_dir() and not (skip_junk and _is_junk_zip_name(info.filename))
        ]
        for index, info in enumerate(files):
            _check_cancel(should_cancel)
            parts = _safe_member_parts(info.filename)
            if parts is None:
                result.fail(f"Blocked unsafe path: {info.filename}")
                continue
            target = root.joinpath(*parts)
            if not _is_within(target, root):
                result.fail(f"Blocked unsafe path: {info.filename}")
                continue

            try:
                if target.exists():
                    if target.is_dir():
                        raise IsADirectoryError(f"{target.name} is a folder")
                    if not overwrite:
                        result.skipped += 1
                        continue
                    backup_path = _backup_file(target, backup_dir)
                    operations.append(
                        {"op": "restore", "path": str(target), "backup": str(backup_path)}
                    )

                for folder in _missing_parents(target, root):
                    folder.mkdir(parents=True, exist_ok=True)
                    key = str(folder)
                    if key not in created_dirs:
                        created_dirs.add(key)
                        operations.append({"op": "mkdir", "path": key})

                with archive.open(info, pwd=pwd) as source, open(target, "wb") as dest:
                    shutil.copyfileobj(source, dest, 1024 * 1024)
                try:
                    stamp = time.mktime(info.date_time + (0, 0, -1))
                    os.utime(target, (stamp, stamp))
                except (OSError, OverflowError, ValueError):
                    pass
                result.ok += 1
                result.bytes_written += info.file_size
                operations.append({"op": "create", "path": str(target)})
            except Exception as exc:  # one bad entry must not stop the archive
                result.fail(f"{info.filename}: {exc}")
            progress(
                progress_base + index + 1,
                progress_total,
                f"Extracting {index + 1:,}/{len(files):,}",
            )
    return result


def extract_zips(
    zip_paths: Sequence[str | Path],
    destination: str | Path,
    subfolder: bool = False,
    overwrite: bool = False,
    skip_junk: bool = True,
    password: str = "",
    progress: Progress = _noop_progress,
    should_cancel: CancelCheck | None = None,
) -> OperationResult:
    """Extract one or more archives into *destination* with undo support."""
    started = time.perf_counter()
    dest = Path(destination).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    manifest_id = _new_manifest_id()
    operations: list[dict] = []
    created_dirs: set[str] = set()
    result = OperationResult(label="Extract archives")
    archives = [Path(p) for p in zip_paths]
    if not archives:
        raise ValueError("No archives selected")

    # Count the work up-front so the progress bar is accurate.
    totals: dict[Path, int] = {}
    for archive_path in archives:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                totals[archive_path] = sum(
                    1
                    for info in archive.infolist()
                    if not info.is_dir() and not (skip_junk and _is_junk_zip_name(info.filename))
                )
        except (OSError, zipfile.BadZipFile):
            totals[archive_path] = 0
    grand_total = max(1, sum(totals.values()))
    done = 0
    used_roots: set[str] = set()

    for archive_path in archives:
        _check_cancel(should_cancel)
        if not archive_path.is_file():
            result.fail(f"{archive_path.name}: file not found")
            done += totals.get(archive_path, 0)
            continue

        root = dest
        if subfolder:
            folder_name = sanitize_component(archive_path.stem) or "archive"
            candidate = folder_name
            counter = 2
            while candidate.casefold() in used_roots:
                candidate = f"{folder_name} ({counter})"
                counter += 1
            used_roots.add(candidate.casefold())
            root = dest / candidate
            existed = root.exists()
            root.mkdir(parents=True, exist_ok=True)
            if not existed:
                created_dirs.add(str(root))
                operations.append({"op": "mkdir", "path": str(root)})

        try:
            part = _extract_one(
                archive_path,
                root,
                manifest_id=manifest_id,
                overwrite=overwrite,
                skip_junk=skip_junk,
                password=password,
                operations=operations,
                created_dirs=created_dirs,
                progress=progress,
                progress_base=done,
                progress_total=grand_total,
                should_cancel=should_cancel,
            )
            result.merge(part)
        except CancelledError:
            result.cancelled = True
            break
        except Exception as exc:
            result.fail(f"{archive_path.name}: {exc}")
        done += totals.get(archive_path, 0)

    result.duration = time.perf_counter() - started
    result.label = f"Extract {len(archives)} archive{'s' if len(archives) != 1 else ''}"
    if result.cancelled:
        result.label += " (cancelled)"
    result.manifest_id = _save_manifest(manifest_id, result.label, operations)
    return result


def _iter_zip_sources(
    sources: Sequence[Path], skip_path: Path | None = None
) -> Iterable[tuple[Path, str]]:
    """Yield ``(file, archive member name)`` for the given files and folders."""
    skip_key = (
        os.path.normcase(os.path.abspath(skip_path)) if skip_path is not None else None
    )
    skip_folded = {name.casefold() for name in SKIP_DIR_NAMES}
    for source in sources:
        if source.is_file():
            if skip_key is not None and os.path.normcase(os.path.abspath(source)) == skip_key:
                continue
            yield source, source.name
        elif source.is_dir():
            for dirpath, dirnames, filenames in os.walk(source):
                current = Path(dirpath)
                dirnames[:] = sorted(d for d in dirnames if d.casefold() not in skip_folded)
                for name in sorted(filenames, key=natural_key):
                    if name in JUNK_NAMES or name.startswith("._"):
                        continue
                    full = current / name
                    if skip_key is not None and os.path.normcase(os.path.abspath(full)) == skip_key:
                        continue
                    yield full, full.relative_to(source.parent).as_posix()


def count_zip_sources(sources: Sequence[Path], skip_path: Path | None = None) -> int:
    return sum(1 for _ in _iter_zip_sources(sources, skip_path))


def create_zip(
    sources: Sequence[str | Path],
    zip_path: str | Path,
    compression: str = "balanced",
    overwrite: bool = False,
    progress: Progress = _noop_progress,
    should_cancel: CancelCheck | None = None,
) -> OperationResult:
    """Create a .zip from files/folders. Undo deletes the archive again.

    The archive is written next to the target and moved into place at the
    end, so an existing archive is never damaged by a cancelled or failed
    run.
    """
    started = time.perf_counter()
    source_paths = [Path(s).expanduser().resolve() for s in sources if Path(s).exists()]
    if not source_paths:
        raise ValueError("Nothing selected to compress")

    target = Path(zip_path).expanduser()
    if target.suffix.casefold() != ".zip":
        target = target.with_name(target.name + ".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target.name} already exists")
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"{target} is a folder")

    levels = {"store": 0, "fast": 1, "balanced": 6, "max": 9}
    level = levels.get(compression, 6)
    deflated = level > 0
    entries = list(_iter_zip_sources(source_paths, target))
    if not entries:
        raise ValueError("Nothing to compress - the selection is empty")
    total = max(1, len(entries))
    result = OperationResult(label=f"Create {target.name}")
    temp = target.with_name(f".gantz-{uuid.uuid4().hex}.zip")

    try:
        archive_kwargs = {"compression": zipfile.ZIP_DEFLATED if deflated else zipfile.ZIP_STORED}
        if deflated:
            archive_kwargs["compresslevel"] = level
        with zipfile.ZipFile(temp, "w", **archive_kwargs) as archive:
            for index, (path, arcname) in enumerate(entries):
                _check_cancel(should_cancel)
                try:
                    archive.write(path, arcname)
                    result.ok += 1
                    result.bytes_written += path.stat().st_size
                except Exception as exc:
                    result.fail(f"{path.name}: {exc}")
                progress(index + 1, total, f"Compressing {index + 1:,}/{total:,}")
        os.replace(temp, target)
    except CancelledError:
        result.cancelled = True
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    result.duration = time.perf_counter() - started
    if result.cancelled:
        result.label += " (cancelled)"
    result.manifest_id = _save_manifest(
        _new_manifest_id(),
        result.label,
        [{"op": "zip", "path": str(target)}] if not result.cancelled else [],
    )
    return result


def clear_undo_history() -> int:
    """Delete every saved undo manifest and its backups. Returns the count."""
    root = _manifest_root()
    manifests = list(root.glob("*.json"))
    for path in manifests:
        _remove_manifest_files(path)
    return len(manifests)
