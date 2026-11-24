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

from pathlib import Path
from typing import Any

from import_orchestrator.engine.errors import TriggerError

# Marker in javascript-validated-build.yaml rewrite-manifests step; replaced at load time.
SCRIPT_MARKER = "# LKO_MANIFEST_PUBLISH_VERSION_SCRIPT"
RESOLVE_TASK_NAME = "resolve-npm-source-artifact"
REWRITE_STEP_NAME = "rewrite-manifests"

# npm-builder UBI8: python3 may be 3.6; python3.9 is installed for node-gyp.
PYTHON_SHEBANG = "#!/usr/bin/python3.9"
FUTURE_IMPORT = "from __future__ import annotations"


def build_embedded_rewrite_script(module_src: str) -> str:
    """Build the Tekton rewrite-step script from ``manifest_publish_version.py`` source.

    ``from __future__`` must be the first statement after the shebang. The early-exit
    guard matches shell ``[ -z "${LIGHTWELL_PUBLISH_VERSION_OVERRIDE}" ]`` (no strip).
    """
    if "$(" in module_src:
        raise TriggerError(
            "manifest_publish_version.py contains '$(', which Tekton would substitute "
            "when the module is embedded in a Task script"
        )

    body_lines: list[str] = []
    saw_future = False
    for line in module_src.splitlines(keepends=True):
        if not saw_future and line.strip() == FUTURE_IMPORT:
            saw_future = True
            continue
        body_lines.append(line)
    if not saw_future:
        raise TriggerError(f"manifest_publish_version.py missing {FUTURE_IMPORT!r}")

    # Guard agrees with shell [ -z "$OVERRIDE" ] in use-or-passthrough / create.
    return (
        f"{PYTHON_SHEBANG}\n"
        f"{FUTURE_IMPORT}\n"
        "\n"
        "import os\n"
        'if os.environ.get("LIGHTWELL_PUBLISH_VERSION_OVERRIDE", "") == "":\n'
        "    raise SystemExit(0)\n"
        "\n"
        f"{''.join(body_lines)}"
    )


def embed_manifest_publish_version_script(pipeline_spec: dict[str, Any]) -> dict[str, Any]:
    """Embed ``manifest_publish_version.py`` into the inline rewrite step.

    The pipeline YAML keeps a placeholder script so the Tekton definition stays
    readable; Konflux runs the tested module source, not a duplicated copy.
    """
    module_path = Path(__file__).with_name("manifest_publish_version.py")
    module_src = module_path.read_text(encoding="utf-8")
    embedded = build_embedded_rewrite_script(module_src)

    for task in pipeline_spec.get("tasks", []):
        if task.get("name") != RESOLVE_TASK_NAME:
            continue
        for step in task.get("taskSpec", {}).get("steps", []):
            if step.get("name") != REWRITE_STEP_NAME:
                continue
            if SCRIPT_MARKER not in step.get("script", ""):
                raise TriggerError(f"{RESOLVE_TASK_NAME}/{REWRITE_STEP_NAME} missing {SCRIPT_MARKER} placeholder")
            step["script"] = embedded
            return pipeline_spec
        raise TriggerError(f"pipeline task {RESOLVE_TASK_NAME!r} missing step {REWRITE_STEP_NAME!r}")

    # Test stubs and pipelines without this task leave the spec unchanged.
    return pipeline_spec
