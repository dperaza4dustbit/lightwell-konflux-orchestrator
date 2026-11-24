"""
Copyright (C) 2026 Lightwell

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from import_orchestrator.ecosystems.javascript import config
from import_orchestrator.ecosystems.javascript.manifest_publish_version import (
    main,
    rewrite_manifest,
    rewrite_package_dirs,
)
from import_orchestrator.ecosystems.javascript.pipeline_embed import (
    FUTURE_IMPORT,
    PYTHON_SHEBANG,
    RESOLVE_TASK_NAME,
    SCRIPT_MARKER,
    build_embedded_rewrite_script,
    embed_manifest_publish_version_script,
)
from import_orchestrator.engine.errors import TriggerError
from import_orchestrator.engine.pipeline_loader import load_pipeline


def _write_manifest(
    path: Path,
    *,
    version: str = "2.1.3",
    optional_dependencies_published: list[str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {
        "name": "ms",
        "version": version,
        "outputs": [
            {
                "id": "main",
                "type": "npm-package",
                "path": f"out/ms-{version}.tgz",
                "pulp_name": "ms",
            }
        ],
    }
    if optional_dependencies_published is not None:
        data["optional_dependencies_published"] = optional_dependencies_published
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


class TestRewriteManifest:
    def test_rewrites_version_and_output_path(self, tmp_path: Path) -> None:
        """Override replaces .version and -<old>.tgz output suffixes."""
        manifest = _write_manifest(tmp_path / "manifest.json")
        rewrite_manifest(manifest, "2.1.3-lwci.0")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["version"] == "2.1.3-lwci.0"
        assert data["outputs"][0]["path"] == "out/ms-2.1.3-lwci.0.tgz"

    def test_rewrites_optional_dependencies_published(self, tmp_path: Path) -> None:
        """Tier B/C @version pins in optional_dependencies_published track override."""
        manifest = _write_manifest(
            tmp_path / "manifest.json",
            version="0.28.2",
            optional_dependencies_published=["@lightwell/esbuild-linux-x64@0.28.2"],
        )
        # Also stamp a platform output path like esbuild.
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["outputs"].append(
            {
                "id": "linux-x64-binary",
                "type": "tl-platform-package",
                "path": "out/@lightwell/esbuild-linux-x64-0.28.2.tgz",
            }
        )
        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        rewrite_manifest(manifest, "0.28.2-lwci.0")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        assert data["optional_dependencies_published"] == ["@lightwell/esbuild-linux-x64@0.28.2-lwci.0"]
        assert data["outputs"][1]["path"] == "out/@lightwell/esbuild-linux-x64-0.28.2-lwci.0.tgz"

    def test_idempotent_when_already_override(self, tmp_path: Path) -> None:
        """Second rewrite with the same override is a no-op."""
        manifest = _write_manifest(tmp_path / "manifest.json", version="2.1.3-lwci.0")
        before = manifest.read_text(encoding="utf-8")
        rewrite_manifest(manifest, "2.1.3-lwci.0")
        assert manifest.read_text(encoding="utf-8") == before

    def test_missing_version_exits(self, tmp_path: Path) -> None:
        """Manifests without .version fail closed."""
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"name": "ms"}\n', encoding="utf-8")
        with pytest.raises(SystemExit, match="missing .version"):
            rewrite_manifest(manifest, "1.0.0-lwci.0")


class TestRewritePackageDirs:
    def test_rewrites_under_source_root(self, tmp_path: Path) -> None:
        """Package dirs are resolved under SOURCE_ROOT."""
        pkg = tmp_path / "packages" / "ms" / "2.1.3"
        _write_manifest(pkg / "manifest.json")
        rewrite_package_dirs(tmp_path, ["packages/ms/2.1.3"], "2.1.3-lwci.0")
        data = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        assert data["version"] == "2.1.3-lwci.0"

    def test_rejects_whitespace_only_override(self, tmp_path: Path) -> None:
        """Blank or space-containing overrides fail before rewriting manifests."""
        pkg = tmp_path / "packages" / "ms" / "2.1.3"
        _write_manifest(pkg / "manifest.json")
        with pytest.raises(SystemExit, match="no whitespace"):
            rewrite_package_dirs(tmp_path, ["packages/ms/2.1.3"], " ")
        with pytest.raises(SystemExit, match="no whitespace"):
            rewrite_package_dirs(tmp_path, ["packages/ms/2.1.3"], "2.1.3 lwci.0")

    def test_main_uses_source_root_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CLI reads SOURCE_ROOT from the environment."""
        pkg = tmp_path / "packages" / "ms" / "2.1.3"
        _write_manifest(pkg / "manifest.json")
        monkeypatch.setenv("SOURCE_ROOT", str(tmp_path))
        assert main(["2.1.3-lwci.0", "packages/ms/2.1.3"]) == 0
        data = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        assert data["outputs"][0]["path"] == "out/ms-2.1.3-lwci.0.tgz"


class TestPipelineEmbed:
    def test_embed_replaces_placeholder_with_module(self) -> None:
        """Loaded pipeline rewrite step runs the tested module source."""
        spec = embed_manifest_publish_version_script(load_pipeline(config.pipeline_definition_path()))
        resolve = next(t for t in spec["tasks"] if t["name"] == RESOLVE_TASK_NAME)
        step = next(s for s in resolve["taskSpec"]["steps"] if s["name"] == "rewrite-manifests")
        assert SCRIPT_MARKER not in step["script"]
        assert step["script"].startswith(PYTHON_SHEBANG)
        assert "def rewrite_manifest" in step["script"]
        assert "raise SystemExit(main())" in step["script"]
        assert "LIGHTWELL_PUBLISH_VERSION_OVERRIDE" in step["script"]

    def test_embedded_script_compiles(self) -> None:
        """Embedded script is valid Python (__future__ must lead after shebang)."""
        module_path = Path(config.__file__).with_name("manifest_publish_version.py")
        script = build_embedded_rewrite_script(module_path.read_text(encoding="utf-8"))
        lines = script.splitlines()
        assert lines[0] == PYTHON_SHEBANG
        assert lines[1] == FUTURE_IMPORT
        compile(script, "<embedded-rewrite>", "exec")

    def test_embedded_script_runs_guard_and_rewrite(self, tmp_path: Path) -> None:
        """Run the real Tekton payload: empty env no-ops; set env rewrites."""
        module_path = Path(config.__file__).with_name("manifest_publish_version.py")
        script = build_embedded_rewrite_script(module_path.read_text(encoding="utf-8"))
        script_file = tmp_path / "embedded_rewrite.py"
        script_file.write_text(script, encoding="utf-8")

        source_root = tmp_path / "source"
        pkg = source_root / "packages" / "ms" / "2.1.3"
        _write_manifest(pkg / "manifest.json")
        before = (pkg / "manifest.json").read_text(encoding="utf-8")

        base_env = {**os.environ, "SOURCE_ROOT": str(source_root)}
        empty = subprocess.run(
            [sys.executable, str(script_file), "2.1.3-lwci.0", "packages/ms/2.1.3"],
            env={**base_env, "LIGHTWELL_PUBLISH_VERSION_OVERRIDE": ""},
            check=False,
            capture_output=True,
            text=True,
        )
        assert empty.returncode == 0, empty.stderr
        assert (pkg / "manifest.json").read_text(encoding="utf-8") == before

        rewritten = subprocess.run(
            [sys.executable, str(script_file), "2.1.3-lwci.0", "packages/ms/2.1.3"],
            env={**base_env, "LIGHTWELL_PUBLISH_VERSION_OVERRIDE": "2.1.3-lwci.0"},
            check=False,
            capture_output=True,
            text=True,
        )
        assert rewritten.returncode == 0, rewritten.stderr
        data = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        assert data["version"] == "2.1.3-lwci.0"
        assert data["outputs"][0]["path"] == "out/ms-2.1.3-lwci.0.tgz"

    def test_empty_guard_matches_shell_z_test(self) -> None:
        """Python empty check matches shell [ -z ] (no .strip())."""
        module_path = Path(config.__file__).with_name("manifest_publish_version.py")
        script = build_embedded_rewrite_script(module_path.read_text(encoding="utf-8"))
        assert 'os.environ.get("LIGHTWELL_PUBLISH_VERSION_OVERRIDE", "") == ""' in script
        assert ".strip()" not in script.split("raise SystemExit(0)", 1)[0]

    def test_reject_tekton_dollar_paren_in_module(self) -> None:
        """Embed refuses module source that Tekton would substitute."""
        with pytest.raises(TriggerError, match=r"\$\("):
            build_embedded_rewrite_script("from __future__ import annotations\nx = '$('\n")

    def test_module_has_no_tekton_dollar_paren(self) -> None:
        """Embedded module must not contain Tekton $(...) substitution markers."""
        module_path = Path(config.__file__).with_name("manifest_publish_version.py")
        assert "$(" not in module_path.read_text(encoding="utf-8")

    def test_pipeline_has_no_hint_workspace_or_when(self) -> None:
        """Always-run resolve task; no emptyDir hint / when-gated apply."""
        spec = load_pipeline(config.pipeline_definition_path())
        assert "publish-version-resolve" not in [w.get("name") for w in spec.get("workspaces", [])]
        resolve = next(t for t in spec["tasks"] if t["name"] == RESOLVE_TASK_NAME)
        assert "when" not in resolve
        assert [s["name"] for s in resolve["taskSpec"]["steps"]] == [
            "use-or-passthrough",
            "rewrite-manifests",
            "create-trusted-artifact",
        ]
        assert not any(t["name"] == "resolve-source-artifact" for t in spec["tasks"])
