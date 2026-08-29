from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from pathlib import Path

from starter.retrieval.dense import MANIFEST_FILE, PRODUCT_IDS_FILE, DenseArtifactError, sha256_file
from starter.retrieval.text import facet_terms, query_facet_terms


FACET_INDEX_FILE = "facet_index.npz"
FACET_TOKENS_FILE = "facet_tokens.json"
FACET_SCHEMA_VERSION = "techjam_safe_facets_v1"
FIELD_WEIGHTS = {
    "category": 3.0,
    "store": 2.0,
    "department": 2.5,
    "detail": 1.5,
}


def build_facet_artifacts(
    catalog_path: str | Path,
    output_dir: str | Path,
    *,
    maximum_document_fraction: float = 0.50,
) -> dict:
    np = _require_numpy()
    started = time.perf_counter()
    source = Path(catalog_path)
    destination = Path(output_dir)
    manifest_path = destination / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_ids = json.loads((destination / PRODUCT_IDS_FILE).read_text(encoding="utf-8"))
    postings: dict[str, dict[int, float]] = defaultdict(dict)
    coverage = {field: 0 for field in FIELD_WEIGHTS}
    product_count = 0

    with source.open(encoding="utf-8") as handle:
        for row_index, line in enumerate(line for line in handle if line.strip()):
            product = json.loads(line)
            identifier = str(product["parent_asin"])
            if row_index >= len(expected_ids) or identifier != expected_ids[row_index]:
                raise DenseArtifactError("facet catalog order differs from dense product mapping")
            fields = facet_terms(product)
            for field, terms in fields.items():
                if terms:
                    coverage[field] += 1
                weight = FIELD_WEIGHTS[field]
                for token in terms:
                    postings[token][row_index] = max(postings[token].get(row_index, 0.0), weight)
            product_count += 1
    if product_count != len(expected_ids):
        raise DenseArtifactError("facet product count differs from dense product mapping")

    maximum_df = max(1, int(product_count * maximum_document_fraction))
    tokens = sorted(token for token, rows in postings.items() if len(rows) <= maximum_df)
    offsets = np.zeros(len(tokens) + 1, dtype=np.int64)
    row_parts = []
    weight_parts = []
    idf = np.zeros(len(tokens), dtype=np.float32)
    for token_index, token in enumerate(tokens):
        items = sorted(postings[token].items())
        rows = np.asarray([item[0] for item in items], dtype=np.int32)
        weights = np.asarray([item[1] for item in items], dtype=np.float32)
        row_parts.append(rows)
        weight_parts.append(weights)
        offsets[token_index + 1] = offsets[token_index] + len(rows)
        idf[token_index] = math.log((1.0 + product_count) / (1.0 + len(rows))) + 1.0
    row_indices = np.concatenate(row_parts) if row_parts else np.empty(0, dtype=np.int32)
    posting_weights = np.concatenate(weight_parts) if weight_parts else np.empty(0, dtype=np.float32)

    index_path = destination / FACET_INDEX_FILE
    tokens_path = destination / FACET_TOKENS_FILE
    np.savez_compressed(
        index_path,
        offsets=offsets,
        row_indices=row_indices,
        posting_weights=posting_weights,
        idf=idf,
    )
    tokens_path.write_text(json.dumps(tokens, separators=(",", ":")), encoding="utf-8")
    for path in (index_path, tokens_path):
        manifest["files"][path.name] = {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    facet_report = {
        "schema_version": FACET_SCHEMA_VERSION,
        "maximum_document_fraction": maximum_document_fraction,
        "field_weights": FIELD_WEIGHTS,
        "coverage": {
            field: {"count": count, "fraction": round(count / product_count, 6)}
            for field, count in coverage.items()
        },
        "term_count": len(tokens),
        "posting_count": int(len(row_indices)),
        "build_seconds": round(time.perf_counter() - started, 6),
    }
    manifest["facets"] = facet_report
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return facet_report


class FacetRetriever:
    def __init__(
        self,
        artifact_dir: str | Path,
        catalog_path: str | Path,
        *,
        validate_checksums: bool = True,
        deterministic_ties: bool = True,
    ) -> None:
        np = _require_numpy()
        self._np = np
        self.artifact_dir = Path(artifact_dir)
        manifest = json.loads((self.artifact_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        if manifest.get("facets", {}).get("schema_version") != FACET_SCHEMA_VERSION:
            raise DenseArtifactError("facet manifest is missing or incompatible")
        if sha256_file(catalog_path) != manifest["catalog_sha256"]:
            raise DenseArtifactError("catalog checksum does not match facet manifest")
        for filename in (FACET_INDEX_FILE, FACET_TOKENS_FILE, PRODUCT_IDS_FILE):
            path = self.artifact_dir / filename
            expected = manifest.get("files", {}).get(filename)
            if not path.is_file() or expected is None:
                raise DenseArtifactError(f"missing facet artifact: {filename}")
            if path.stat().st_size != int(expected["size_bytes"]):
                raise DenseArtifactError(f"facet artifact size mismatch: {filename}")
            if validate_checksums and sha256_file(path) != expected["sha256"]:
                raise DenseArtifactError(f"facet artifact checksum mismatch: {filename}")
        tokens = json.loads((self.artifact_dir / FACET_TOKENS_FILE).read_text(encoding="utf-8"))
        self.token_to_index = {token: index for index, token in enumerate(tokens)}
        self.product_ids = json.loads((self.artifact_dir / PRODUCT_IDS_FILE).read_text(encoding="utf-8"))
        arrays = np.load(self.artifact_dir / FACET_INDEX_FILE, allow_pickle=False)
        self.offsets = arrays["offsets"]
        self.row_indices = arrays["row_indices"]
        self.posting_weights = arrays["posting_weights"]
        self.idf = arrays["idf"]
        if len(self.offsets) != len(tokens) + 1:
            raise DenseArtifactError("facet offsets do not match token mapping")
        self.deterministic_ties = deterministic_ties
        self.query_count = 0
        self.query_seconds = 0.0

    def search(self, query: str, top_n: int) -> list[str]:
        if top_n <= 0:
            return []
        started = time.perf_counter()
        scores = self._np.zeros(len(self.product_ids), dtype=self._np.float32)
        matched = False
        for token in query_facet_terms(query):
            token_index = self.token_to_index.get(token)
            if token_index is None:
                continue
            matched = True
            start = int(self.offsets[token_index])
            end = int(self.offsets[token_index + 1])
            rows = self.row_indices[start:end]
            contribution = self.posting_weights[start:end] * self.idf[token_index]
            self._np.add.at(scores, rows, contribution)
        if not matched:
            return []
        nonzero = self._np.flatnonzero(scores > 0)
        count = min(int(top_n), len(nonzero))
        if count == 0:
            return []
        if self.deterministic_ties:
            # A stable descending sort keeps tied scores in ascending row order, so
            # the Top-N boundary no longer depends on how a particular NumPy build
            # partitions ties. This is the same order the comparison below produces.
            local = self._np.argsort(-scores[nonzero], kind="stable")[:count]
            ordered = nonzero[local].tolist()
        else:
            if count < len(nonzero):
                local = self._np.argpartition(scores[nonzero], -count)[-count:]
                candidates = nonzero[local]
            else:
                candidates = nonzero
            ordered = sorted(candidates.tolist(), key=lambda index: (-float(scores[index]), index))
        self.query_count += 1
        self.query_seconds += time.perf_counter() - started
        return [str(self.product_ids[index]) for index in ordered]

    @property
    def average_query_latency_ms(self) -> float | None:
        if not self.query_count:
            return None
        return 1000.0 * self.query_seconds / self.query_count


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise DenseArtifactError("NumPy is required for facet retrieval") from exc
    return np
