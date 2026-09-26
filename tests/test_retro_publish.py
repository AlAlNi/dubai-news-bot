import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from retro_publish import PhotoCaptions, image_identity, load_evidence, run, send_post, valid_post

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 26, 9, tzinfo=timezone.utc)
SOURCE = {'title': 'Photo', 'text': 'Dubai in the 1980s. Richard Parry.', 'url': 'https://www.thenationalnews.com/news/uae/story/'}


class RetroPublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)
        env = patch.dict(os.environ, {'GITHUB_ACTIONS': 'false', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                                     'SOURCE_VERIFIER': 'openai', 'TELEGRAM_BOT_TOKEN': 'test-secret',
                                     'TELEGRAM_CHANNEL_ID': 'test-channel'})
        env.start()
        self.addCleanup(env.stop)
        self.seed = json.loads((ROOT/'config/retro_first_post.json').read_text(encoding='utf-8'))

    def test_exact_photo_caption_matched_despite_resize_parameters(self):
        parser = PhotoCaptions()
        parser.feed('<img src="https://www.thenationalnews.com/resizer/v2/ABC.jpeg?width=400" alt="Dubai in the 1980s. Photo: Richard Parry.">')
        self.assertEqual(parser.photos[image_identity('https://www.thenationalnews.com/resizer/v2/ABC.jpeg?width=800')],
                         'Dubai in the 1980s. Photo: Richard Parry.')

    def test_no_caption_no_publication_even_if_page_mentions_year(self):
        response = Mock(status_code=200, headers={'Content-Type': 'text/html'})
        response.iter_content.return_value = [b'<article>Dubai 1980 Richard Parry</article><img src="https://example.com/other.jpg" alt="Dubai 1980">']
        with patch('retro_publish.requests.get', return_value=response):
            with self.assertRaisesRegex(ValueError, 'photo_date_or_location'):
                load_evidence(self.seed)
        response.close.assert_called_once()

    def test_agreed_post_keeps_quote_and_fits(self):
        self.assertTrue(valid_post(self.seed['post_html']))
        self.assertIn('<blockquote>', self.seed['post_html'])

    def test_publish_once_and_persist_sending_before_request(self):
        def send(*args):
            state = json.loads((self.storage/'retro_publications.json').read_text())
            self.assertEqual(state['slots']['2026-W39']['status'], 'sending')
            return {'status': 'published', 'message_id': 123}
        with patch('retro_publish.load_evidence', return_value=SOURCE), patch(
            'retro_publish.verify_summary', return_value={'status': 'approved'}
        ), patch('retro_publish.send_post', side_effect=send) as sender, patch('retro_publish.write_post') as writer:
            self.assertEqual(run(self.storage, ROOT/'config/retro_first_post.json', NOW)['status'], 'published')
            self.assertEqual(run(self.storage, ROOT/'config/retro_first_post.json', NOW)['status'], 'already_attempted')
        sender.assert_called_once()
        writer.assert_not_called()

    def test_rejected_or_budget_paused_post_never_sent(self):
        with patch('retro_publish.load_evidence', return_value=SOURCE), patch(
            'retro_publish.verify_summary', return_value={'status': 'deferred', 'reason': 'Monthly budget'}
        ), patch('retro_publish.send_post') as sender:
            result = run(self.storage, ROOT/'config/retro_first_post.json', NOW)
        self.assertEqual(result['status'], 'verification_deferred')
        sender.assert_not_called()

    def test_failed_persistence_prevents_publication(self):
        with patch('retro_publish.load_evidence', return_value=SOURCE), patch(
            'retro_publish.persist', side_effect=RuntimeError('push failed')
        ), patch('retro_publish.send_post') as sender:
            with self.assertRaises(RuntimeError): run(self.storage, ROOT/'config/retro_first_post.json', NOW)
        sender.assert_not_called()

    def test_telegram_timeout_is_not_retried_or_logged_with_secret(self):
        with patch('retro_publish.requests.post', side_effect=TimeoutError('test-secret')) as post:
            self.assertEqual(send_post(self.seed['post_html'], SOURCE['url']), {'status': 'send_unknown'})
        post.assert_called_once()

    def test_unknown_delivery_blocks_rerun(self):
        with patch('retro_publish.load_evidence', return_value=SOURCE), patch(
            'retro_publish.verify_summary', return_value={'status': 'approved'}
        ), patch('retro_publish.send_post', return_value={'status': 'send_unknown'}) as sender:
            run(self.storage, ROOT/'config/retro_first_post.json', NOW)
            result = run(self.storage, ROOT/'config/retro_first_post.json', NOW)
        self.assertEqual(result['previous_status'], 'send_unknown')
        sender.assert_called_once()

    def test_first_post_revision_retries_rejection_once_and_preserves_record(self):
        state = {'version': 1, 'slots': {'2026-W39': {
            'status': 'verification_rejected', 'image_identity': image_identity(self.seed['image_url']),
            'verification': {'status': 'rejected'}}}}
        path = self.storage/'retro_publications.json'
        path.write_text(json.dumps(state))
        with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'push'}), patch('retro_publish.load_evidence', return_value=SOURCE), patch(
            'retro_publish.verify_summary', return_value={'status': 'approved'}
        ), patch('retro_publish.send_post', return_value={'status': 'published', 'message_id': 321}) as sender:
            self.assertEqual(run(self.storage, ROOT/'config/retro_first_post.json', NOW)['status'], 'published')
            self.assertEqual(run(self.storage, ROOT/'config/retro_first_post.json', NOW)['status'], 'already_attempted')
        sender.assert_called_once()
        saved = json.loads(path.read_text())['slots']['2026-W39']
        self.assertEqual(saved['previous_attempt']['status'], 'verification_rejected')
        self.assertEqual(saved['seed_revision'], 2)
