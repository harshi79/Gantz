"""Unit tests for the Gantz core engine (no GUI required)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gantz_core as core  # noqa: E402


class TempWorkspace(unittest.TestCase):
    """Gives each test an isolated folder + undo history."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.inbox = self.root / "inbox"
        self.outbox = self.root / "outbox"
        self.inbox.mkdir()
        self.outbox.mkdir()
        self._old_data_dir = os.environ.get("GANTZ_DATA_DIR")
        os.environ["GANTZ_DATA_DIR"] = str(self.root / "appdata")

    def tearDown(self) -> None:
        if self._old_data_dir is None:
            os.environ.pop("GANTZ_DATA_DIR", None)
        else:
            os.environ["GANTZ_DATA_DIR"] = self._old_data_dir
        self._tmp.cleanup()

    def make_files(self, names_and_content: dict[str, bytes | str]) -> None:
        for name, content in names_and_content.items():
            path = self.inbox / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                path.write_text(content, encoding="utf-8")
            else:
                path.write_bytes(content)

    def files(self, **kwargs):
        return core.scan_files(self.inbox, **kwargs)


class HelperTests(unittest.TestCase):
    def test_natural_sort_orders_numbers_numerically(self):
        names = ["file10.txt", "file2.txt", "file1.txt", "Alpha.txt"]
        ordered = sorted(names, key=core.natural_key)
        self.assertEqual(ordered, ["Alpha.txt", "file1.txt", "file2.txt", "file10.txt"])

    def test_human_size(self):
        self.assertEqual(core.human_size(512), "512 B")
        self.assertEqual(core.human_size(1536), "1.5 KB")
        self.assertEqual(core.human_size(10 * 1024), "10 KB")

    def test_parse_extensions(self):
        self.assertEqual(core.parse_extensions(".txt, MD; *.csv"), {".txt", ".md", ".csv"})
        self.assertIsNone(core.parse_extensions(""))
        self.assertIsNone(core.parse_extensions("*"))

    def test_split_compound_suffix(self):
        self.assertEqual(core.split_compound_suffix("backup.tar.gz"), ("backup", ".tar.gz"))
        self.assertEqual(core.split_compound_suffix("photo.JPG"), ("photo", ".JPG"))
        self.assertEqual(core.split_compound_suffix("README"), ("README", ""))

    def test_sanitize_component(self):
        self.assertEqual(core.sanitize_component('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")
        self.assertEqual(core.sanitize_component("  name... "), "name")
        self.assertEqual(core.sanitize_component("CON"), "_CON")

    def test_fileinfo_helpers(self):
        path = Path("/tmp/example.TXT")
        info = core.FileInfo(path=path, size=10, mtime=0, ext=".txt")
        self.assertEqual(info.name, "example.TXT")
        self.assertTrue(info.is_text)


class ScanTests(TempWorkspace):
    def test_scan_is_recursive_and_filters_by_extension(self):
        self.make_files(
            {
                "a.txt": "a",
                "b.md": "b",
                "nested/deep/c.txt": "c",
                "skip.bin": b"\x00\x01",
            }
        )
        self.assertEqual(len(self.files()), 4)
        filtered = self.files(extensions={".txt"})
        self.assertEqual([f.name for f in filtered], ["a.txt", "c.txt"])

    def test_scan_non_recursive(self):
        self.make_files({"a.txt": "a", "nested/b.txt": "b"})
        self.assertEqual([f.name for f in self.files(recursive=False)], ["a.txt"])

    def test_hidden_files_excluded_by_default(self):
        self.make_files({"visible.txt": "a", ".hidden.txt": "b"})
        self.assertEqual([f.name for f in self.files()], ["visible.txt"])

    def test_scan_skips_vcs_folders(self):
        self.make_files({".git/config": "x", "real.txt": "y"})
        self.assertEqual([f.name for f in self.files(include_hidden=True)], ["real.txt"])

    def test_scan_missing_folder_returns_empty(self):
        self.assertEqual(core.scan_files(self.root / "nope"), [])


class RenameTests(TempWorkspace):
    def test_after_first_numbering_matches_original_behaviour(self):
        self.make_files({"b.txt": "2", "a.txt": "1", "c.txt": "3"})
        plan = core.build_rename_plan(self.files(), "yori", numbering="after_first")
        self.assertEqual([i.target_name for i in plan.items], ["yori.txt", "yori1.txt", "yori2.txt"])
        self.assertTrue(all(item.status == "ready" for item in plan.items))

    def test_numbering_modes(self):
        self.make_files({"a.txt": "1", "b.txt": "2"})
        everyone = core.build_rename_plan(self.files(), "x", numbering="all", start=1)
        self.assertEqual([i.target_name for i in everyone.items], ["x1.txt", "x2.txt"])
        nobody = core.build_rename_plan(self.files(), "x", numbering="none")
        self.assertEqual([i.target_name for i in nobody.items], ["x.txt", "x.txt"])
        self.assertTrue(all(item.status == "duplicate" for item in nobody.items))

    def test_padding_separator_and_compressed_suffix(self):
        self.make_files({"a.txt": "1", "b.txt": "2", "c.tar.gz": "3"})
        plan = core.build_rename_plan(
            self.files(), "Report", numbering="all", start=1, padding=3, separator="_"
        )
        self.assertEqual(
            [i.target_name for i in plan.items],
            ["Report_001.txt", "Report_002.txt", "Report_003.tar.gz"],
        )

    def test_prefix_is_sanitised(self):
        self.make_files({"a.txt": "1"})
        plan = core.build_rename_plan(self.files(), 'bad:name?', numbering="none")
        self.assertEqual(plan.items[0].target_name, "bad_name_.txt")

    def test_existing_target_is_flagged(self):
        self.make_files({"a.txt": "1", "keep.txt": "2"})
        plan = core.build_rename_plan(
            [f for f in self.files() if f.name == "a.txt"], "keep", numbering="none"
        )
        self.assertEqual(plan.items[0].status, "exists")

    def test_swap_rename_is_safe(self):
        # a.txt -> b.txt and b.txt -> a.txt at once.
        self.make_files({"a.txt": "AAA", "b.txt": "BBB"})
        plan = core.build_rename_plan(self.files(), "tmp", numbering="none")
        self.assertTrue(all(i.status == "duplicate" for i in plan.items))

        # A real swap: rename file "a" to "b" and vice versa via the in-place engine.
        pairs = [(self.inbox / "a.txt", self.inbox / "b.txt"),
                 (self.inbox / "b.txt", self.inbox / "a.txt")]
        result = core.OperationResult(label="swap")
        core._apply_in_place_renames(
            pairs, result, core._noop_progress, None, [],
            backup_dir=self.root / "backup", overwrite=False,
        )
        self.assertEqual(result.ok, 2)
        self.assertEqual((self.inbox / "a.txt").read_text(encoding="utf-8"), "BBB")
        self.assertEqual((self.inbox / "b.txt").read_text(encoding="utf-8"), "AAA")

    def test_apply_rename_in_place_and_undo(self):
        self.make_files({"b.txt": "2", "a.txt": "1"})
        plan = core.build_rename_plan(self.files(), "yori", numbering="after_first")
        result = core.apply_rename_plan(plan)
        self.assertEqual(result.ok, 2)
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()),
                         ["yori.txt", "yori1.txt"])
        self.assertTrue(core.can_undo())

        undo = core.undo_last()
        self.assertIsNotNone(undo)
        self.assertEqual(undo.ok, 2)
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()), ["a.txt", "b.txt"])
        self.assertFalse(core.can_undo())

    def test_copy_rename_to_output_folder_keeps_sources(self):
        self.make_files({"a.txt": "A", "b.txt": "B"})
        plan = core.build_rename_plan(self.files(), "copy", numbering="all", padding=2)
        result = core.apply_rename_plan(plan, mode="copy", destination=self.outbox)
        self.assertEqual(result.ok, 2)
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()), ["a.txt", "b.txt"])
        self.assertEqual(sorted(p.name for p in self.outbox.iterdir()), ["copy01.txt", "copy02.txt"])
        self.assertEqual((self.outbox / "copy01.txt").read_text(encoding="utf-8"), "A")

    def test_move_rename_removes_sources(self):
        self.make_files({"a.txt": "A"})
        plan = core.build_rename_plan(self.files(), "moved", numbering="all")
        result = core.apply_rename_plan(plan, mode="move", destination=self.outbox)
        self.assertEqual(result.ok, 1)
        self.assertEqual(list(self.inbox.iterdir()), [])
        self.assertTrue((self.outbox / "moved1.txt").exists())

        core.undo_last()
        self.assertTrue((self.inbox / "a.txt").exists())
        self.assertFalse((self.outbox / "moved1.txt").exists())

    def test_copy_skips_existing_without_overwrite(self):
        self.make_files({"a.txt": "A"})
        plan = core.build_rename_plan(self.files(), "dup", numbering="all")
        (self.outbox / "dup1.txt").write_text("old", encoding="utf-8")
        result = core.apply_rename_plan(plan, mode="copy", destination=self.outbox)
        self.assertEqual(result.ok, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual((self.outbox / "dup1.txt").read_text(encoding="utf-8"), "old")

    def test_copy_overwrite_is_undoable(self):
        self.make_files({"a.txt": "NEW"})
        plan = core.build_rename_plan(self.files(), "dup", numbering="all")
        (self.outbox / "dup1.txt").write_text("OLD", encoding="utf-8")
        result = core.apply_rename_plan(
            plan, mode="copy", destination=self.outbox, overwrite=True
        )
        self.assertEqual(result.ok, 1)
        self.assertEqual((self.outbox / "dup1.txt").read_text(encoding="utf-8"), "NEW")
        core.undo_last()
        self.assertEqual((self.outbox / "dup1.txt").read_text(encoding="utf-8"), "OLD")


class ReplaceTests(TempWorkspace):
    def test_search_finds_text_case_insensitively(self):
        self.make_files({"one.txt": "Visit Example.COM now", "two.md": "nothing here"})
        plan = core.build_replace_plan(self.files(), "example.com", "example.org")
        self.assertEqual(plan.matching_files, 1)
        self.assertEqual(plan.total_matches, 1)
        item = next(i for i in plan.items if i.matches)
        self.assertEqual(item.previews[0].after, "Visit example.org now")

    def test_case_sensitive_search(self):
        self.make_files({"one.txt": "Example example"})
        plan = core.build_replace_plan(self.files(), "Example", case_sensitive=True)
        self.assertEqual(plan.total_matches, 1)

    def test_regex_and_whole_word(self):
        self.make_files({"one.txt": "id 42, id 43, id 420"})
        plan = core.build_replace_plan(self.files(), r"\d+", "X", regex=True)
        self.assertEqual(plan.total_matches, 3)
        whole = core.build_replace_plan(self.files(), "id", "ID", whole_word=True)
        self.assertEqual(whole.total_matches, 3)

    def test_apply_replace_is_atomic_and_undoable(self):
        self.make_files({"one.txt": "foo bar foo", "binary.dat": b"\x00foo\x00"})
        plan = core.build_replace_plan(self.files(), "foo", "baz")
        self.assertEqual(plan.matching_files, 1)
        self.assertEqual(plan.total_matches, 2)
        binary = next(i for i in plan.items if i.file.name == "binary.dat")
        self.assertIn("Binary", binary.error)

        result = core.apply_replace_plan(plan)
        self.assertEqual(result.ok, 1)
        self.assertEqual((self.inbox / "one.txt").read_text(encoding="utf-8"), "baz bar baz")

        core.undo_last()
        self.assertEqual((self.inbox / "one.txt").read_text(encoding="utf-8"), "foo bar foo")

    def test_replace_preserves_bom_and_non_utf8_encoding(self):
        bom_file = self.inbox / "bom.txt"
        bom_file.write_bytes(b"\xef\xbb\xbffind me")
        latin = self.inbox / "latin.txt"
        latin.write_bytes("caf\u00e9 find".encode("cp1252"))

        plan = core.build_replace_plan(self.files(), "find", "FOUND")
        self.assertEqual(plan.total_matches, 2)
        result = core.apply_replace_plan(plan)
        self.assertEqual(result.ok, 2)
        self.assertTrue(bom_file.read_bytes().startswith(b"\xef\xbb\xbfFOUND"))
        self.assertEqual(latin.read_bytes(), "caf\u00e9 FOUND".encode("cp1252"))

    def test_no_matches_no_manifest(self):
        self.make_files({"one.txt": "abc"})
        plan = core.build_replace_plan(self.files(), "zzz")
        result = core.apply_replace_plan(plan)
        self.assertEqual(result.ok, 0)
        self.assertFalse(core.can_undo())

    def test_large_file_is_skipped(self):
        self.make_files({"big.txt": "x" * 100})
        plan = core.build_replace_plan(self.files(), "x", max_bytes=10)
        self.assertIn("Larger", plan.items[0].error)


class ZipTests(TempWorkspace):
    def test_create_zip_and_undo(self):
        self.make_files({"a.txt": "A", "nested/b.txt": "B"})
        archive = self.outbox / "bundle.zip"
        result = core.create_zip([self.inbox], archive)
        self.assertEqual(result.ok, 2)
        self.assertTrue(archive.exists())
        with zipfile.ZipFile(archive) as zf:
            names = sorted(zf.namelist())
        self.assertEqual(names, ["inbox/a.txt", "inbox/nested/b.txt"])

        core.undo_last()
        self.assertFalse(archive.exists())

    def test_create_zip_compression_levels(self):
        self.make_files({"a.txt": "hello world " * 500})
        for level in ("store", "fast", "balanced", "max"):
            archive = self.outbox / f"{level}.zip"
            core.create_zip([self.inbox / "a.txt"], archive, compression=level)
            self.assertTrue(zipfile.is_zipfile(archive))

    def test_read_zip_summary(self):
        self.make_files({"a.txt": "A"})
        archive = self.outbox / "s.zip"
        core.create_zip([self.inbox / "a.txt"], archive)
        summary = core.read_zip(archive)
        self.assertEqual(summary.file_count, 1)
        self.assertEqual(summary.total_size, 1)
        self.assertEqual(summary.error, "")

    def zip_with(self, members: dict[str, bytes], name: str = "test.zip") -> Path:
        archive = self.root / name
        with zipfile.ZipFile(archive, "w") as zf:
            for member, payload in members.items():
                zf.writestr(member, payload)
        return archive

    def test_extract_with_subfolder_and_junk_filter(self):
        archive = self.zip_with(
            {
                "docs/readme.txt": b"hi",
                "__MACOSX/docs/._readme.txt": b"junk",
                ".DS_Store": b"junk",
            }
        )
        result = core.extract_zips([archive], self.outbox, subfolder=True)
        self.assertEqual(result.ok, 1)
        self.assertEqual((self.outbox / "test" / "docs" / "readme.txt").read_bytes(), b"hi")
        self.assertFalse((self.outbox / "test" / "docs" / "._readme.txt").exists())

    def test_extract_blocks_zip_slip(self):
        archive = self.zip_with({"../evil.txt": b"pwn", "good.txt": b"ok"})
        result = core.extract_zips([archive], self.outbox)
        self.assertEqual(result.ok, 1)
        self.assertEqual(result.failed, 1)
        self.assertFalse((self.root / "evil.txt").exists())

    def test_extract_does_not_overwrite_by_default(self):
        archive = self.zip_with({"a.txt": b"new"})
        (self.outbox / "a.txt").write_bytes(b"old")
        result = core.extract_zips([archive], self.outbox)
        self.assertEqual(result.ok, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual((self.outbox / "a.txt").read_bytes(), b"old")

    def test_extract_overwrite_is_undoable(self):
        archive = self.zip_with({"a.txt": b"new"})
        (self.outbox / "a.txt").write_bytes(b"old")
        result = core.extract_zips([archive], self.outbox, overwrite=True)
        self.assertEqual(result.ok, 1)
        self.assertEqual((self.outbox / "a.txt").read_bytes(), b"new")
        core.undo_last()
        self.assertEqual((self.outbox / "a.txt").read_bytes(), b"old")

    def test_two_archives_to_subfolders_use_unique_names(self):
        first = self.zip_with({"a.txt": b"1"}, name="same.zip")
        (self.root / "other").mkdir()
        second = self.zip_with({"b.txt": b"2"}, name="same2.zip")
        # Same stem on purpose:
        target = self.root / "other" / "same.zip"
        second.replace(target)
        result = core.extract_zips([first, target], self.outbox, subfolder=True)
        self.assertEqual(result.ok, 2)
        self.assertTrue((self.outbox / "same" / "a.txt").exists())
        self.assertTrue((self.outbox / "same (2)" / "b.txt").exists())

    def test_unsupported_or_corrupt_archive_reports_error(self):
        bad = self.root / "broken.zip"
        bad.write_bytes(b"not a zip")
        result = core.extract_zips([bad], self.outbox)
        self.assertEqual(result.failed, 1)
        self.assertEqual(core.read_zip(bad).error != "", True)


class UndoTests(TempWorkspace):
    def test_undo_without_history_returns_none(self):
        self.assertIsNone(core.undo_last())

    def test_manifests_are_listed_newest_first(self):
        self.make_files({"a.txt": "1", "b.txt": "2"})
        plan = core.build_rename_plan(self.files(), "x", numbering="all")
        core.apply_rename_plan(plan)
        plan2 = core.build_rename_plan(self.files(), "y", numbering="all")
        core.apply_rename_plan(plan2)
        manifests = core.list_manifests()
        self.assertGreaterEqual(len(manifests), 2)
        self.assertGreaterEqual(manifests[0]["created"], manifests[1]["created"])
        self.assertEqual(manifests[0]["label"], "Rename to 'y'")


if __name__ == "__main__":
    unittest.main(verbosity=2)
