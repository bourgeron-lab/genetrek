"""Tests for resources-directory resolution."""

from __future__ import annotations

import os
import time

import pytest

from genetrek import paths
from genetrek.paths import ENV_VAR, ResourcesNotFoundError


class TestResolution:
    def test_explicit_argument_wins(self, resources, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_VAR, str(tmp_path / "elsewhere"))
        assert paths.resources_dir(resources.root) == resources.root.resolve()

    def test_env_var_is_used(self, resources, monkeypatch):
        monkeypatch.setenv(ENV_VAR, str(resources.root))
        assert paths.resources_dir() == resources.root.resolve()

    def test_discovery_finds_the_repo_tree(self, monkeypatch):
        """With no override, discovery locates the checked-in resources tree."""
        monkeypatch.delenv(ENV_VAR, raising=False)
        found = paths.resources_dir()
        assert (found / "config" / "fields_description.yaml").is_file()

    def test_missing_explicit_path_raises(self, tmp_path):
        with pytest.raises(ResourcesNotFoundError, match="does not exist"):
            paths.resources_dir(tmp_path / "nope")

    def test_missing_env_path_raises_naming_the_variable(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_VAR, str(tmp_path / "nope"))
        with pytest.raises(ResourcesNotFoundError, match=rf"\$?{ENV_VAR}"):
            paths.resources_dir()

    def test_file_instead_of_directory_raises(self, tmp_path):
        target = tmp_path / "afile"
        target.write_text("", encoding="utf-8")
        with pytest.raises(ResourcesNotFoundError, match="not a directory"):
            paths.resources_dir(target)

    def test_empty_env_var_falls_through_to_discovery(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "")
        assert (paths.resources_dir() / "config").is_dir()


class TestSubpaths:
    def test_all_subpaths_are_under_the_root(self, resources):
        for getter in (
            paths.fields_yaml_path,
            paths.categories_path,
            paths.badges_path,
            paths.external_links_path,
            paths.token_colors_path,
            paths.site_description_path,
            paths.content_dir,
            paths.parquet_path,
            paths.column_mapping_path,
            paths.aliases_path,
            paths.history_path,
            paths.backups_dir,
            paths.original_files_dir,
        ):
            assert getter(resources.root).is_relative_to(resources.root.resolve())

    def test_fixture_files_exist(self, resources):
        assert paths.fields_yaml_path(resources.root).is_file()
        assert paths.parquet_path(resources.root).is_file()
        assert paths.badges_path(resources.root).is_file()

    def test_latest_hgnc_file_is_none_when_absent(self, resources):
        assert paths.latest_hgnc_file(resources.root) is None

    def test_latest_hgnc_file_found(self, resources, hgnc_file):
        assert paths.latest_hgnc_file(resources.root) == hgnc_file

    def test_latest_hgnc_file_prefers_newest(self, resources, hgnc_file):
        older = resources.root / "original_file" / "hgnc_complete_set_20200101.txt"
        older.write_text("hgnc_id\n", encoding="utf-8")
        past = time.time() - 86_400
        os.utime(older, (past, past))
        assert paths.latest_hgnc_file(resources.root) == hgnc_file
