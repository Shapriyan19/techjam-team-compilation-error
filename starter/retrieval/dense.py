from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from starter.retrieval.text import TEXT_SCHEMA_VERSION, dense_terms, normalized_product_text


DENSE_ALGORITHM = "catalog_random_indexing_v1"
DENSE_EMBEDDINGS_FILE = "dense_embeddings.npy"
DENSE_ENCODER_FILE = "dense_encoder.npz"
PRODUCT_IDS_FILE = "product_ids.json"
VOCABULARY_FILE = "vocabulary.json"
MANIFEST_FILE = "manifest.json"


class DenseArtifactError(RuntimeError):
    pass


def build_dense_artifacts(
    catalog_path: str | Path,
    output_dir: str | Path,
    *,
    dimension: int = 96,
    vocabulary_size: int = 30_000,
    minimum_document_frequency: int = 2,
    maximum_document_fraction: float = 0.80,
    maximum_document_terms: int = 160,
    random_sparsity: int = 4,
    identity_weight: float = 0.35,
    seed: int = 2026,
) -> dict:
    np = _require_numpy()
    started = time.perf_counter()
    source = Path(catalog_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    if dimension < 8:
        raise ValueError("dimension must be at least 8")
    if random_sparsity < 1 or random_sparsity > dimension:
        raise ValueError("random_sparsity must be between 1 and dimension")

    document_frequency: Counter[str] = Counter()
    product_ids: list[str] = []
    seen_ids: set[str] = set()
    for product in _catalog_rows(source):
        identifier = str(product["parent_asin"])
        if identifier in seen_ids:
            raise DenseArtifactError(f"duplicate catalog parent_asin: {identifier}")
        seen_ids.add(identifier)
        product_ids.append(identifier)
        terms = dense_terms(
            normalized_product_text(product),
            include_bigrams=True,
            limit=maximum_document_terms,
        )
        document_frequency.update(set(terms))
    product_count = len(product_ids)
    if product_count == 0:
        raise DenseArtifactError("catalog is empty")

    maximum_df = max(1, int(product_count * maximum_document_fraction))
    vocabulary = [
        token
        for token, count in sorted(document_frequency.items(), key=lambda item: (-item[1], item[0]))
        if minimum_document_frequency <= count <= maximum_df
    ][:vocabulary_size]
    if not vocabulary:
        raise DenseArtifactError("dense vocabulary is empty")
    token_to_index = {token: index for index, token in enumerate(vocabulary)}
    idf = np.asarray(
        [math.log((1.0 + product_count) / (1.0 + document_frequency[token])) + 1.0 for token in vocabulary],
        dtype=np.float32,
    )
    positions, signs = _identity_coordinates(vocabulary, dimension, random_sparsity, seed, np)

    word_vectors = np.zeros((len(vocabulary), dimension), dtype=np.float32)
    for product in _catalog_rows(source):
        token_ids = _unique_token_ids(
            dense_terms(normalized_product_text(product), True, maximum_document_terms),
            token_to_index,
        )
        if not token_ids:
            continue
        ids = np.asarray(token_ids, dtype=np.int32)
        context = np.zeros(dimension, dtype=np.float32)
        np.add.at(context, positions[ids].reshape(-1), signs[ids].reshape(-1))
        scale = np.float32(1.0 / math.sqrt(len(token_ids)))
        word_vectors[ids] += context * scale
        for column in range(random_sparsity):
            np.add.at(
                word_vectors,
                (ids, positions[ids, column]),
                -signs[ids, column] * scale,
            )
    _normalize_rows(word_vectors, np)

    embeddings = np.zeros((product_count, dimension), dtype=np.float32)
    for row_index, product in enumerate(_catalog_rows(source)):
        terms = dense_terms(normalized_product_text(product), True, maximum_document_terms)
        embeddings[row_index] = _encode_terms(
            terms,
            token_to_index,
            word_vectors,
            idf,
            positions,
            signs,
            identity_weight,
            np,
        )

    embeddings_path = destination / DENSE_EMBEDDINGS_FILE
    encoder_path = destination / DENSE_ENCODER_FILE
    ids_path = destination / PRODUCT_IDS_FILE
    vocabulary_path = destination / VOCABULARY_FILE
    np.save(embeddings_path, embeddings, allow_pickle=False)
    np.savez_compressed(
        encoder_path,
        word_vectors=word_vectors,
        idf=idf,
        positions=positions,
        signs=signs,
        identity_weight=np.asarray([identity_weight], dtype=np.float32),
    )
    ids_path.write_text(json.dumps(product_ids, separators=(",", ":")), encoding="utf-8")
    vocabulary_path.write_text(json.dumps(vocabulary, separators=(",", ":")), encoding="utf-8")

    files = {}
    for path in (embeddings_path, encoder_path, ids_path, vocabulary_path):
        files[path.name] = {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
    manifest = {
        "artifact_version": 1,
        "algorithm": DENSE_ALGORITHM,
        "text_schema_version": TEXT_SCHEMA_VERSION,
        "catalog_sha256": sha256_file(source),
        "product_count": product_count,
        "embedding_dimension": dimension,
        "vocabulary_size": len(vocabulary),
        "minimum_document_frequency": minimum_document_frequency,
        "maximum_document_fraction": maximum_document_fraction,
        "maximum_document_terms": maximum_document_terms,
        "random_sparsity": random_sparsity,
        "identity_weight": identity_weight,
        "seed": seed,
        "dtype": "float32",
        "normalized": True,
        "numpy_version": np.__version__,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "dense_build_seconds": round(time.perf_counter() - started, 6),
        "files": files,
    }
    (destination / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


class DenseRetriever:
    def __init__(
        self,
        artifact_dir: str | Path,
        catalog_path: str | Path,
        *,
        validate_checksums: bool = True,
    ) -> None:
        np = _require_numpy()
        self._np = np
        self.artifact_dir = Path(artifact_dir)
        self.catalog_path = Path(catalog_path)
        self.manifest = json.loads((self.artifact_dir / MANIFEST_FILE).read_text(encoding="utf-8"))
        self._validate_manifest(validate_checksums)
        self.product_ids = json.loads((self.artifact_dir / PRODUCT_IDS_FILE).read_text(encoding="utf-8"))
        vocabulary = json.loads((self.artifact_dir / VOCABULARY_FILE).read_text(encoding="utf-8"))
        self.token_to_index = {token: index for index, token in enumerate(vocabulary)}
        encoder = np.load(self.artifact_dir / DENSE_ENCODER_FILE, allow_pickle=False)
        self.word_vectors = encoder["word_vectors"].astype(np.float32, copy=False)
        self.idf = encoder["idf"].astype(np.float32, copy=False)
        self.positions = encoder["positions"].astype(np.int32, copy=False)
        self.signs = encoder["signs"].astype(np.float32, copy=False)
        self.identity_weight = float(encoder["identity_weight"][0])
        self.embeddings = np.load(
            self.artifact_dir / DENSE_EMBEDDINGS_FILE,
            mmap_mode="r",
            allow_pickle=False,
        )
        expected_shape = (
            int(self.manifest["product_count"]),
            int(self.manifest["embedding_dimension"]),
        )
        if self.embeddings.shape != expected_shape:
            raise DenseArtifactError(f"embedding shape {self.embeddings.shape} != {expected_shape}")
        if len(self.product_ids) != expected_shape[0]:
            raise DenseArtifactError("product ID count does not match embedding rows")
        if self.word_vectors.shape != (len(vocabulary), expected_shape[1]):
            raise DenseArtifactError("encoder dimensions do not match manifest")
        self.query_count = 0
        self.query_seconds = 0.0

    def encode(self, query: str) -> Any:
        vector = _encode_terms(
            dense_terms(query, include_bigrams=True, limit=100),
            self.token_to_index,
            self.word_vectors,
            self.idf,
            self.positions,
            self.signs,
            self.identity_weight,
            self._np,
        )
        return vector if float(self._np.linalg.norm(vector)) > 0.0 else None

    def search(self, query: str, top_n: int) -> list[str]:
        if top_n <= 0:
            return []
        started = time.perf_counter()
        vector = self.encode(query)
        if vector is None:
            raise DenseArtifactError("query contains no known dense terms")
        scores = self._np.asarray(self.embeddings @ vector, dtype=self._np.float32)
        count = min(int(top_n), len(self.product_ids))
        if count == len(self.product_ids):
            candidate_indices = self._np.arange(count)
        else:
            candidate_indices = self._np.argpartition(scores, -count)[-count:]
        ordered = sorted(candidate_indices.tolist(), key=lambda index: (-float(scores[index]), index))
        self.query_count += 1
        self.query_seconds += time.perf_counter() - started
        return [str(self.product_ids[index]) for index in ordered]

    @property
    def average_query_latency_ms(self) -> float | None:
        if not self.query_count:
            return None
        return 1000.0 * self.query_seconds / self.query_count

    def _validate_manifest(self, validate_checksums: bool) -> None:
        required = {
            "artifact_version", "algorithm", "text_schema_version", "catalog_sha256",
            "product_count", "embedding_dimension", "files",
        }
        missing = required - set(self.manifest)
        if missing:
            raise DenseArtifactError(f"manifest missing fields: {sorted(missing)}")
        if self.manifest["algorithm"] != DENSE_ALGORITHM:
            raise DenseArtifactError("unsupported dense algorithm")
        if self.manifest["text_schema_version"] != TEXT_SCHEMA_VERSION:
            raise DenseArtifactError("unsupported product text schema")
        if sha256_file(self.catalog_path) != self.manifest["catalog_sha256"]:
            raise DenseArtifactError("catalog checksum does not match dense manifest")
        for filename in (DENSE_EMBEDDINGS_FILE, DENSE_ENCODER_FILE, PRODUCT_IDS_FILE, VOCABULARY_FILE):
            path = self.artifact_dir / filename
            if not path.is_file():
                raise DenseArtifactError(f"missing dense artifact: {filename}")
            expected = self.manifest["files"].get(filename)
            if expected is None:
                raise DenseArtifactError(f"manifest missing artifact entry: {filename}")
            if path.stat().st_size != int(expected["size_bytes"]):
                raise DenseArtifactError(f"artifact size mismatch: {filename}")
            if validate_checksums and sha256_file(path) != expected["sha256"]:
                raise DenseArtifactError(f"artifact checksum mismatch: {filename}")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _catalog_rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise DenseArtifactError("NumPy is required for dense retrieval") from exc
    return np


def _identity_coordinates(vocabulary, dimension, sparsity, seed, np):
    positions = np.empty((len(vocabulary), sparsity), dtype=np.int32)
    signs = np.empty((len(vocabulary), sparsity), dtype=np.float32)
    for row, token in enumerate(vocabulary):
        selected: set[int] = set()
        counter = 0
        while len(selected) < sparsity:
            digest = hashlib.blake2b(
                f"{seed}:{token}:{counter}".encode("utf-8"),
                digest_size=16,
            ).digest()
            position = int.from_bytes(digest[:8], "little") % dimension
            if position not in selected:
                column = len(selected)
                selected.add(position)
                positions[row, column] = position
                signs[row, column] = 1.0 if digest[8] & 1 else -1.0
            counter += 1
    return positions, signs


def _unique_token_ids(terms: list[str], token_to_index: dict[str, int]) -> list[int]:
    return list(dict.fromkeys(token_to_index[token] for token in terms if token in token_to_index))


def _encode_terms(
    terms,
    token_to_index,
    word_vectors,
    idf,
    positions,
    signs,
    identity_weight,
    np,
):
    counts = Counter(token_to_index[token] for token in terms if token in token_to_index)
    dimension = word_vectors.shape[1]
    if not counts:
        return np.zeros(dimension, dtype=np.float32)
    ids = np.asarray(list(counts), dtype=np.int32)
    weights = idf[ids] * np.sqrt(np.asarray([counts[int(index)] for index in ids], dtype=np.float32))
    vector = np.sum(word_vectors[ids] * weights[:, None], axis=0, dtype=np.float32)
    identity = np.zeros(dimension, dtype=np.float32)
    weighted_signs = signs[ids] * weights[:, None]
    np.add.at(identity, positions[ids].reshape(-1), weighted_signs.reshape(-1))
    vector += np.float32(identity_weight) * identity
    norm = float(np.linalg.norm(vector))
    if norm > 0.0:
        vector /= norm
    return vector.astype(np.float32, copy=False)


def _normalize_rows(matrix, np) -> None:
    norms = np.linalg.norm(matrix, axis=1)
    nonzero = norms > 0
    matrix[nonzero] /= norms[nonzero, None]
