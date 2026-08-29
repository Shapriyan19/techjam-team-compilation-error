from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import platform
import sys
from pathlib import Path

from starter.agent import Agent


REQUIRED_RUNTIME_ARTIFACTS = {
    "manifest.json": "facet schema, catalog checksum, and file manifest",
    "product_ids.json": "facet row-to-parent_asin mapping",
    "facet_index.npz": "compressed facet postings",
    "facet_tokens.json": "facet token mapping",
}

OPTIONAL_EXPERIMENT_ARTIFACTS = {
    "dense_embeddings.npy": "rolled-back dense retrieval and semantic experiment",
    "dense_encoder.npz": "rolled-back dense retrieval and semantic experiment",
    "vocabulary.json": "rolled-back dense retrieval and semantic experiment",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_evaluator_output(path: Path, catalog_ids: set[str]) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for session in payload.get("sessions", []):
        if session.get("hit") and session.get("best_rank") not in range(1, 11):
            errors.append(f"invalid hit rank for {session.get('sample_id')}")
    required_metrics = {
        "hit_rate_at_10", "mrr", "mttc", "efficiency",
        "recommended_technical_score", "scenario_metrics", "sessions",
    }
    missing = required_metrics - set(payload)
    if missing:
        errors.append(f"missing evaluator fields: {sorted(missing)}")
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "session_count": len(payload.get("sessions", [])),
        "catalog_id_count": len(catalog_ids),
        "valid": not errors,
        "errors": errors,
    }


def validate_turn_outputs(path: Path, catalog_ids: set[str]) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    turns = payload.get("turn_outputs", [])
    for index, turn in enumerate(turns):
        identifiers = [str(value) for value in turn.get("recommendations", [])]
        if len(identifiers) > 10:
            errors.append(f"turn {index} exceeds Top 10")
        if len(identifiers) != len(set(identifiers)):
            errors.append(f"turn {index} contains duplicate IDs")
        invalid = [value for value in identifiers if not value or value not in catalog_ids]
        if invalid:
            errors.append(f"turn {index} contains invalid catalog IDs")
    return {
        "path": str(path),
        "turn_count": len(turns),
        "valid": not errors,
        "errors": errors[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit final TechJam submission requirements")
    parser.add_argument("--output", default="artifacts/evaluation/p7_submission_audit.json")
    parser.add_argument("--results", default="results.json")
    parser.add_argument(
        "--diagnostics",
        default="artifacts/evaluation/p7_e005_run3_diagnostics.json",
    )
    args = parser.parse_args()

    repository = Path(__file__).resolve().parents[1]
    artifact_dir = repository / "artifacts" / "retrieval"
    manifest_path = artifact_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    manifest_files = manifest.get("files", {})
    artifacts = []
    for filename, purpose in {**REQUIRED_RUNTIME_ARTIFACTS, **OPTIONAL_EXPERIMENT_ARTIFACTS}.items():
        path = artifact_dir / filename
        expected = manifest_files.get(filename, {})
        required = filename in REQUIRED_RUNTIME_ARTIFACTS
        actual_sha256 = sha256_file(path) if path.is_file() else None
        # The manifest is the root of trust for its children and cannot safely
        # carry its own checksum. Successful JSON loading above validates it.
        checksum_valid = bool(
            path.is_file()
            and (
                filename == "manifest.json"
                or (
                    expected.get("sha256")
                    and actual_sha256 == expected.get("sha256")
                )
            )
        )
        artifacts.append({
            "path": str(path.relative_to(repository)),
            "purpose": purpose,
            "required": required,
            "present": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else None,
            "expected_sha256": expected.get("sha256"),
            "actual_sha256": actual_sha256,
            "checksum_valid": checksum_valid,
            "missing_behavior": "lexical fallback" if required else "inactive experiment unavailable",
            "packaged_with_repository": True,
            "rebuild_command": "python -m scripts.build_retrieval_index",
        })

    catalog_path = repository / "data" / "catalog.jsonl"
    catalog_ids: set[str] = set()
    if catalog_path.is_file():
        with catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    catalog_ids.add(str(json.loads(line)["parent_asin"]))

    reset_signature = str(inspect.signature(Agent.reset))
    respond_signature = str(inspect.signature(Agent.respond))
    results_path = repository / args.results
    diagnostics_path = repository / args.diagnostics
    report = {
        "repository_root": str(repository),
        "working_directory": str(Path.cwd()),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable_recorded_for_audit_only": sys.executable,
            "required": ">=3.11",
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": {
            "numpy": importlib.metadata.version("numpy"),
        },
        "network_required": False,
        "runtime_writes_required": False,
        "generated_output": "results.json when running the local evaluator",
        "paths": "repository-relative defaults; no user-specific runtime path required",
        "agent_contract": {
            "reset": reset_signature,
            "respond": respond_signature,
            "valid": reset_signature == "(self, session_id: 'str', user_profile: 'dict') -> 'None'"
            and respond_signature == "(self, session_id: 'str', user_message: 'str', turn: 'int', top_k: 'int') -> 'dict'",
        },
        "catalog": {
            "path": "data/catalog.jsonl",
            "present": catalog_path.is_file(),
            "size_bytes": catalog_path.stat().st_size if catalog_path.is_file() else None,
            "sha256": sha256_file(catalog_path) if catalog_path.is_file() else None,
            "unique_ids": len(catalog_ids),
        },
        "artifacts": artifacts,
        "required_artifacts_valid": all(item["present"] and item["checksum_valid"] for item in artifacts if item["required"]),
        "evaluator_output": validate_evaluator_output(results_path, catalog_ids) if results_path.is_file() else None,
        "complete_turn_output_validation": (
            validate_turn_outputs(diagnostics_path, catalog_ids)
            if diagnostics_path.is_file()
            else None
        ),
    }
    output_path = repository / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
