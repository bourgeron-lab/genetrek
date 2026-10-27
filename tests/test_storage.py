"""Tests for the crash-safety and bookkeeping primitives behind save()."""

from __future__ import annotations

import json

import pytest

from genetrek.db import storage


class TestWritePair:
    def test_both_files_appear(self, tmp_path):
        a, b = tmp_path / "a.txt", tmp_path / "sub" / "b.txt"
        storage.write_pair(
            tmp_path,
            [
                (a, lambda p: p.write_text("A", encoding="utf-8")),
                (b, lambda p: p.write_text("B", encoding="utf-8")),
            ],
        )
        assert a.read_text(encoding="utf-8") == "A"
        assert b.read_text(encoding="utf-8") == "B"

    def test_existing_files_are_replaced(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("old", encoding="utf-8")
        storage.write_pair(
            tmp_path, [(a, lambda p: p.write_text("new", encoding="utf-8"))]
        )
        assert a.read_text(encoding="utf-8") == "new"

    def test_a_failing_writer_leaves_destinations_untouched(self, tmp_path):
        a, b = tmp_path / "a.txt", tmp_path / "b.txt"
        a.write_text("original", encoding="utf-8")
        b.write_text("original", encoding="utf-8")

        def explode(_path):
            msg = "writer failed"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError, match="writer failed"):
            storage.write_pair(
                tmp_path,
                [(a, lambda p: p.write_text("new", encoding="utf-8")), (b, explode)],
            )
        assert a.read_text(encoding="utf-8") == "original"
        assert b.read_text(encoding="utf-8") == "original"

    def test_temporary_files_are_cleaned_up_on_failure(self, tmp_path):
        def explode(_path):
            msg = "nope"
            raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            storage.write_pair(tmp_path, [(tmp_path / "a.txt", explode)])
        assert list(tmp_path.glob(".*tmp")) == []

    def test_no_marker_is_left_behind_on_success(self, tmp_path):
        storage.write_pair(
            tmp_path,
            [(tmp_path / "a.txt", lambda p: p.write_text("A", encoding="utf-8"))],
        )
        assert not storage.interrupted_save(tmp_path)

    def test_a_stray_marker_is_detected(self, tmp_path):
        (tmp_path / storage.MARKER_NAME).write_text("2026-01-01", encoding="utf-8")
        assert storage.interrupted_save(tmp_path)
        storage.clear_interrupted_marker(tmp_path)
        assert not storage.interrupted_save(tmp_path)

    def test_clearing_an_absent_marker_is_harmless(self, tmp_path):
        storage.clear_interrupted_marker(tmp_path)


class TestBackups:
    def test_backup_copies_existing_files(self, tmp_path):
        source = tmp_path / "data.txt"
        source.write_text("payload", encoding="utf-8")
        target = storage.make_backup(tmp_path / "b", [source], stamp="20260101-000000")
        assert target is not None
        assert (target / "data.txt").read_text(encoding="utf-8") == "payload"

    def test_backup_skips_absent_files(self, tmp_path):
        assert storage.make_backup(tmp_path / "b", [tmp_path / "absent.txt"]) is None

    def test_backup_names_do_not_collide(self, tmp_path):
        source = tmp_path / "data.txt"
        source.write_text("x", encoding="utf-8")
        first = storage.make_backup(tmp_path / "b", [source], stamp="20260101-000000")
        second = storage.make_backup(tmp_path / "b", [source], stamp="20260101-000000")
        assert first != second
        assert second is not None
        assert second.name.endswith("-2")

    def test_prune_keeps_the_newest(self, tmp_path):
        root = tmp_path / "b"
        for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
            (root / stamp).mkdir(parents=True)
        removed = storage.prune_backups(root, keep=2)
        assert [p.name for p in removed] == ["20260101-000000"]
        assert sorted(p.name for p in root.iterdir()) == [
            "20260102-000000",
            "20260103-000000",
        ]

    def test_prune_zero_removes_everything(self, tmp_path):
        root = tmp_path / "b"
        (root / "20260101-000000").mkdir(parents=True)
        assert len(storage.prune_backups(root, keep=0)) == 1
        assert list(root.iterdir()) == []

    def test_prune_on_a_missing_directory_is_harmless(self, tmp_path):
        assert storage.prune_backups(tmp_path / "absent", keep=3) == []

    def test_prune_keeps_all_when_under_the_limit(self, tmp_path):
        root = tmp_path / "b"
        (root / "20260101-000000").mkdir(parents=True)
        assert storage.prune_backups(root, keep=5) == []


class TestHistory:
    def test_append_and_read(self, tmp_path):
        path = tmp_path / "history.jsonl"
        assert storage.append_history(path, [{"op": "a"}, {"op": "b"}]) == 2
        assert [r["op"] for r in storage.read_history(path)] == ["a", "b"]

    def test_append_is_additive(self, tmp_path):
        path = tmp_path / "history.jsonl"
        storage.append_history(path, [{"op": "a"}])
        storage.append_history(path, [{"op": "b"}])
        assert len(storage.read_history(path)) == 2

    def test_append_nothing_writes_nothing(self, tmp_path):
        path = tmp_path / "history.jsonl"
        assert storage.append_history(path, []) == 0
        assert not path.exists()

    def test_read_missing_file(self, tmp_path):
        assert storage.read_history(tmp_path / "absent.jsonl") == []

    def test_limit_returns_the_newest(self, tmp_path):
        path = tmp_path / "history.jsonl"
        storage.append_history(path, [{"op": str(i)} for i in range(5)])
        assert [r["op"] for r in storage.read_history(path, limit=2)] == ["3", "4"]

    def test_corrupt_lines_are_skipped_not_fatal(self, tmp_path):
        """An audit trail must stay readable even if a line got mangled."""
        path = tmp_path / "history.jsonl"
        path.write_text(
            '{"op": "good"}\nnot json at all\n\n{"op": "also good"}\n',
            encoding="utf-8",
        )
        assert [r["op"] for r in storage.read_history(path)] == ["good", "also good"]

    def test_non_serialisable_values_do_not_raise(self, tmp_path):
        path = tmp_path / "history.jsonl"
        storage.append_history(path, [{"op": "x", "when": tmp_path}])
        assert json.loads(path.read_text(encoding="utf-8"))["when"] == str(tmp_path)
