import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("staging_entry", ROOT / "scripts" / "run_staging.py")
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)
from openai_budget import Budget, LEDGER_FILENAME


class StagingTests(unittest.TestCase):
    def test_staging_budget_remains_shared(self):
        with patch.dict(os.environ, {"BOT_ENVIRONMENT": "staging"}):
            budget = Budget(ROOT / "storage" / "dubai_news_staging")
            self.assertEqual(budget.path, ROOT / "storage" / "dubai_news" / LEDGER_FILENAME)

    def test_production_and_local_budget_keep_requested_path(self):
        with patch.dict(os.environ, {"BOT_ENVIRONMENT": "production"}):
            self.assertEqual(Budget("custom").path, Path("custom") / LEDGER_FILENAME)

    def test_only_manual_main_can_run_staging(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "BOT_ENVIRONMENT": "staging"}
        with patch.dict(os.environ, env):
            staging.validate_context()
            for key, value in [("GITHUB_REF", "refs/heads/develop"),
                               ("GITHUB_EVENT_NAME", "schedule"),
                               ("BOT_ENVIRONMENT", "production")]:
                with patch.dict(os.environ, {key: value}), self.assertRaises(RuntimeError):
                    staging.validate_context()

    def test_channel_validation_blocks_missing_public_and_production_targets(self):
        env = {"TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHANNEL_ID": "-100123",
               "PRODUCTION_CHANNEL_ID": "@production"}
        def response(chat):
            r = Mock(status_code=200)
            r.json.return_value = {"ok": True, "result": chat}
            return r
        private = {"id": -100123, "type": "channel"}
        prod = {"id": -100456, "type": "channel"}
        post = Mock()
        with patch.dict(os.environ, env), patch.dict(sys.modules, {"requests": SimpleNamespace(post=post)}):
            post.side_effect = [response(private), response(prod)]
            staging.validate_channel()
            for target, production in [(dict(private, username="public"), prod), (private, private)]:
                post.side_effect = [response(target), response(production)]
                with self.assertRaises(RuntimeError):
                    staging.validate_channel()
            post.reset_mock()
            with patch.dict(os.environ, {"TELEGRAM_CHANNEL_ID": ""}), self.assertRaises(RuntimeError):
                staging.validate_channel()
            post.assert_not_called()

    def test_invalid_destination_prevents_collecting_or_spending(self):
        with patch.object(staging, "validate_context"), patch.object(staging, "validate_channel", side_effect=RuntimeError("wrong channel")), patch.object(staging.importlib, "import_module") as imp:
            with self.assertRaises(RuntimeError):
                staging.run("collect_and_publish")
            imp.assert_not_called()

    def test_news_modules_all_use_test_storage(self):
        modules = {name: Mock() for name in ("rss_collect", "next_draft", "auto_notify")}
        modules["rss_collect"].handler.return_value = {"statusCode": 200, "body": '{"new_draft": true}'}
        modules["auto_notify"].handler.return_value = {"statusCode": 200, "body": '{"published": true}'}
        with tempfile.TemporaryDirectory() as d, patch.object(staging, "STORAGE", Path(d)), patch.object(staging, "validate_context"), patch.object(staging, "validate_channel"), patch.object(staging.importlib, "import_module", side_effect=modules.__getitem__), patch.dict(sys.modules, {"workflow_run": SimpleNamespace(report_result=lambda r: 0)}):
            self.assertEqual(staging.run("collect_and_publish"), 0)
            for module in modules.values():
                self.assertEqual(module.MOUNTED_BUCKET_PATH, d)
            modules["auto_notify"].handler.assert_called_once()

    def test_scrub_private_channel_preserves_dedupe_message_id(self):
        with tempfile.TemporaryDirectory() as d, patch.object(staging, "STORAGE", Path(d)):
            path = Path(d) / "published.json"
            path.write_text(json.dumps([{"publication": {"telegram_channel_id": "-100123", "message_id": 42}}]))
            staging.scrub_state()
            self.assertEqual(json.loads(path.read_text()), [{"publication": {"message_id": 42}}])

    def test_workflow_uses_only_test_telegram_token(self):
        workflow = (ROOT / ".github/workflows/staging.yml").read_text()
        self.assertIn("secrets.TEST_TELEGRAM_BOT_TOKEN", workflow)
        self.assertNotIn("secrets.TELEGRAM_BOT_TOKEN", workflow)
        self.assertIn("group: dubai-news-storage", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("git restore --source=FETCH_HEAD --worktree -- src tests config", workflow)


if __name__ == "__main__":
    unittest.main()
