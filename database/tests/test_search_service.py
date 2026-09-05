from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.connection import connect
from database.services.integrity import run_health_gate
from database.services.search import build_search_projection, search_documents
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


class SearchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.path = Path(self.temp.name) / "search.db"
        populate(self.path, PROFILES["smoke"])
        self.connection = connect(self.path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_full_text_and_facets_resolve_authoritative_entities(self) -> None:
        generation = build_search_projection(
            self.connection, evidence_cutoff_utc="2026-12-31T23:59:59Z"
        )
        parts = search_documents(
            self.connection, generation_id=generation,
            query="Synthetic Component 000005", entity_type="Part",
        )
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].title, "PART-000005")
        suppliers = search_documents(
            self.connection, generation_id=generation,
            query="Synthetic Supplier 002", entity_type="Supplier",
        )
        self.assertEqual(len(suppliers), 1)
        self.assertEqual(suppliers[0].title, "Synthetic Supplier 002")
        event_id = self.connection.execute(
            "SELECT event_id FROM sourcing_event LIMIT 1"
        ).fetchone()[0]
        event_results = search_documents(
            self.connection, generation_id=generation, event_id=event_id, limit=10,
        )
        self.assertEqual(len(event_results), 10)
        event_document = next(item for item in event_results
                              if item.entity_type == "Sourcing Event")
        self.assertEqual(event_document.title,
                         "SP-SYNTHETIC-001 - Synthetic Sourcing Event")
        next_page = search_documents(
            self.connection, generation_id=generation, event_id=event_id,
            after_document_id=event_results[-1].search_document_id, limit=10,
        )
        self.assertTrue(next_page)
        self.assertGreater(next_page[0].search_document_id,
                           event_results[-1].search_document_id)

    def test_generation_is_immutable_and_health_verifies_fts_cache(self) -> None:
        generation = build_search_projection(
            self.connection, evidence_cutoff_utc="2026-12-31T23:59:59Z"
        )
        self.assertNotIn(
            "SEARCH_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(
                self.connection, as_of_utc="2026-12-31T23:59:59Z")},
        )
        document_id = self.connection.execute(
            """SELECT search_document_id FROM search_document_fts
               WHERE search_generation_id = ? LIMIT 1""", (generation,),
        ).fetchone()[0]
        self.connection.execute(
            """UPDATE search_document_fts SET searchable_text = 'tampered'
               WHERE search_generation_id = ? AND search_document_id = ?""",
            (generation, document_id),
        )
        self.assertIn(
            "SEARCH_GENERATION_MISMATCH",
            {finding.code for finding in run_health_gate(
                self.connection, as_of_utc="2026-12-31T23:59:59Z")},
        )
        with self.assertRaisesRegex(Exception, "search_document_projection is immutable"):
            self.connection.execute(
                """UPDATE search_document_projection SET title = 'changed'
                   WHERE search_generation_id = ? AND search_document_id = ?""",
                (generation, document_id),
            )

    def test_query_limits_are_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 500"):
            search_documents(self.connection, generation_id="missing", limit=501)


if __name__ == "__main__":
    unittest.main()
