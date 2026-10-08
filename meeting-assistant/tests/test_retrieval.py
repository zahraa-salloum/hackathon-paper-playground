from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from meeting_assistant.retrieval import RetrievalEngine
from meeting_assistant.storage import Store


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.first = self.store.create_meeting("Planning")["id"]
        self.second = self.store.create_meeting("Readiness")["id"]
        self.store.add_segments(self.first, [
            {"start": 0, "end": 20, "text": "Atlas pilot launch was initially October 15.", "speaker": "Speaker A"},
            {"start": 21, "end": 40, "text": "Omar owns CSV importer validation.", "speaker": "Speaker B"},
            {"start": 41, "end": 60, "text": "We chose SQLite because the archive must work offline."},
        ])
        self.store.add_segments(self.second, [
            {"start": 0, "end": 20, "text": "Atlas pilot launch moved to October 22 because CSV importer validation needed review."},
            {"start": 21, "end": 40, "text": "Maya will write the pilot guide."},
        ])
        self.engine = RetrievalEngine(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_scope_and_provenance_for_every_citation(self):
        result = self.engine.answer("Why did the Atlas pilot launch move?", [self.second])
        self.assertFalse(result["insufficient_evidence"])
        self.assertEqual(result["mode"], "extractive")
        stored = {row["id"]: row for row in self.store.get_meeting(self.second)["segments"]}
        for citation in result["citations"]:
            self.assertEqual(citation["meeting_id"], self.second)
            self.assertEqual(citation["text"], stored[citation["segment_id"]]["text"])
            self.assertIn("[" + citation["id"] + "]", result["answer"])
        self.assertTrue(any("October 22" in c["text"] for c in result["citations"]))

    def test_no_evidence_and_empty_scope_abstain_without_generation(self):
        self.store.save_settings({"chat_provider": "openai"})
        with patch("meeting_assistant.providers.generate_answer") as generate:
            result = self.engine.answer("What was the quasar telescope aperture?")
            self.assertTrue(result["insufficient_evidence"])
            self.assertEqual(result["citations"], [])
            self.assertTrue(self.engine.answer("Atlas launch", [])["insufficient_evidence"])
            generate.assert_not_called()

    def test_followup_history_and_bounded_expansion(self):
        result = self.engine.answer("Who owns it?", history=[{"role": "user", "content": "Tell me about CSV importer validation"}])
        self.assertTrue(any("Omar" in c["text"] for c in result["citations"]))
        self.assertIn("history", [step["stage"] for step in result["steps"]])
        self.assertIn("neighbors", [step["stage"] for step in result["steps"]])
        self.assertIn("related", [step["stage"] for step in result["steps"]])
        self.assertLessEqual(len(result["citations"]), 6)
        for strategy in ("lexical", "basic"):
            simpler = self.engine.answer("Atlas launch", strategy=strategy)
            self.assertNotIn("neighbors", [step["stage"] for step in simpler["steps"]])

    def test_optional_embeddings_can_recall_paraphrase(self):
        self.store.save_settings({"embedding_provider": "sentence_transformers"})
        def embed(texts, settings):
            return [[1.0, 0.0]] + [[1.0, 0.0] if "SQLite" in text else [0.0, 1.0] for text in texts[1:]]
        with patch("meeting_assistant.providers.embed_texts", side_effect=embed):
            result = self.engine.answer("Which persistence technology enables disconnected operation?", strategy="basic")
        self.assertFalse(result["insufficient_evidence"])
        self.assertIn("SQLite", result["citations"][0]["text"])

    def test_generated_citations_are_validated_and_unknown_ids_fall_back(self):
        self.store.save_settings({"chat_provider": "openai"})
        with patch("meeting_assistant.providers.generate_answer", return_value="The pilot moved to October 22. [E1]"):
            generated = self.engine.answer("Atlas launch", [self.second])
        self.assertEqual(generated["mode"], "generated")
        self.assertEqual([c["id"] for c in generated["citations"]], ["E1"])
        for invalid in ("The launch was cancelled. [E999]", "No references.",
                        "First paragraph without a citation.\n\nSecond paragraph. [E1]", "It moved. [E1, E2]"):
            with patch("meeting_assistant.providers.generate_answer", return_value=invalid):
                fallback = self.engine.answer("Atlas launch", [self.second])
            self.assertEqual(fallback["mode"], "extractive")
            self.assertNotIn("cancelled", fallback["answer"])

    def test_provider_exceptions_do_not_leak_secrets(self):
        self.store.save_settings({"chat_provider": "openai", "embedding_provider": "openai"})
        with patch("meeting_assistant.providers.generate_answer", side_effect=RuntimeError("secret-api-key")), \
             patch("meeting_assistant.providers.embed_texts", side_effect=RuntimeError("secret-api-key")):
            result = self.engine.answer("Atlas launch")
        self.assertNotIn("secret-api-key", str(result))
        self.assertEqual(result["mode"], "extractive")
        self.assertFalse(result["insufficient_evidence"])

    def test_summary_is_explicitly_a_sample(self):
        result = self.engine.answer("Summarize this meeting", [self.first])
        self.assertFalse(result["insufficient_evidence"])
        self.assertIn("sample", result["answer"])
        self.assertLessEqual(len(result["citations"]), 6)


if __name__ == "__main__":
    unittest.main()
