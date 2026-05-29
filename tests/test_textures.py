from __future__ import annotations

import unittest

from harness.textures import TextureCandidate, _parse_listing_candidates, _passes_intent_gate, _text_score


class TextureListingParserTest(unittest.TestCase):
    def test_parse_listing_candidates_filters_navigation_links(self) -> None:
        html = """
        <a href="/texture/stone-pebbles,1.html">Stone Pebbles</a>
        <a href="/texture/">Browse</a>
        <a href="/texture/stone-pebbles,1.html">Stone Pebbles</a>
        <a href="/about">About</a>
        """

        candidates = _parse_listing_candidates(html, "https://freestocktextures.com/photos-stone/")

        self.assertEqual([item.title for item in candidates], ["Stone Pebbles"])

    def test_text_score_prefers_brushed_metal_over_rust_for_brushed_metal_query(self) -> None:
        query = "brushed metal surface"

        brushed = _text_score(query, "Scratched Brushed Steel Metal", ["metal", "steel"])
        rusty = _text_score(query, "metal russet rusty", ["metal", "rust"])
        paper = _text_score(query, "Shiny Silver Metallic Paper", ["paper"])

        self.assertGreater(brushed, rusty)
        self.assertGreater(brushed, paper)

    def test_text_score_requires_leather_for_leather_query(self) -> None:
        query = "brown leather surface"

        leather = _text_score(query, "Brown Quilted Leather", ["leather"])
        paper = _text_score(query, "Brown Paper with Black Dots", ["paper"])
        wood = _text_score(query, "Dark Wooden Floor", ["wood"])

        self.assertGreater(leather, paper)
        self.assertGreater(leather, wood)

    def test_text_score_demotes_plain_ceramic_mismatches(self) -> None:
        query = "plain white ceramic"

        ceramic = _text_score(query, "Plain White Ceramic Tile", ["ceramic"])
        wall = _text_score(query, "Imperfect Grunge White Wall", ["wall", "grunge"])
        paper = _text_score(query, "White Wrinkled Sheet of Paper", ["paper"])

        self.assertGreater(ceramic, wall)
        self.assertGreater(ceramic, paper)

    def test_intent_gate_filters_non_leather_for_leather_query(self) -> None:
        leather = TextureCandidate(title="Black Quilted Leather", page_url="https://example.test/texture/leather,1.html", tags=["leather"])
        leather.score = _text_score("brown leather surface", leather.title, leather.tags)
        paper = TextureCandidate(title="Brown Paper with Black Dots", page_url="https://example.test/texture/paper,1.html", tags=["leather"])
        paper.score = _text_score("brown leather surface", paper.title, paper.tags)

        self.assertTrue(_passes_intent_gate("brown leather surface", leather))
        self.assertFalse(_passes_intent_gate("brown leather surface", paper))

    def test_intent_gate_filters_plain_ceramic_mismatches(self) -> None:
        wall = TextureCandidate(title="Imperfect White Wall Closeup", page_url="https://example.test/texture/wall,1.html", tags=["wall"])
        wall.score = _text_score("plain white ceramic", wall.title, wall.tags)

        self.assertFalse(_passes_intent_gate("plain white ceramic", wall))

    def test_intent_gate_filters_title_negatives_for_brushed_metal_query(self) -> None:
        scratched = TextureCandidate(title="scratched metal galvanized", page_url="https://example.test/texture/metal,1.html", tags=["metal"])
        scratched.score = _text_score("brushed metal surface", scratched.title, scratched.tags)
        paper = TextureCandidate(title="Shiny Silver Metallic Paper", page_url="https://example.test/texture/paper,1.html", tags=["metallic"])
        paper.score = _text_score("brushed metal surface", paper.title, paper.tags)

        self.assertTrue(_passes_intent_gate("brushed metal surface", scratched))
        self.assertFalse(_passes_intent_gate("brushed metal surface", paper))


if __name__ == "__main__":
    unittest.main()
