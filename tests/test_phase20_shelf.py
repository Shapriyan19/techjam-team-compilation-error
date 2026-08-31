from __future__ import annotations

from starter.clarification import (
    ASK_PRIORITY_ORDER,
    NEVER_ASK_API_ATTRIBUTES,
    API_ATTRIBUTES,
    QUESTION_TEMPLATES,
)
from starter.ranking.phrases import (
    CatalogPhraseIndex,
    ConstraintPhraseScorer,
    searchable_text,
)
from starter.retrieval.shelf import ShelfIndex, coarse_category
from starter.runtime_config import AllocationConfig
from starter.state import SessionState
from starter.understanding import (
    EXHAUSTED_PREFERENCE_RE,
    extract_disclosed_constraints,
    update_state_from_message,
)


def _index(rows: dict[str, list[str]]) -> ShelfIndex:
    index = ShelfIndex()
    for parent_asin, categories in rows.items():
        index.add(parent_asin, categories)
    index.finalize()
    return index


class TestCoarseCategory:
    def test_keeps_last_two_fragments(self):
        assert coarse_category(
            ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Loafers & Slip-Ons"]
        ) == "Shoes Loafers & Slip-Ons"

    def test_drops_the_constant_top_level_labels(self):
        assert coarse_category(["Clothing, Shoes & Jewelry", "Men", "Hoodies"]) == "Men Hoodies"

    def test_splits_on_commas_inside_a_single_entry(self):
        assert coarse_category(["Tops, Tees & Blouses", "Blouses"]) == "Tees & Blouses Blouses"

    def test_falls_back_when_nothing_survives(self):
        assert coarse_category([]) == "clothing item"
        assert coarse_category(["Clothing"]) == "clothing item"


class TestShelfMatching:
    index = _index({
        "A1": ["Clothing, Shoes & Jewelry", "Women", "Shoes", "Loafers & Slip-Ons"],
        "A2": ["Clothing, Shoes & Jewelry", "Men", "Watches", "Wrist Watches"],
        "A3": ["Clothing, Shoes & Jewelry", "Men", "Watches"],
        "A4": ["Clothing, Shoes & Jewelry", "Men", "Outdoor & Work", "Work & Safety"],
    })

    def test_recovers_the_shelf_from_a_buying_opening(self):
        assert self.index.match(
            "I'm looking for Shoes Loafers & Slip-Ons. A key requirement is: 100% Leather."
        ) == "Shoes Loafers & Slip-Ons"

    def test_recovers_the_shelf_from_a_browsing_opening(self):
        assert self.index.match(
            "I'm looking for Men Watches, but I'm still exploring."
        ) == "Men Watches"

    def test_prefers_the_longer_label_when_one_contains_the_other(self):
        assert self.index.match(
            "I'm looking for Watches Wrist Watches, but I'm still exploring."
        ) == "Watches Wrist Watches"

    def test_a_constraint_cannot_hijack_the_stated_category(self):
        # "Work & Safety" is a shelf label and also appears in the requirement.
        # The category the shopper actually named must still win.
        assert self.index.match(
            "I'm looking for Men Watches. A key requirement is: rated for Outdoor & Work Work & Safety use."
        ) == "Men Watches"

    def test_returns_none_when_no_shelf_is_named(self):
        assert self.index.match("Those options are not quite right yet.") is None
        assert self.index.match("") is None

    def test_members_are_grouped_by_shelf(self):
        assert self.index.members("Men Watches") == ["A3"]
        assert self.index.members("Watches Wrist Watches") == ["A2"]
        assert self.index.members(None) == []
        assert self.index.members("No Such Shelf") == []


class TestDisclosedConstraints:
    def test_buying_opening_yields_the_requirement_not_the_category(self):
        assert extract_disclosed_constraints(
            "I'm looking for Women Jeans. A key requirement is: cotton.", "Women Jeans"
        ) == ("cotton",)

    def test_a_single_word_requirement_survives(self):
        # "cotton" is often the whole of the opening requirement; dropping short
        # fragments would discard the most specific thing the shopper said.
        assert extract_disclosed_constraints(
            "I'm looking for Women Jeans. A key requirement is: cotton.", "Women Jeans"
        )[0] == "cotton"

    def test_browsing_opening_discloses_nothing(self):
        assert extract_disclosed_constraints(
            "I'm looking for Men Hoodies, but I'm still exploring.", "Men Hoodies"
        ) == ()

    def test_answers_split_on_semicolons(self):
        assert extract_disclosed_constraints(
            "For that, what matters is: 100% Leather; Leather sole."
        ) == ("100% Leather", "Leather sole")

    def test_override_message_is_a_disclosure(self):
        assert extract_disclosed_constraints(
            "Actually, ignore my earlier preference. What I need is: Water Resistant."
        ) == ("Water Resistant",)

    def test_intent_override_opening_strips_the_category(self):
        assert extract_disclosed_constraints(
            "I'm looking for Men Watches. Stainless Steel Band", "Men Watches"
        ) == ("Stainless Steel Band",)

    def test_non_clue_replies_disclose_nothing(self):
        assert extract_disclosed_constraints("Those options are not quite right yet.") == ()
        assert extract_disclosed_constraints(
            "I don't have an additional preference for material."
        ) == ()


class TestExhaustionSignal:
    def test_additional_preference_means_drained(self):
        assert EXHAUSTED_PREFERENCE_RE.search("I don't have an additional preference for other.")

    def test_declining_one_question_is_not_exhaustion(self):
        # The boundary shopper deflects a single question but still has
        # requirements to give. Treating this as exhaustion would block the
        # attribute forever and throw those requirements away.
        assert not EXHAUSTED_PREFERENCE_RE.search(
            "I don't have a preference for other; please use your judgment."
        )

    def test_state_records_exhaustion_only_for_the_drained_form(self):
        state = SessionState(session_id="s")
        state.record_asked_attribute("other", "other")
        update_state_from_message(
            state, "I don't have a preference for other; please use your judgment.", 1
        )
        assert "other" not in state.no_preference_attributes
        update_state_from_message(state, "I don't have an additional preference for other.", 2)
        assert "other" in state.no_preference_attributes


class TestQuestionOrdering:
    def test_other_is_asked_first(self):
        assert ASK_PRIORITY_ORDER[0] == "other"

    def test_priority_order_avoids_unanswerable_attributes(self):
        for attribute in ASK_PRIORITY_ORDER:
            assert API_ATTRIBUTES[attribute] not in NEVER_ASK_API_ATTRIBUTES

    def test_every_priority_attribute_has_a_question(self):
        for attribute in ASK_PRIORITY_ORDER:
            assert QUESTION_TEMPLATES[attribute]


class TestEmitWidth:
    config = AllocationConfig()

    def test_says_nothing_specific_yet_so_commit_to_one(self):
        assert self.config.effective_top_k(turn=1, top_k=10, constraint_count=0) == 1
        assert self.config.effective_top_k(turn=1, top_k=10, constraint_count=1) == 1

    def test_widens_gradually_as_the_intent_sharpens(self):
        assert self.config.effective_top_k(turn=1, top_k=10, constraint_count=2) == 2
        assert self.config.effective_top_k(turn=1, top_k=10, constraint_count=3) == 2

    def test_opens_up_once_the_shopper_has_said_everything(self):
        # A session holds at most four requirement phrases, so a count past the
        # end of the table means there is nothing further to wait for.
        assert self.config.effective_top_k(turn=1, top_k=10, constraint_count=4) == 10
        assert self.config.effective_top_k(turn=2, top_k=10, constraint_count=6) == 10

    def test_opens_up_before_the_clock_runs_out(self):
        assert self.config.effective_top_k(turn=4, top_k=10, constraint_count=0) == 10
        assert self.config.effective_top_k(turn=9, top_k=10, constraint_count=0) == 10

    def test_never_exceeds_the_requested_width(self):
        assert self.config.effective_top_k(turn=5, top_k=3, constraint_count=9) == 3

    def test_turn_indexed_behaviour_is_still_reachable(self):
        # The previous scheme keyed the width to the turn alone; clearing the
        # width table and setting precision_turns restores it exactly.
        legacy = AllocationConfig(emit_widths=(), precision_turns=2, precision_top_k=1)
        assert legacy.effective_top_k(turn=2, top_k=10, constraint_count=4) == 1
        assert legacy.effective_top_k(turn=3, top_k=10, constraint_count=0) == 10


def _phrase_index(products: dict[str, dict]) -> CatalogPhraseIndex:
    index = CatalogPhraseIndex()
    for parent_asin, product in products.items():
        index.add(parent_asin, product)
    return index


class TestPhraseScoring:
    products = {
        # Two near-identical leather loafers; one adds a distinctive construction.
        "COMMON": {
            "title": "Leather Loafer",
            "features": ["100% Leather", "Imported", "Rubber sole"],
            "details": {"Closure type": "Slip-On"},
            "rating_number": "10",
        },
        "RARE": {
            "title": "Leather Loafer",
            "features": ["100% Leather", "Imported", "Opanka stitch-to-sole"],
            "details": {"Closure type": "Slip-On"},
            "rating_number": "10",
        },
        # Same evidence as COMMON, but far more reviews.
        "POPULAR": {
            "title": "Leather Loafer",
            "features": ["100% Leather", "Imported", "Rubber sole"],
            "details": {"Closure type": "Slip-On"},
            "rating_number": "90000",
        },
        "UNRELATED": {
            "title": "Cotton T-Shirt",
            "features": ["100% Cotton"],
            "details": {},
            "rating_number": "10",
        },
    }
    scorer = ConstraintPhraseScorer(_phrase_index(products))

    def test_a_rare_phrase_outweighs_a_common_one(self):
        ranked = self.scorer.score(
            ["COMMON", "RARE", "POPULAR", "UNRELATED"],
            ("100% Leather", "Opanka stitch-to-sole"),
        )
        assert ranked[0][0] == "RARE"

    def test_a_product_matching_nothing_ranks_last(self):
        ranked = self.scorer.score(
            ["UNRELATED", "COMMON", "RARE"], ("100% Leather", "Rubber sole")
        )
        assert ranked[-1][0] == "UNRELATED"

    def test_punctuation_only_differences_still_match(self):
        # The shopper's requirement arrives as "Closure type: Slip-On" while the
        # product text renders the same detail as "Closure type Slip-On". A
        # strict comparison scores zero; these must still be recognised.
        ranked = self.scorer.score(["UNRELATED", "COMMON"], ("Closure type: Slip-On",))
        assert ranked[0][0] == "COMMON"
        assert ranked[0][1] > ranked[1][1]

    def test_popularity_breaks_ties_but_does_not_override_evidence(self):
        tied = self.scorer.score(["COMMON", "POPULAR"], ("100% Leather",))
        assert tied[0][0] == "POPULAR"
        # ... yet a product with better evidence still beats a more popular one.
        contested = self.scorer.score(
            ["POPULAR", "RARE"], ("100% Leather", "Opanka stitch-to-sole")
        )
        assert contested[0][0] == "RARE"

    def test_no_constraints_leaves_the_incoming_order_alone(self):
        ranked = self.scorer.score(["RARE", "COMMON"], ())
        assert [identifier for identifier, _ in ranked] == ["RARE", "COMMON"]

    def test_empty_candidate_set(self):
        assert self.scorer.score([], ("100% Leather",)) == []

    def test_searchable_text_flattens_every_field_shape(self):
        text = searchable_text(self.products["COMMON"])
        assert "100% leather" in text
        assert "closure type slip-on" in text
