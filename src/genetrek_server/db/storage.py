"""Writing the parquet/YAML pair to disk without leaving it inconsistent.

The two files must agree, so a save has to be all-or-nothing. POSIX gives no way
to swap two files in one atomic step, so this module does the next best thing:

* each file is written to a temporary sibling, flushed, and ``fsync``-ed before
  anything is swapped, so neither destination is ever partially written;
* the two ``os.replace`` calls happen back to back, with nothing between them;
* a marker file is created before the swaps and removed after, so a process
  killed *between* the two swaps leaves evidence -- :func:`interrupted_save`
  reports it and the backup is there to recover from.

The residual window is two syscalls wide. It cannot be closed without a real
journal, so it is made detectable instead of pretended away.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

#: Name of the marker written while destinations are being swapped.
MARKER_NAME = ".save-in-progress"

#: Timestamp format for backup directory names.
BACKUP_STAMP = "%Y%m%d-%H%M%S"


@dataclass(frozen=True)
class SaveResult:
    """What a save did.

    Attributes:
        backup: Directory the previous files were copied to, if any.
        pruned: Backup directories removed to honour the retention limit.
        n_operations: Number of history records appended.
    """

    backup: Path | None
    pruned: list[Path]
    n_operations: int


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def interrupted_save(resources_marker_dir: Path) -> bool:
    """Whether a previous save was interrupted mid-swap."""
    return (resources_marker_dir / MARKER_NAME).exists()


def clear_interrupted_marker(marker_dir: Path) -> None:
    """Remove the interrupted-save marker, acknowledging the inconsistency."""
    (marker_dir / MARKER_NAME).unlink(missing_ok=True)


def write_pair(
    marker_dir: Path,
    writes: Sequence[tuple[Path, Callable[[Path], None]]],
) -> None:
    """Write several files, then swap them all into place.

    Args:
        marker_dir: Directory the in-progress marker is written to. Usually the
            directory holding the parquet.
        writes: ``(destination, writer)`` pairs. Each ``writer`` is called with a
            temporary path and must write the complete file there.

    Raises:
        OSError: If a write or a swap fails. Temporary files are cleaned up and
            destinations are left untouched when the failure happens before the
            first swap.
    """
    marker_dir.mkdir(parents=True, exist_ok=True)
    # Tracked separately from `staged` so a writer that raises still gets its
    # temporary file cleaned up. After a successful replace the path is gone, so
    # unlinking it again is a harmless no-op.
    created: list[Path] = []
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, writer in writes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            handle, raw = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
            os.close(handle)
            temporary = Path(raw)
            created.append(temporary)
            writer(temporary)
            _fsync_file(temporary)
            staged.append((temporary, destination))

        _fsync_dir(marker_dir)
        marker = marker_dir / MARKER_NAME
        marker.write_text(utc_now(), encoding="utf-8")
        try:
            for temporary, destination in staged:
                os.replace(temporary, destination)
        finally:
            marker.unlink(missing_ok=True)
    finally:
        for temporary in created:
            temporary.unlink(missing_ok=True)


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - not supported on every filesystem
        pass
    finally:
        os.close(fd)


def make_backup(
    backups_root: Path,
    files: Iterable[Path],
    *,
    stamp: str | None = None,
) -> Path | None:
    """Copy the given files into a timestamped backup directory.

    Returns:
        The backup directory, or ``None`` if none of the files existed yet.
    """
    existing = [path for path in files if path.is_file()]
    if not existing:
        return None
    label = stamp or datetime.now(tz=UTC).astimezone().strftime(BACKUP_STAMP)
    target = backups_root / label
    suffix = 1
    while target.exists():
        suffix += 1
        target = backups_root / f"{label}-{suffix}"
    target.mkdir(parents=True)
    for path in existing:
        shutil.copy2(path, target / path.name)
    return target


def prune_backups(backups_root: Path, keep: int) -> list[Path]:
    """Delete all but the ``keep`` newest backup directories.

    Returns:
        The directories that were removed, oldest first.
    """
    if keep < 0 or not backups_root.is_dir():
        return []
    directories = sorted(
        (p for p in backups_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    doomed = directories[: max(0, len(directories) - keep)]
    for directory in doomed:
        shutil.rmtree(directory)
    return doomed


def append_history(path: Path, records: Iterable[dict[str, Any]]) -> int:
    """Append records to the JSON-lines mutation log.

    Returns:
        The number of records written.
    """
    lines = [json.dumps(record, default=str, sort_keys=True) for record in records]
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return len(lines)


def read_history(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read the mutation log, newest last.

    Malformed lines are skipped rather than raising: the log is an audit trail,
    and a corrupt line should not make it unreadable.
    """
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records[-limit:] if limit else records
