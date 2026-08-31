"""Tests proving the config_document.py / config_document_io.py split (WP-CFG).

``ConfigDocument`` (config_document.py) is now genuinely pure: no ``open()``,
no ``print()``, no filesystem access anywhere in its methods. The six
file-I/O operations plus ``print()`` moved to ``ConfigDocumentIOMixin`` in
config_document_io.py.

These tests use a test-only combined subclass, exactly the pattern
``AgentSpec/20260828_Isolation_DevPlanTicket.md``'s WP-CFG describes for the
later integration step (wiring the mixin into ``CgsDocument``/``GtsDocument``
is a separate, not-yet-done step — this file only proves the design works).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ComplexGitSync.config_document import ConfigDocument
from ComplexGitSync.config_document_io import ConfigDocumentIOMixin


class _TestDocument(ConfigDocument, ConfigDocumentIOMixin):
    """Combined pure base + I/O mixin, exactly as a real subclass would do it."""

    DOCUMENT_KIND = "test"


# ---------------------------------------------------------------------------
# Pure operations — zero filesystem access
# ---------------------------------------------------------------------------


class TestPureOperationsTouchNoFilesystem:
    """read/get/to_dict/from_dict must not create or touch any file."""

    def _cwd_entries(self, tmp_path: Path) -> set[str]:
        return set(os.listdir(tmp_path))

    def test_read_get_to_dict_create_no_files(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._cwd_entries(tmp_path)

        doc = _TestDocument({"section": {"key": "value", "num": 3}})
        assert doc.read("section.key") == "value"
        assert doc.get("section.num") == 3
        assert doc.get("missing", "fallback") == "fallback"
        assert doc.to_dict() == {"section": {"key": "value", "num": 3}}
        assert repr(doc) == "_TestDocument(kind='test')"

        after = self._cwd_entries(tmp_path)
        assert before == after, "pure operations must not create any file"

    def test_from_dict_creates_no_files(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._cwd_entries(tmp_path)

        doc = _TestDocument.from_dict({"a": 1})
        assert isinstance(doc, _TestDocument)
        assert doc.to_dict() == {"a": 1}

        after = self._cwd_entries(tmp_path)
        assert before == after

    def test_to_dict_returns_deep_copy(self):
        data = {"a": [1, 2, 3]}
        doc = _TestDocument(data)
        result = doc.to_dict()
        result["a"].append(4)
        assert doc._data["a"] == [1, 2, 3]

    def test_init_rejects_non_dict(self):
        with pytest.raises(TypeError, match="must be a dict"):
            ConfigDocument("not a dict")  # type: ignore[arg-type]

    def test_read_missing_intermediate_returns_default(self):
        doc = _TestDocument({"a": "scalar"})
        assert doc.read("a.b") is None

    def test_config_document_has_no_io_methods(self):
        """The trimmed base class must not carry any of the six I/O methods or print()."""
        for name in ("from_toml", "from_json", "from_yaml", "to_toml", "to_json", "to_yaml", "print"):
            assert name not in vars(ConfigDocument), (
                f"ConfigDocument.{name} should have moved to ConfigDocumentIOMixin"
            )


# ---------------------------------------------------------------------------
# I/O round-trips via the combined subclass
# ---------------------------------------------------------------------------


class TestIORoundTrips:
    def test_toml_round_trip(self, tmp_path: Path):
        doc = _TestDocument({"section": {"key": "val", "num": 7}})
        out = tmp_path / "doc.toml"
        doc.to_toml(out)
        assert out.exists()

        reloaded = _TestDocument.from_toml(out)
        assert reloaded.to_dict() == doc.to_dict()

    def test_json_round_trip(self, tmp_path: Path):
        doc = _TestDocument({"hello": "world", "num": 3, "nested": {"x": [1, 2]}})
        out = tmp_path / "doc.json"
        doc.to_json(out)
        assert out.exists()

        reloaded = _TestDocument.from_json(out)
        assert reloaded.to_dict() == doc.to_dict()

    def test_yaml_round_trip(self, tmp_path: Path):
        pytest.importorskip("yaml")
        doc = _TestDocument({"hello": "world", "list": [1, 2, 3]})
        out = tmp_path / "doc.yaml"
        doc.to_yaml(out)
        assert out.exists()

        reloaded = _TestDocument.from_yaml(out)
        assert reloaded.to_dict() == doc.to_dict()

    def test_toml_json_yaml_produce_equivalent_documents(self, tmp_path: Path):
        pytest.importorskip("yaml")
        data = {"a": 1, "b": {"c": "text"}}
        doc = _TestDocument(data)

        toml_out = tmp_path / "doc.toml"
        json_out = tmp_path / "doc.json"
        yaml_out = tmp_path / "doc.yaml"
        doc.to_toml(toml_out)
        doc.to_json(json_out)
        doc.to_yaml(yaml_out)

        from_toml = _TestDocument.from_toml(toml_out).to_dict()
        from_json = _TestDocument.from_json(json_out).to_dict()
        from_yaml = _TestDocument.from_yaml(yaml_out).to_dict()
        assert from_toml == from_json == from_yaml == data

    def test_from_yaml_missing_pyyaml_mentions_pixi(self, tmp_path: Path, monkeypatch):
        import builtins

        out = tmp_path / "doc.yaml"
        out.write_text("hello: world\n", encoding="utf-8")
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(ImportError, match="pixi"):
            _TestDocument.from_yaml(out)

    def test_to_yaml_missing_pyyaml_mentions_pixi(self, tmp_path: Path, monkeypatch):
        import builtins

        out = tmp_path / "doc.yaml"
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(ImportError, match="pixi"):
            _TestDocument({"hello": "world"}).to_yaml(out)

    def test_print_writes_toml_to_stdout(self, capsys):
        _TestDocument({"a": 1}).print()
        captured = capsys.readouterr()
        assert "a = 1" in captured.out
