"""Optional LOCAL pretrained experiments, never automatic model downloads.

E5 is a different representation from catalog random indexing. Its exact
cosine search replaces no default route. No additional ANN/kNN dependency.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import os
from pathlib import Path

from starter.retrieval.dense import sha256_file


@dataclass(frozen=True)
class NeuralConfig:
    mode: str = "off"
    dense_dir: Path = Path("artifacts/neural/e5")
    reranker_dir: Path = Path("artifacts/neural/models/reranker")
    dense_weight: float = 0.20
    top_n: int = 100
    shortlist: int = 40
    threads: int = 2
    backend: str = "torch"

    def __post_init__(self):
        if self.mode not in {"off", "dense", "cross_encoder", "shadow"}:
            raise ValueError("unsupported local neural mode")
        if min(self.top_n, self.shortlist, self.threads) < 1 or self.dense_weight < 0:
            raise ValueError("invalid neural limits")
        if self.backend not in {"torch", "onnx"}:
            raise ValueError("unsupported neural backend")

    @classmethod
    def from_environment(cls):
        return cls(
            mode=os.getenv("TECHJAM_LOCAL_NEURAL", "off"),
            dense_dir=Path(os.getenv("TECHJAM_PRETRAINED_DENSE_DIR", "artifacts/neural/e5")),
            reranker_dir=Path(os.getenv("TECHJAM_CROSS_ENCODER_DIR", "artifacts/neural/models/reranker")),
            dense_weight=float(os.getenv("TECHJAM_PRETRAINED_DENSE_WEIGHT", ".20")),
            shortlist=int(os.getenv("TECHJAM_CROSS_ENCODER_TOP_N", "40")),
            threads=int(os.getenv("TECHJAM_NEURAL_THREADS", "2")),
            backend=os.getenv("TECHJAM_NEURAL_BACKEND", "torch"),
        )


def product_text(product: dict) -> str:
    """Product identity first; exclude broad category boilerplate. No labels."""
    from starter.ranking.features import _category_values, _text
    return " | ".join((
        _text(product.get("title")), " ".join(_category_values(product.get("categories"))),
        _text(product.get("store")), _text(product.get("features")),
        _text(product.get("details")), _text(product.get("description")),
    ))[:2400]


def _local_model(path: Path, cross_encoder=False, threads=2, backend="torch"):
    if not path.is_dir():
        raise FileNotFoundError(f"Prepare the local model first: {path}")
    if backend == "onnx":
        return OnnxModel(path, cross_encoder, threads)
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer
    torch.set_num_threads(threads)
    if cross_encoder:
        return CrossEncoder(str(path), device="cpu", local_files_only=True,
                            trust_remote_code=False, max_length=256)
    return SentenceTransformer(str(path), device="cpu", local_files_only=True,
                               trust_remote_code=False)


class OnnxModel:
    """Native ONNX + local tokenizer; no torch import or runtime HTTP access."""
    def __init__(self, path, cross_encoder, threads):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        self.max_seq_length = 256 if cross_encoder else 128
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path / "onnx/model_qint8_avx512_vnni.onnx"),
                                           options, providers=["CPUExecutionProvider"])

    def _batch(self, texts):
        import numpy as np
        self.tokenizer.enable_truncation(max_length=self.max_seq_length)
        encoded = self.tokenizer.encode_batch(texts)
        data = {
            "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
            "attention_mask": np.asarray([item.attention_mask for item in encoded], dtype=np.int64),
            "token_type_ids": np.asarray([item.type_ids for item in encoded], dtype=np.int64),
        }
        inputs = {item.name: data[item.name] for item in self.session.get_inputs()}
        return self.session.run(None, inputs)[0], data["attention_mask"]

    def encode(self, texts, batch_size=64, **kwargs):
        import numpy as np
        batches = []
        for start in range(0, len(texts), batch_size):
            hidden, mask = self._batch(texts[start:start+batch_size])
            expanded = mask[..., None].astype(np.float32)
            vectors = (hidden * expanded).sum(axis=1) / expanded.sum(axis=1).clip(min=1)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-12)
            batches.append(vectors)
            if kwargs.get("show_progress_bar") and start % (batch_size * 25) == 0:
                print(f"Encoded {min(start+batch_size, len(texts))}/{len(texts)} products", flush=True)
        return np.concatenate(batches)

    def predict(self, pairs, batch_size=16, **kwargs):
        import numpy as np
        return np.concatenate([self._batch(pairs[start:start+batch_size])[0].reshape(-1)
                               for start in range(0, len(pairs), batch_size)])


class PretrainedDenseRetriever:
    def __init__(self, catalog_path: Path, config: NeuralConfig):
        import numpy as np
        self.np = np
        root = config.dense_dir
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if sha256_file(catalog_path) != manifest["catalog_sha256"]:
            raise ValueError("pretrained dense catalog mismatch")
        for name, checksum in manifest["files"].items():
            if sha256_file(root / name) != checksum:
                raise ValueError(f"pretrained dense checksum mismatch: {name}")
        self.ids = json.loads((root / "ids.json").read_text(encoding="utf-8"))
        self.matrix = np.load(root / "embeddings.npy", mmap_mode="r", allow_pickle=False)
        if self.matrix.shape != (len(self.ids), manifest["dimension"]):
            raise ValueError("pretrained dense dimension mismatch")
        if not manifest.get("normalized") or len(set(self.ids)) != len(self.ids):
            raise ValueError("invalid pretrained dense metadata")
        for name, checksum in manifest["model_files"].items():
            if sha256_file(root / manifest["model_path"] / name) != checksum:
                raise ValueError(f"pretrained model changed since encoding: {name}")
        self.model = _local_model(root / manifest["model_path"], threads=config.threads,
                                  backend=manifest["backend"])
        self.model.max_seq_length = manifest["max_seq_length"]
        self.cache = OrderedDict()

    def search(self, query: str, top_n: int):
        if not query.strip() or top_n <= 0:
            return []
        key = (query, top_n)
        if key not in self.cache:
            vector = self.model.encode(["query: " + query], normalize_embeddings=True,
                                       convert_to_numpy=True, show_progress_bar=False)[0]
            scores = self.matrix @ vector
            # Resolve ties by identifier, not catalog position.
            ordered = self.np.lexsort((self.np.asarray(self.ids), -scores))[:top_n]
            self.cache[key] = tuple(self.ids[int(index)] for index in ordered)
            if len(self.cache) > 256:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return list(self.cache[key])


class LocalCrossEncoder:
    def __init__(self, store, config: NeuralConfig):
        self.store, self.config = store, config
        self.model = _local_model(config.reranker_dir, cross_encoder=True, threads=config.threads,
                                  backend=config.backend)
        self.cache = OrderedDict()

    def rank(self, query, candidates):
        import numpy as np
        head = candidates[:self.config.shortlist]
        pending, keys = [], []
        for candidate in head:
            key = (query, candidate.parent_asin)
            keys.append(key)
            if key not in self.cache:
                product = self.store._read(candidate.parent_asin)
                if product is None:
                    raise ValueError("candidate missing from local catalog")
                pending.append((key, (query, product_text(product))))
        if pending:
            scores = np.asarray(self.model.predict([pair for _, pair in pending],
                batch_size=16, show_progress_bar=False)).reshape(-1)
            if len(scores) != len(pending) or not np.isfinite(scores).all():
                raise ValueError("invalid cross-encoder scores")
            for (key, _), score in zip(pending, scores):
                self.cache[key] = float(score)
        ordered = sorted(zip(head, keys), key=lambda pair: (-self.cache[pair[1]], pair[0].fresh_rank, pair[0].parent_asin))
        for key in keys:
            self.cache.move_to_end(key)
        while len(self.cache) > 8192:
            self.cache.popitem(last=False)
        # Preserve deterministic feature scores for question calibration; neural
        # scores have different units. Shadow performs work but changes no order.
        if self.config.mode == "shadow":
            return candidates
        return [candidate for candidate, _ in ordered] + candidates[len(head):]
