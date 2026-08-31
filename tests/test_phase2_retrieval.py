from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent
from starter.ranking.config import PhaseThreeConfig
from starter.retrieval.config import RetrievalConfig
from starter.retrieval.dense import (
    DENSE_EMBEDDINGS_FILE,
    DenseRetriever,
    build_dense_artifacts,
    sha256_file,
)
from starter.retrieval.facets import FacetRetriever, build_facet_artifacts
from starter.retrieval.rrf import weighted_rrf


try:
    import numpy as np
except ImportError:
    np = None


def _product(identifier: str, title: str, categories: list[str], **overrides) -> dict:
    product = {
        "parent_asin": identifier,
        "title": title,
        "features": [],
        "description": [],
        "price": None,
        "categories": categories,
        "details": {},
        "average_rating": 4.0,
        "rating_number": 1,
        "store": "Example",
    }
    product.update(overrides)
    return product


@unittest.skipUnless(np is not None, "NumPy dependency is not installed")
class DenseArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        root = Path(cls._temporary.name)
        cls.catalog_path = root / "catalog.jsonl"
        cls.artifact_dir = root / "artifacts"
        cls.products = [
            _product(
                "A",
                "Cushioned supportive walking sneakers",
                ["Clothing", "Women", "Shoes", "Walking"],
                features=["memory foam", "breathable comfort"],
                details={"Department": "womens", "Material": "mesh"},
            ),
            _product(
                "B",
                "Quick dry beach sandals",
                ["Clothing", "Women", "Shoes", "Sandals"],
                features=["summer vacation beach footwear"],
                details={"Department": "womens", "Material": "rubber"},
            ),
            _product(
                "C",
                "Silk evening party dress",
                ["Clothing", "Women", "Dresses"],
                details={"Department": "womens", "Material": "silk"},
            ),
            _product(
                "D",
                "Waterproof hiking boots",
                ["Clothing", "Men", "Shoes", "Hiking Boots"],
                features=["outdoor trail grip"],
                details={"Department": "mens", "Material": "leather"},
            ),
        ]
        cls.catalog_path.write_text(
            "".join(json.dumps(product) + "\n" for product in cls.products),
            encoding="utf-8",
        )
        build_dense_artifacts(
            cls.catalog_path,
            cls.artifact_dir,
            dimension=16,
            vocabulary_size=500,
            minimum_document_frequency=1,
            maximum_document_fraction=1.0,
            maximum_document_terms=100,
            random_sparsity=3,
        )
        build_facet_artifacts(cls.catalog_path, cls.artifact_dir, maximum_document_fraction=1.0)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_dense_index_artifact_integrity(self) -> None:
        retriever = DenseRetriever(self.artifact_dir, self.catalog_path)
        manifest = retriever.manifest
        embedding_path = self.artifact_dir / DENSE_EMBEDDINGS_FILE

        self.assertEqual(manifest["product_count"], len(self.products))
        self.assertEqual(manifest["embedding_dimension"], 16)
        self.assertEqual(
            sha256_file(embedding_path),
            manifest["files"][DENSE_EMBEDDINGS_FILE]["sha256"],
        )
        self.assertEqual(retriever.embeddings.dtype, np.float32)

    def test_catalog_rows_map_exactly_to_embedding_rows(self) -> None:
        retriever = DenseRetriever(self.artifact_dir, self.catalog_path)

        self.assertEqual(retriever.product_ids, ["A", "B", "C", "D"])
        self.assertEqual(retriever.embeddings.shape[0], len(retriever.product_ids))

    def test_dense_search_returns_valid_catalog_ids(self) -> None:
        retriever = DenseRetriever(self.artifact_dir, self.catalog_path)

        results = retriever.search("comfortable shoes for a long walking trip", 3)
        self.assertTrue(results)
        self.assertEqual(len(results), len(set(results)))
        self.assertTrue(set(results).issubset({"A", "B", "C", "D"}))
        self.assertEqual(results[0], "A")

    def test_facet_search_uses_catalog_backed_values(self) -> None:
        retriever = FacetRetriever(self.artifact_dir, self.catalog_path)

        results = retriever.search("women beach sandals", 3)
        self.assertTrue(results)
        self.assertEqual(results[0], "B")


class ReciprocalRankFusionTest(unittest.TestCase):
    def test_rrf_orders_known_ranked_lists(self) -> None:
        fused = weighted_rrf(
            {"lexical": ["A", "B", "C"], "dense": ["B", "C", "D"]},
            k=60,
        )
        self.assertEqual(fused, ["B", "C", "A", "D"])

    def test_weighted_rrf_respects_route_weight(self) -> None:
        fused = weighted_rrf(
            {"lexical": ["A", "B"], "dense": ["B", "A"]},
            {"lexical": 2.0, "dense": 1.0},
            k=0,
        )
        self.assertEqual(fused, ["A", "B"])

    def test_rrf_removes_duplicates_within_and_across_routes(self) -> None:
        fused = weighted_rrf(
            {"lexical": ["A", "A", "B"], "dense": ["A", "C", "C"]},
            limit=10,
        )
        self.assertEqual(len(fused), len(set(fused)))
        self.assertEqual(set(fused), {"A", "B", "C"})


class _RecordingDense:
    def __init__(self, results: list[str] | None = None, failure: Exception | None = None) -> None:
        self.results = results or []
        self.failure = failure
        self.queries: list[str] = []
        self.average_query_latency_ms = None

    def search(self, query: str, top_n: int) -> list[str]:
        self.queries.append(query)
        if self.failure:
            raise self.failure
        return self.results[:top_n]


class AgentHybridIntegrationTest(unittest.TestCase):
    def make_agent(self, directory: str, mode: str = "hybrid") -> Agent:
        catalog_path = Path(directory) / "catalog.jsonl"
        catalog_path.write_text(
            "".join([
                json.dumps(_product("A", "Black travel shoes", ["Clothing", "Shoes"])) + "\n",
                json.dumps(_product("B", "White waterproof sneakers", ["Clothing", "Shoes"])) + "\n",
            ]),
            encoding="utf-8",
        )
        return Agent(
            catalog_path,
            RetrievalConfig(
                mode=mode,
                artifact_dir=Path(directory) / "missing-artifacts",
                lexical_top_n=10,
                dense_top_n=10,
                facet_top_n=10,
                dense_weight=1.0,
                validate_artifact_checksums=False,
            ),
            PhaseThreeConfig(mode="off"),
        )

    def test_dense_failure_falls_back_to_lexical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            agent._dense_retriever = _RecordingDense(failure=RuntimeError("dense unavailable"))
            agent.reset("session", {})

            response = agent.respond("session", "black travel shoes", 1, 10)
            expected = agent._lexical_search(agent.session_state("session").rewritten_query, 10)

            self.assertEqual(response["recommendations"], expected)
            self.assertIn("fallback", agent.dense_status)

    def test_accumulated_query_feeds_lexical_and_dense_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            dense = _RecordingDense(["B", "A"])
            agent._dense_retriever = dense
            lexical_queries: list[str] = []
            original = agent._lexical_search

            def recording_lexical(query: str, top_k: int) -> list[dict]:
                lexical_queries.append(query)
                return original(query, top_k)

            agent._lexical_search = recording_lexical
            agent.reset("session", {})
            agent.respond("session", "shoes for travel", 1, 10)
            agent.respond("session", "water-resistant and comfortable", 2, 10)

            for query in (lexical_queries[-1], dense.queries[-1]):
                lowered = query.casefold()
                for expected in ("shoes", "travel", "water-resistant", "comfortable"):
                    self.assertIn(expected, lowered)
            # Query expansion may issue a second lexical query; the dense lane
            # intentionally receives the unexpanded accumulated state query.
            self.assertIn(dense.queries[-1], lexical_queries)

    def test_override_state_feeds_fresh_retrieval_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory)
            dense = _RecordingDense(["B", "A"])
            agent._dense_retriever = dense
            agent.reset("session", {})
            agent.respond("session", "black heels under $100", 1, 10)
            agent.respond("session", "actually sneakers instead", 2, 10)

            query = dense.queries[-1].casefold()
            self.assertIn("sneakers", query)
            self.assertNotIn("heels", query)
            self.assertIn("black", query)
            self.assertIn("100", query)

    def test_zero_dense_weight_skips_dense_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            catalog_path.write_text(
                "".join([
                    json.dumps(_product("A", "Black travel shoes", ["Clothing", "Shoes"])) + "\n",
                    json.dumps(_product("B", "White waterproof sneakers", ["Clothing", "Shoes"])) + "\n",
                ]),
                encoding="utf-8",
            )
            config = RetrievalConfig(
                mode="hybrid_facet",
                artifact_dir=Path(directory) / "missing-artifacts",
                lexical_top_n=10,
                dense_top_n=10,
                facet_top_n=10,
                dense_weight=0.0,
                validate_artifact_checksums=False,
            )
            agent = Agent(catalog_path, config, PhaseThreeConfig(mode="off"))
            dense = _RecordingDense(failure=AssertionError("dense route should not execute"))
            facet = _RecordingDense(["B", "A"])
            agent._dense_retriever = dense
            agent._facet_retriever = facet
            agent.reset("session", {})

            response = agent.respond("session", "waterproof shoes", 1, 10)

            self.assertEqual(dense.queries, [])
            self.assertTrue(facet.queries)
            self.assertEqual(len(response["recommendations"]), 2)

    def test_lexical_only_control_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            agent = self.make_agent(directory, mode="lexical")
            agent.reset("session", {})

            response = agent.respond("session", "black travel shoes", 1, 10)
            expected = agent._lexical_search(agent.session_state("session").rewritten_query, 10)

            self.assertEqual(response["recommendations"], expected)
            self.assertEqual(agent.dense_status, "disabled")


@unittest.skipUnless(
    np is not None
    and Path("artifacts/retrieval/manifest.json").is_file()
    and Path("data/catalog.jsonl").is_file(),
    "full frozen-catalog dense artifacts are not available",
)
class FrozenCatalogSemanticBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.retriever = DenseRetriever("artifacts/retrieval", "data/catalog.jsonl")
        cls.products = {}
        with Path("data/catalog.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    product = json.loads(line)
                    cls.products[str(product["parent_asin"])] = product

    def searchable_label(self, identifier: str) -> str:
        product = self.products[identifier]
        category_tail = " ".join((product.get("categories") or [])[-2:])
        return f"{product.get('title') or ''} {category_tail}".casefold()

    def test_walking_trip_query_returns_plausible_footwear(self) -> None:
        results = self.retriever.search("something comfortable for a long walking trip", 5)
        footwear_terms = ("shoe", "sneaker", "footwear", "boot", "sandal")
        plausible = sum(
            any(term in self.searchable_label(identifier) for term in footwear_terms)
            for identifier in results
        )
        self.assertGreaterEqual(plausible, 4)

    def test_beach_holiday_query_returns_plausible_summer_products(self) -> None:
        results = self.retriever.search("something for a beach holiday", 5)
        summer_terms = ("beach", "summer", "swim", "cover-up", "cover up", "vacation")
        plausible = sum(
            any(term in self.searchable_label(identifier) for term in summer_terms)
            for identifier in results
        )
        self.assertGreaterEqual(plausible, 3)


if __name__ == "__main__":
    unittest.main()
