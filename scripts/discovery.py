"""Repository discovery for specialist roles with evidence.

Inspects a target repository and proposes specialist roles based on static file
signals, language heuristics, and user labels.
"""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import role_library

SKIP_DIRS = {".git", "vendor", "node_modules", "third_party"}
MAX_READ_BYTES = 256 * 1024

SAFETY_KEYWORDS = (
    "openai",
    "anthropic",
    "google.genai",
    "google_genai",
    "google-genai",
    "vertexai",
    "vertex_ai",
    "vertex-ai",
    "langchain",
)

FRONTEND_KEYWORDS = ("react", "vue", "svelte", "next")


def walk_files(root: Path, max_files: int = 50000) -> list[str]:
    """Walk repository files, skipping vendored/ignored dirs, up to max_files.

    Returns repository-relative POSIX paths.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    result: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted([d for d in dirnames if d not in SKIP_DIRS])
        filenames.sort()
        for fname in filenames:
            rel = (Path(dirpath) / fname).relative_to(root_path).as_posix()
            result.append(rel)
            if len(result) >= max_files:
                return result
    return result


def _read_prefix(path: Path, max_bytes: int = MAX_READ_BYTES) -> str:
    """Read at most max_bytes of a file, ignoring decode errors and catching OSError."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(max_bytes)
        return chunk.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def _add_evidence(ev_list: list[str], item: str) -> None:
    if item not in ev_list:
        ev_list.append(item)


def discover_roles(
    root: Path | str,
    subdir: str = "",
    deploy: str = "service",
    labels: list[str] | None = None,
    max_files: int = 50000,
) -> list[dict[str, Any]]:
    """Inspect repository and propose specialist roles with concrete evidence.

    Returns a list of dicts: {"role": str, "team": "build"|"eval", "evidence": list[str]},
    sorted by (team, role). Never includes an always-role, and when deploy="none",
    never includes a needs_service role.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return []

    normalized_labels = [lbl.strip().lower() for lbl in (labels or []) if lbl.strip()]

    target_dir = (root_path / subdir) if subdir else root_path
    if not target_dir.is_dir():
        return []

    raw_files = walk_files(target_dir, max_files=max_files)
    prefix = subdir.strip("/").replace("\\", "/") if subdir else ""
    rel_files = [f"{prefix}/{f}" if prefix else f for f in raw_files]

    # Map role -> evidence list
    evidence_by_role: dict[str, list[str]] = {r: [] for r in role_library.ROLES}

    # 1. Label-based signals
    if "security" in normalized_labels:
        _add_evidence(evidence_by_role["security-auditor"], "label: security")
    if "flaky" in normalized_labels:
        _add_evidence(evidence_by_role["test-engineer"], "label: flaky")
    if "performance" in normalized_labels:
        _add_evidence(evidence_by_role["performance-analyst"], "label: performance")

    # 2. Subdir-based signals
    if subdir.strip():
        _add_evidence(evidence_by_role["factory-analyst"], f"subdir: {subdir}")

    # 3. File and path scans
    source_counts: dict[str, int] = {"go": 0, "python": 0, "typescript": 0, "rust": 0, "java": 0}
    test_counts: dict[str, int] = {"go": 0, "python": 0, "typescript": 0, "rust": 0, "java": 0}

    go_source_files: list[str] = []
    has_go_mod = False
    go_mod_path = ""

    for rel_f, raw_f in zip(rel_files, raw_files):
        p = Path(rel_f)
        name = p.name
        name_lower = name.lower()
        parts = p.parts

        # security-auditor path signals
        if name_lower == "security.md":
            _add_evidence(evidence_by_role["security-auditor"], rel_f)
        elif any(any(kw in part.lower() for kw in ("auth", "crypto", "oauth", "secret")) for part in parts):
            _add_evidence(evidence_by_role["security-auditor"], rel_f)

        # release-manager signals
        if (
            fnmatch.fnmatch(name.upper(), "CHANGELOG*")
            or fnmatch.fnmatch(name.upper(), "RELEASE*")
            or fnmatch.fnmatch(name_lower, ".goreleaser*")
            or (".github/workflows/" in rel_f and "release" in name_lower)
        ):
            _add_evidence(evidence_by_role["release-manager"], rel_f)

        # migration-specialist signals
        if (
            any(part.lower() == "migrations" for part in parts[:-1])
            or rel_f.lower().endswith(".sql")
            or rel_f.lower().endswith(".tf")
            or name_lower == "alembic.ini"
        ):
            _add_evidence(evidence_by_role["migration-specialist"], rel_f)

        # contract-tester signals
        if (
            rel_f.lower().endswith(".proto")
            or fnmatch.fnmatch(name_lower, "openapi*.yaml")
            or fnmatch.fnmatch(name_lower, "openapi*.yml")
            or fnmatch.fnmatch(name_lower, "swagger*.json")
            or rel_f.lower().endswith(".graphql")
        ):
            _add_evidence(evidence_by_role["contract-tester"], rel_f)

        # frontend signals (*.tsx, *.vue)
        if rel_f.lower().endswith((".tsx", ".vue")):
            _add_evidence(evidence_by_role["frontend"], rel_f)

        # performance-analyst bench dir signal
        if any(part.lower() in ("bench", "benchmarks") for part in parts[:-1]):
            _add_evidence(evidence_by_role["performance-analyst"], rel_f)

        # Language file counting (for test-engineer ratio)
        if rel_f.endswith(".go"):
            if rel_f.endswith("_test.go"):
                test_counts["go"] += 1
            else:
                source_counts["go"] += 1
                go_source_files.append(raw_f)
        elif rel_f.endswith(".py"):
            is_py_test = (
                name.startswith("test_")
                or name.endswith("_test.py")
                or "/tests/" in f"/{rel_f}"
                or "/test/" in f"/{rel_f}"
            )
            if is_py_test:
                test_counts["python"] += 1
            else:
                source_counts["python"] += 1
        elif rel_f.endswith((".ts", ".tsx", ".js", ".jsx")):
            is_js_test = (
                ".test." in name
                or ".spec." in name
                or "/tests/" in f"/{rel_f}"
                or "/test/" in f"/{rel_f}"
                or "/__tests__/" in f"/{rel_f}"
            )
            if is_js_test:
                test_counts["typescript"] += 1
            else:
                source_counts["typescript"] += 1
        elif rel_f.endswith(".rs"):
            is_rs_test = "/tests/" in f"/{rel_f}"
            if is_rs_test:
                test_counts["rust"] += 1
            else:
                source_counts["rust"] += 1
        elif rel_f.endswith((".java", ".kt")):
            is_java_test = "test" in name_lower or "/test/" in f"/{rel_f}" or "/tests/" in f"/{rel_f}"
            if is_java_test:
                test_counts["java"] += 1
            else:
                source_counts["java"] += 1

        # Track manifest files
        if name == "go.mod":
            has_go_mod = True
            go_mod_path = rel_f

        # Text inspections for specific files
        full_path = target_dir / raw_f

        # Benchmark in Go tests
        if rel_f.endswith("_test.go"):
            go_test_text = _read_prefix(full_path)
            if "func Benchmark" in go_test_text:
                _add_evidence(evidence_by_role["performance-analyst"], rel_f)

        # package.json inspection (frontend & api-compat-checker)
        if name == "package.json":
            pkg_text = _read_prefix(full_path)
            pkg_text_lower = pkg_text.lower()
            if any(kw in pkg_text_lower for kw in FRONTEND_KEYWORDS):
                _add_evidence(evidence_by_role["frontend"], rel_f)
            if deploy == "none":
                try:
                    pkg_data = json.loads(pkg_text)
                    if "main" in pkg_data or "exports" in pkg_data:
                        _add_evidence(evidence_by_role["api-compat-checker"], rel_f)
                except Exception:
                    if '"main"' in pkg_text or '"exports"' in pkg_text:
                        _add_evidence(evidence_by_role["api-compat-checker"], rel_f)

        # pyproject.toml inspection (api-compat-checker)
        if name == "pyproject.toml" and deploy == "none":
            pyproj_text = _read_prefix(full_path)
            if re.search(r"^\s*\[project\]", pyproj_text, re.MULTILINE):
                _add_evidence(evidence_by_role["api-compat-checker"], rel_f)

        # Cargo.toml inspection (api-compat-checker)
        if name == "Cargo.toml" and deploy == "none":
            cargo_text = _read_prefix(full_path)
            if re.search(r"^\s*\[lib\]", cargo_text, re.MULTILINE):
                _add_evidence(evidence_by_role["api-compat-checker"], rel_f)

        # safety-evaluator inspection (dependency manifests & code imports)
        is_candidate_safety_file = (
            name in ("requirements.txt", "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Pipfile")
            or rel_f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt"))
        )
        if is_candidate_safety_file:
            content = _read_prefix(full_path).lower()
            if any(kw in content for kw in SAFETY_KEYWORDS):
                _add_evidence(evidence_by_role["safety-evaluator"], rel_f)

    # 4. Compute test ratio for detected language
    best_lang = max(source_counts, key=lambda k: source_counts[k])
    src_cnt = source_counts[best_lang]
    tst_cnt = test_counts[best_lang]
    if src_cnt > 0:
        ratio = tst_cnt / src_cnt
        if ratio < 0.2:
            _add_evidence(
                evidence_by_role["test-engineer"],
                f"low test ratio: {tst_cnt}/{src_cnt} ({ratio:.2f})",
            )

    # 5. Check Go library layout for api-compat-checker
    if has_go_mod and deploy == "none":
        has_pkg_main = False
        for raw_go_file in go_source_files:
            go_src = _read_prefix(target_dir / raw_go_file)
            if re.search(r"^\s*package\s+main\b", go_src, re.MULTILINE):
                has_pkg_main = True
                break
        if not has_pkg_main:
            _add_evidence(evidence_by_role["api-compat-checker"], go_mod_path)

    # 6. ui-auditor shares frontend signals
    if evidence_by_role["frontend"]:
        for ev in evidence_by_role["frontend"]:
            _add_evidence(evidence_by_role["ui-auditor"], ev)

    # 7. benchmark-runner shares performance-analyst signals when deploy="none"
    if deploy == "none" and evidence_by_role["performance-analyst"]:
        for ev in evidence_by_role["performance-analyst"]:
            _add_evidence(evidence_by_role["benchmark-runner"], ev)

    # 8. Filter and assemble result
    results: list[dict[str, Any]] = []
    for role_name, ev_list in evidence_by_role.items():
        if not ev_list:
            continue
        role_def = role_library.ROLES.get(role_name)
        if not role_def:
            continue
        if role_def["kind"] == "always":
            continue
        if deploy == "none" and role_def["needs_service"]:
            continue

        results.append({
            "role": role_name,
            "team": role_def["team"],
            "evidence": ev_list[:3],
        })

    results.sort(key=lambda item: (item["team"], item["role"]))
    return results
