"""Explicit model download / offline catalog encoding. Never runs in Agent."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import time

from starter.neural import _local_model, product_text
from starter.retrieval.dense import sha256_file


MODELS = {"dense": "intfloat/e5-small-v2", "reranker": "cross-encoder/ms-marco-MiniLM-L6-v2"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=MODELS)
    parser.add_argument("--download", action="store_true", help="Explicitly allow Hugging Face download")
    parser.add_argument("--encode", action="store_true", help="Build full-catalog E5 embeddings locally")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--root", default="artifacts/neural")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--backend", choices=["torch", "onnx"], default="torch")
    args = parser.parse_args()
    root = Path(args.root)
    model_path = root / "models" / args.kind
    if args.download:
        from huggingface_hub import HfApi, snapshot_download
        revision = HfApi().model_info(MODELS[args.kind]).sha
        patterns = ["*.json", "*.safetensors", "*.txt", "*.model", "README.md"]
        ignored = ["openvino/*"]
        if args.backend == "onnx":
            patterns.append("onnx/model_qint8_avx512_vnni.onnx")
        else:
            ignored.append("onnx/*")
        snapshot_download(MODELS[args.kind], revision=revision, local_dir=str(model_path),
                          allow_patterns=patterns, ignore_patterns=ignored)
        (model_path / "source.json").write_text(json.dumps({"model": MODELS[args.kind], "revision": revision}, indent=2), encoding="utf-8")
        print(f"Downloaded {MODELS[args.kind]} at {revision}", flush=True)
    if args.encode:
        if args.kind != "dense":
            parser.error("--encode applies only to dense")
        import numpy as np
        model = _local_model(model_path, threads=args.threads, backend=args.backend)
        model.max_seq_length = 128
        started = time.perf_counter()
        ids, texts = [], []
        with open(args.catalog, encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                ids.append(str(row["parent_asin"]))
                texts.append("passage: " + product_text(row))
        output = root / "e5"
        output.mkdir(parents=True, exist_ok=True)
        embeddings = model.encode(texts, normalize_embeddings=True, batch_size=64,
                                  convert_to_numpy=True, show_progress_bar=True)
        np.save(output / "embeddings.npy", embeddings.astype(np.float32), allow_pickle=False)
        (output / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
        manifest = {
            "catalog_sha256": sha256_file(args.catalog), "model_path": "../models/dense",
            "source": json.loads((model_path / "source.json").read_text(encoding="utf-8")),
            "dimension": int(embeddings.shape[1]), "max_seq_length": 128,
            "normalized": True, "build_seconds": time.perf_counter()-started,
            "backend": args.backend,
            "model_files": {str(path.relative_to(model_path)): sha256_file(path)
                            for path in model_path.rglob("*") if path.is_file() and ".cache" not in path.parts},
            "files": {name: sha256_file(output / name) for name in ("ids.json", "embeddings.npy")},
            "versions": {name: importlib.metadata.version(name) for name in ("torch", "numpy", "sentence-transformers", "transformers")},
        }
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
