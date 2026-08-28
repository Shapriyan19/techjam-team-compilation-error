from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from starter.retrieval.dense import build_dense_artifacts
from starter.retrieval.facets import build_facet_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline TechJam dense and facet artifacts")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--output-dir", default="artifacts/retrieval")
    parser.add_argument("--dimension", type=int, default=96)
    parser.add_argument("--vocabulary-size", type=int, default=30_000)
    parser.add_argument("--minimum-document-frequency", type=int, default=2)
    parser.add_argument("--maximum-document-terms", type=int, default=160)
    parser.add_argument("--skip-facets", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    manifest = build_dense_artifacts(
        args.catalog,
        args.output_dir,
        dimension=args.dimension,
        vocabulary_size=args.vocabulary_size,
        minimum_document_frequency=args.minimum_document_frequency,
        maximum_document_terms=args.maximum_document_terms,
    )
    facet_report = None
    if not args.skip_facets:
        facet_report = build_facet_artifacts(args.catalog, args.output_dir)
    manifest = json.loads((Path(args.output_dir) / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps({
        "output_dir": str(Path(args.output_dir).resolve()),
        "product_count": manifest["product_count"],
        "embedding_dimension": manifest["embedding_dimension"],
        "vocabulary_size": manifest["vocabulary_size"],
        "dense_build_seconds": manifest["dense_build_seconds"],
        "facet_report": facet_report,
        "total_build_seconds": round(time.perf_counter() - started, 6),
        "files": manifest["files"],
    }, indent=2))


if __name__ == "__main__":
    main()
