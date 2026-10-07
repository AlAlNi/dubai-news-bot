"""Contract tests with handcrafted decisions; no model quality claim."""
import json
import os
from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from openai_emoji import subject_decorations, apply_decorations, EmojiResponseError
from emoji_review import inserted_emoji, valid_emoji_verdicts
from evaluate_emoji_subjects import evaluate_mock, live_context_allowed


class SubjectSelectionTests(TestCase):
    def test_saved_corpus_mock_choices_preserve_prose_and_review_gate(self):
        corpus = json.loads((ROOT / "tests/fixtures/emoji_subject_corpus.json").read_text(encoding="utf-8"))
        self.assertEqual({case["id"] for case in corpus}, {"parking", "electric_vehicle", "cycling", "education"})
        for case in corpus:
            with self.subTest(case=case["id"]):
                result = evaluate_mock(case)
                self.assertFalse(result["semantic_accuracy_proven"])
                self.assertEqual(result["api_calls"], 0)
                symbol = case["mock_selection"]["decorations"][0]["emoji"]
                self.assertEqual(result["post"].replace(symbol + " ", "", 1), case["text"])
                verdicts = [{**i, "supported": True, "reason": "Mock appropriate subject"} for i in result["insertions"]]
                self.assertTrue(valid_emoji_verdicts(result["post"], verdicts))
                verdicts[0]["supported"] = False
                self.assertFalse(valid_emoji_verdicts(result["post"], verdicts))

    def test_absent_subject_abstention_and_protected_paragraphs(self):
        text = "<b>Parking &amp; cars</b>\n\n<a href='https://example.com'>Link</a>"
        valid = {"decorations": [{"paragraph": 0, "subject": "Parking & cars", "emoji": None}]}
        self.assertEqual(apply_decorations(text, subject_decorations(text, valid)), text)
        self.assertEqual(inserted_emoji(text), [])
        for change in [{"subject": "metro"}, {"subject": ""}, {"subject": " Parking"},
                       {"subject": "x" * 81}, {"paragraph": True}, {"paragraph": 1},
                       {"emoji": "invented"}, {"extra": "rewrite"}]:
            with self.subTest(change=change), self.assertRaises(EmojiResponseError):
                subject_decorations(text, {"decorations": [{**valid["decorations"][0], **change}]})
        self.assertEqual(subject_decorations(text, {"decorations": []}), {"decorations": []})

    def test_subject_presence_does_not_prove_semantic_accuracy(self):
        text = "<b>Велозаезд</b>"
        selected = subject_decorations(text, {"decorations": [{"paragraph": 0, "subject": "Велозаезд", "emoji": "🚇"}]})
        post = apply_decorations(text, selected)
        self.assertFalse(valid_emoji_verdicts(post, [{"paragraph": 0, "emoji": "🚇", "supported": False,
                                                    "reason": "Cycling is not metro"}]))

    def test_live_evaluation_requires_manual_staging_and_no_bypass(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(live_context_allowed())
        env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
               "GITHUB_REF": "refs/heads/main", "BOT_ENVIRONMENT": "staging"}
        with patch.dict(os.environ, env, clear=True):
            self.assertTrue(live_context_allowed())
            os.environ["OPENAI_BYPASS_DAILY_LIMIT"] = "true"
            self.assertFalse(live_context_allowed())
