import json
from pathlib import Path
import tempfile
import unittest

from starter.agent import Agent, _terms
from starter.improvements import ImprovementConfig
from starter.ranking.config import FeatureWeights
from starter.ranking.evidence import FreshCandidate
from starter.ranking.features import CatalogFeatureStore, DeterministicFeatureScorer, _product_features, _category_agreement
from starter.state import SessionState, SlotValue, ConstraintStrength
from starter.understanding import update_state_from_message, rewrite_query
from starter.retrieval.query_expansion import expand_query, expansion_is_useful
from starter.retrieval.hash_dense import HashDenseIndex, encode


ROOT = "Clothing, Shoes & Jewelry"
PRODUCTS = [
    {"parent_asin": "pants", "title": "Women's water resistant running pants", "categories": [ROOT, "Clothing", "Pants"], "price": 35.99},
    {"parent_asin": "shoes", "title": "Women's comfortable running shoes", "categories": [ROOT, "Shoes", "Running"], "price": 50},
    {"parent_asin": "sneakers", "title": "White canvas sneakers", "categories": [ROOT, "Shoes", "Sneakers"]},
]


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.catalog = Path(directory.name) / "catalog.jsonl"
        self.catalog.write_text("".join(json.dumps(row) + "\n" for row in PRODUCTS), encoding="utf-8")
        self.store = CatalogFeatureStore(self.catalog)
        self.addCleanup(self.store.close)

    def state(self, *messages, active=True):
        state = SessionState("test")
        for turn, message in enumerate(messages, 1):
            update_state_from_message(state, message, turn, active_evidence=active)
        return state

    def test_category_root_does_not_make_pants_shoes(self):
        slot = SlotValue("running shoes", ConstraintStrength.HARD, 1)
        self.assertEqual(_category_agreement(slot, self.store.get("pants")), (0.0, 1.0))
        self.assertEqual(_category_agreement(slot, self.store.get("shoes")), (1.0, 0.0))

    def test_category_unknown_is_not_a_contradiction(self):
        product = _product_features({"parent_asin": "x", "title": "Example", "categories": [ROOT]})
        self.assertEqual(_category_agreement(SlotValue("shoes", ConstraintStrength.HARD, 1), product), (0.0, 0.0))

    def test_generic_shoes_accept_sneakers(self):
        self.assertEqual(_category_agreement(SlotValue("shoes", ConstraintStrength.HARD, 1), self.store.get("sneakers")), (1.0, 0.0))

    def test_soft_category_does_not_get_hard_conflict(self):
        self.assertEqual(_category_agreement(SlotValue("shoes", ConstraintStrength.SOFT, 1), self.store.get("pants")), (0.0, 0.0))

    def test_later_negative_invalidates_positive_fragment_and_query(self):
        state = self.state("black leather shoes", "no leather")
        self.assertNotIn("leather", " ".join(state.verbatim_fragments).lower())
        self.assertNotIn("leather", rewrite_query(state).lower())
        self.assertEqual(state.fragment_provenance[0]["original"], "black leather shoes")
        self.assertEqual(state.fragment_provenance[0]["updated_turn"], 2)

    def test_override_preserves_unrelated_information(self):
        state = self.state("black heels under $100 for travel", "actually sneakers instead")
        self.assertNotIn("heels", " ".join(state.verbatim_fragments).lower())
        self.assertIn("black", rewrite_query(state))
        self.assertEqual(state.slots["budget_max"].value, 100)
        self.assertIn("travel", rewrite_query(state))

    def test_removed_value_does_not_revive_old_fragment(self):
        state = self.state("black leather shoes", "no leather", "leather if possible")
        self.assertNotIn("leather", state.fragment_provenance[0]["active"])
        self.assertIn("leather", state.slots["material"].value)

    def test_elaboration_is_not_an_override(self):
        state = self.state("earrings with hoop closure", "jewelry made of silver")
        self.assertIn("earrings", " ".join(state.verbatim_fragments))

    def test_word_boundaries_preserve_unrelated_words(self):
        state = self.state("red shoes with preferred support", "not red")
        self.assertIn("preferred", " ".join(state.verbatim_fragments))
        self.assertIn("preferred", rewrite_query(state))
        self.assertNotIn("red", _terms(" ".join(state.verbatim_fragments)))

    def test_recent_query_keeps_late_clue_within_lexical_budget(self):
        state = self.state("shoes " + " ".join("term" + str(i) for i in range(45)), "prefer ultragrip cushioning")
        legacy = list(dict.fromkeys(_terms(rewrite_query(state))))[:40]
        recent = list(dict.fromkeys(_terms(rewrite_query(state, recent_first=True))))[:40]
        self.assertNotIn("ultragrip", legacy)
        self.assertIn("ultragrip", recent)
        self.assertEqual(recent[0], "shoes")

    def test_prepared_features_are_exactly_equivalent_across_turns(self):
        state = SessionState("test")
        fresh = [FreshCandidate(row["parent_asin"], i, 1.0/(60+i), (("lexical", i),))
                 for i, row in enumerate(PRODUCTS, 1)]
        slow = DeterministicFeatureScorer(self.store, FeatureWeights(), ImprovementConfig(optimize=False))
        fast = DeterministicFeatureScorer(self.store, FeatureWeights(), ImprovementConfig(optimize=True))
        for turn, message in enumerate(["black running shoes", "under 80", "comfortable if possible", "actually white instead", "no leather"], 1):
            update_state_from_message(state, message, turn)
            self.assertEqual(slow.rank(fresh, state), fast.rank(fresh, state))

    def test_lexical_cache_keys_topn_copies_and_eviction(self):
        agent = Agent(self.catalog, improvements=ImprovementConfig(lexical_cache_size=1))
        self.addCleanup(agent.close)
        original = agent._lexical_search("shoes", 1)
        original[0]["parent_asin"] = "modified"
        self.assertNotEqual(agent._lexical_search("shoes", 1)[0]["parent_asin"], "modified")
        self.assertEqual(agent.lexical_cache_hits, 1)
        agent._lexical_search("shoes", 2)
        self.assertEqual(len(agent._lexical_cache), 1)
        agent._lexical_search("shoes", 1)
        self.assertEqual(agent.lexical_cache_misses, 3)

    def test_reset_clears_active_evidence_not_shared_catalog_cache(self):
        agent = Agent(self.catalog, improvements=ImprovementConfig(active_evidence=True))
        self.addCleanup(agent.close)
        agent.reset("test", {})
        response = agent.respond("test", "black leather shoes", 1, 10)
        self.assertTrue(response["recommendations"])
        agent.reset("test", {})
        self.assertEqual(agent.session_state("test").fragment_provenance, [])
        self.assertEqual(agent.session_state("test").verbatim_fragments, ())

    def test_query_expansion_preserves_original_terms(self):
        expanded = expand_query("women's water-resistant sneakers")
        self.assertIn("water-resistant", expanded)
        self.assertIn("sneakers", expanded)
        self.assertIn("waterproof", expanded)
        self.assertIn("trainers", expanded)

    def test_query_expansion_is_bounded_and_gated(self):
        self.assertTrue(expansion_is_useful("sneakers black"))
        self.assertFalse(expansion_is_useful("one two three four five six seven eight nine ten"))

    def test_hash_dense_is_normalized_and_searches(self):
        self.assertAlmostEqual(float((encode("running shoes") ** 2).sum()), 1.0, places=5)
        index = HashDenseIndex([("a", "black running shoes"), ("b", "formal necklace")])
        self.assertEqual(index.search("athletic shoes", 1)[0], "a")


if __name__ == "__main__":
    unittest.main()
