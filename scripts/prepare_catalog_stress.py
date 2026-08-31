"""Reorder the same catalog and remap facet postings, without touching controls.

No labels or target IDs are read. This intentionally changes row-order tie
breaking while preserving the product-to-facet weights exactly. Dense is off.
"""
import argparse
import json
from pathlib import Path
import random
import shutil

import numpy as np
from starter.retrieval.dense import sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--artifacts", default="artifacts/retrieval")
    parser.add_argument("--output", default="artifacts/evaluation/catalog-stress-2026")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    source = Path(args.artifacts)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if sha256_file(args.catalog) != manifest["catalog_sha256"]:
        raise ValueError("source catalog differs from source artifacts")
    lines = Path(args.catalog).read_bytes().splitlines(keepends=True)
    original_ids = json.loads((source / "product_ids.json").read_text(encoding="utf-8"))
    if [str(json.loads(line)["parent_asin"]) for line in lines] != original_ids:
        raise ValueError("source product mapping mismatch")
    order = list(range(len(lines)))
    random.Random(args.seed).shuffle(order)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "catalog.jsonl").write_bytes(b"".join(lines[index] for index in order))
    artifacts = output / "retrieval"
    artifacts.mkdir()
    (artifacts / "product_ids.json").write_text(json.dumps([original_ids[index] for index in order]), encoding="utf-8")
    shutil.copyfile(source / "facet_tokens.json", artifacts / "facet_tokens.json")
    old_to_new = np.empty(len(order), dtype=np.int32)
    old_to_new[order] = np.arange(len(order), dtype=np.int32)
    with np.load(source / "facet_index.npz", allow_pickle=False) as data:
        np.savez_compressed(artifacts / "facet_index.npz", offsets=data["offsets"],
                            row_indices=old_to_new[data["row_indices"]],
                            posting_weights=data["posting_weights"], idf=data["idf"])
    manifest.update(catalog_sha256=sha256_file(output / "catalog.jsonl"),
                    stress_test={"seed": args.seed, "source_catalog_sha256": sha256_file(args.catalog)})
    manifest["files"] = {name: {"sha256": sha256_file(artifacts / name), "size_bytes": (artifacts / name).stat().st_size}
                         for name in ("product_ids.json", "facet_tokens.json", "facet_index.npz")}
    (artifacts / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Prepared {len(order)} shuffled products in {output}; official catalog unchanged.")


if __name__ == "__main__":
    main()
