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

import argparse
import json
import os
import re
from pathlib import Path


def rewrite_manifest(manifest_path: Path, override: str) -> None:
    """Rewrite ``manifest.json`` publish identity fields to ``override``.

    Updates ``.version``, ``outputs[].path`` suffixes ``-<old>.tgz``, and
    ``optional_dependencies_published`` entries ending in ``@<old>``.
    Idempotent when ``.version`` already equals ``override``.
    """
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    old = data.get("version")
    if not old or not isinstance(old, str):
        raise SystemExit(f"[manifest-publish-version] ERROR: {manifest_path} missing .version")
    if old == override:
        print(f"[manifest-publish-version] {manifest_path}: already {override}")
        return

    data["version"] = override
    tgz_re = re.compile(re.escape(f"-{old}.tgz") + r"$")
    outputs = data.get("outputs")
    if isinstance(outputs, list):
        for item in outputs:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if isinstance(path, str):
                item["path"] = tgz_re.sub(f"-{override}.tgz", path)

    at_re = re.compile(re.escape(f"@{old}") + r"$")
    optional = data.get("optional_dependencies_published")
    if isinstance(optional, list):
        rewritten = []
        for entry in optional:
            if isinstance(entry, str):
                rewritten.append(at_re.sub(f"@{override}", entry))
            else:
                rewritten.append(entry)
        data["optional_dependencies_published"] = rewritten

    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[manifest-publish-version] {manifest_path}: {old} -> {override}")


def rewrite_package_dirs(source_root: Path, package_dirs: list[str], override: str) -> None:
    """Rewrite each ``<source_root>/<pkg>/manifest.json`` to ``override``."""
    if not override or any(ch.isspace() for ch in override):
        raise SystemExit("[manifest-publish-version] ERROR: override must be non-empty and contain no whitespace")
    if not package_dirs:
        raise SystemExit("[manifest-publish-version] ERROR: at least one package dir required")
    if not source_root.is_dir():
        raise SystemExit(f"[manifest-publish-version] ERROR: SOURCE_ROOT not a directory: {source_root}")

    for pkg in package_dirs:
        pkg = pkg.lstrip("/")
        manifest = source_root / pkg / "manifest.json"
        if not manifest.is_file():
            raise SystemExit(f"[manifest-publish-version] ERROR: missing manifest: {manifest}")
        rewrite_manifest(manifest, override)

    print("[manifest-publish-version] Done")


def main(argv: list[str] | None = None) -> int:
    """CLI: rewrite recipe manifests under SOURCE_ROOT for a publish-version override."""
    parser = argparse.ArgumentParser(
        description="Rewrite onboarding manifest.json version/outputs for SCRATCH overrides",
    )
    parser.add_argument("override", help="Effective publish version (e.g. 2.1.3-lwci.0)")
    parser.add_argument(
        "packages",
        nargs="+",
        help="Package dirs relative to SOURCE_ROOT (e.g. packages/ms/2.1.3)",
    )
    args = parser.parse_args(argv)
    source_root = Path(os.environ.get("SOURCE_ROOT", "./source"))
    rewrite_package_dirs(source_root, args.packages, args.override)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
