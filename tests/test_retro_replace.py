import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from retro_replace import run, send_photo, CONFIG
from retro_publish import send_post


class ReplacementTests(unittest.TestCase):
    def test_exact_photo_sent_instead_of_page_preview(self):
        item = json.loads(CONFIG.read_text(encoding='utf-8'))
        response = Mock(status_code=200)
        response.json.return_value = {'ok': True, 'result': {'message_id': 42, 'photo': [{'file_id': 'photo'}]}}
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'secret', 'TELEGRAM_CHANNEL_ID': 'channel'}), patch('retro_replace.requests.post', return_value=response) as post:
            self.assertEqual(send_photo(item)['status'], 'published')
        self.assertTrue(post.call_args.args[0].endswith('/sendPhoto'))
        self.assertEqual(post.call_args.kwargs['json']['photo'], item['image_url'])

    def test_durable_no_duplicate_and_verification_gate(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GITHUB_ACTIONS': 'false', 'SOURCE_VERIFIER': 'openai', 'TELEGRAM_BOT_TOKEN': 'secret', 'TELEGRAM_CHANNEL_ID': 'channel'}), patch('retro_replace.load_evidence', return_value={'text': 'source'}), patch('retro_replace.verify_summary', return_value={'status': 'approved'}), patch('retro_replace.send_photo', return_value={'status': 'published', 'message_id': 42}) as send:
            path = Path(d)/'state.json'
            self.assertEqual(run(path=path)['status'], 'published')
            self.assertEqual(run(path=path)['status'], 'already_attempted')
            send.assert_called_once()

    def test_scheduled_text_posts_cannot_show_unrelated_preview(self):
        response = Mock(status_code=200)
        response.json.return_value = {'ok': True, 'result': {'message_id': 42}}
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'secret', 'TELEGRAM_CHANNEL_ID': 'channel'}), patch('retro_publish.requests.post', return_value=response) as post:
            send_post('text', 'https://example.com/article')
        self.assertEqual(post.call_args.kwargs['json']['link_preview_options'], {'is_disabled': True})

    def test_corrected_caption_retries_only_rejected_first_revision(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GITHUB_ACTIONS': 'false', 'SOURCE_VERIFIER': 'openai', 'TELEGRAM_BOT_TOKEN': 'secret', 'TELEGRAM_CHANNEL_ID': 'channel'}), patch('retro_replace.load_evidence', return_value={'text': 'caption'}) as evidence, patch('retro_replace.verify_summary', return_value={'status': 'rejected'}), patch('retro_replace.send_photo') as send:
            path = Path(d)/'state.json'
            path.write_text(json.dumps({'status': 'verification_rejected', 'revision': 1}), encoding='utf-8')
            self.assertEqual(run(path=path)['status'], 'verification_rejected')
            self.assertEqual(run(path=path)['status'], 'already_attempted')
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['previous_attempt']['revision'], 1)
            self.assertEqual(saved['revision'], 2)
            self.assertFalse(evidence.call_args.kwargs['include_article'])
            send.assert_not_called()
