from __future__ import annotations

import unittest

from agent_search.engines.base import SearchResult as AdapterSearchResult
from agent_search.results import SearchResult, result_to_dict


class ResultContractTests(unittest.TestCase):
    def test_engine_base_reexports_shared_result_type(self) -> None:
        self.assertIs(AdapterSearchResult, SearchResult)

    def test_dynamic_adapter_fields_survive_as_defensive_copy(self) -> None:
        result = SearchResult("Title", "https://example.test", "Snippet")
        result.rating = 9.2

        payload = result_to_dict(result)
        payload["title"] = "mutated"

        self.assertEqual(payload["rating"], 9.2)
        self.assertEqual(result.title, "Title")

    def test_mapping_input_is_copied(self) -> None:
        original = {"title": "Title", "url": "https://example.test"}
        payload = result_to_dict(original)
        payload["title"] = "mutated"

        self.assertEqual(original["title"], "Title")

    def test_invalid_to_dict_shape_fails_at_contract_boundary(self) -> None:
        class InvalidResult:
            def to_dict(self):
                return ["not", "a", "mapping"]

        with self.assertRaisesRegex(TypeError, "expected a mapping"):
            result_to_dict(InvalidResult())


if __name__ == "__main__":
    unittest.main()
