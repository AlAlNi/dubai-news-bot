import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import retro_stage
from retro_publish import select_photo, image_identity
from retro_photos import candidates
from retro_policy import usage_allowed

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = '-1001234567890'
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)
PHOTO = 'https://images.example.com/offline-fixture.jpg'
URL = 'https://www.thenationalnews.com/news/uae/offline-fixture/'


def fixture():
    # Synthetic metadata, never an approved real-world photograph.
    post = json.loads((ROOT/'config/retro_creek_replacement.json').read_text(encoding='utf-8'))['post_html']
    return {'id': 'fixture', 'image_url': PHOTO, 'source_url': URL, 'post_html': post,
            'usage_review': {'status': 'approved', 'basis': 'written_permission',
                             'reviewer': 'offline fixture', 'reviewed_at': '2026-10-08',
                             'evidence': 'SYNTHETIC TEST ONLY', 'rights_holder': 'fixture owner',
                             'attribution': 'Fixture owner', 'image_url': PHOTO, 'source_url': URL,
                             'channel_id': CHANNEL, 'telegram_republication': True}}


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Path(self.tmp.name)/'stage'
        self.production = Path(self.tmp.name)/'production'
        self.production.mkdir()
        self.item = fixture()
        self.source = {'title': 'Photo', 'url': URL, 'text': 'Dubai Creek in the 1980s from the Deira side.'}
        for p in [patch.dict(os.environ, {'GITHUB_ACTIONS': 'false', 'SOURCE_VERIFIER': 'openai', 'BOT_ENVIRONMENT': 'staging',
                    'TELEGRAM_BOT_TOKEN': 'production-secret', 'TELEGRAM_CHANNEL_ID': '-999',
                    'TEST_TELEGRAM_BOT_TOKEN': 'fixture-test-secret', 'TEST_TELEGRAM_CHANNEL_ID': CHANNEL}),
                  patch('retro_publish.load_evidence', return_value=self.source),
                  patch('retro_stage.verify_summary', return_value={'status': 'approved'})]:
            p.start()
            self.addCleanup(p.stop)

    def run_stage(self, send=False):
        return retro_stage.run([self.item], CHANNEL, storage=self.storage,
                               production=self.production, send=send, now=NOW)

    def record(self):
        return json.loads((self.storage/'retro_publications.json').read_text(encoding='utf-8'))['slots'][CHANNEL+':2026-W41']

    def test_dry_prepare_never_sends_or_updates_production(self):
        with patch('retro_stage.requests.post') as post:
            self.assertEqual(self.run_stage()['status'], 'prepared')
        post.assert_not_called()
        self.assertEqual(list(self.production.iterdir()), [])
        self.assertEqual(self.record()['payload']['photo'], PHOTO)

    def test_photo_sent_once_with_durable_reservation(self):
        def send(payload):
            self.assertEqual(self.record()['status'], 'sending')
            self.assertEqual(payload['chat_id'], CHANNEL)
            self.assertEqual(payload['photo'], PHOTO)
            self.assertIn('Источник', payload['caption'])
            return {'status': 'published', 'message_id': 1}
        with patch('retro_stage.send_photo', side_effect=send) as sender:
            self.assertEqual(self.run_stage(True)['status'], 'published')
            self.assertEqual(self.run_stage(True)['previous_status'], 'published')
        sender.assert_called_once()

    def test_rejected_and_deferred_never_send(self):
        with patch('retro_stage.verify_summary', return_value={'status': 'rejected'}), patch('retro_stage.send_photo') as sender:
            self.assertEqual(self.run_stage(True)['status'], 'verification_rejected')
        sender.assert_not_called()

    def test_budget_deferred_blocks_rerun(self):
        with patch('retro_stage.verify_summary', return_value={'status': 'deferred'}), patch('retro_stage.send_photo') as sender:
            self.assertEqual(self.run_stage(True)['status'], 'verification_deferred')
            self.assertEqual(self.run_stage(True)['previous_status'], 'verification_deferred')
        sender.assert_not_called()

    def test_evidence_failures_are_sanitized_and_bounded(self):
        with patch('retro_publish.load_evidence', side_effect=RuntimeError('secret response')) as load:
            result, source, report = select_photo([self.item]*5, set())
        self.assertIsNone(result)
        self.assertEqual(load.call_count, 3)
        self.assertEqual(report['counts'], {'evidence_error': 3, 'evidence_attempt_limit': 2})
        self.assertNotIn('secret', json.dumps(report))

    def test_source_path_and_image_validation_precede_fetch(self):
        with patch('retro_publish.load_evidence') as load:
            result, source, report = select_photo([
                dict(self.item, source_url='https://www.thenationalnews.com/lifestyle/photo'),
                dict(self.item, image_url='http://127.0.0.1/photo')], set())
        load.assert_not_called()
        self.assertEqual(report['counts'], {'unsupported_archive_source': 1, 'invalid_image_url': 1})

    def test_unknown_send_not_retried(self):
        with patch('retro_stage.requests.post', side_effect=TimeoutError('fixture-test-secret')) as post:
            self.assertEqual(self.run_stage(True)['status'], 'send_unknown')
            self.assertEqual(self.run_stage(True)['previous_status'], 'send_unknown')
        post.assert_called_once()
        self.assertNotIn('fixture-test-secret', json.dumps(self.record()))

    def test_final_write_failure_keeps_sending_marker(self):
        original = retro_stage.atomic_json
        def persist(path, state):
            if state.get('slots', {}).get(CHANNEL+':2026-W41', {}).get('status') == 'published':
                raise OSError('disk error')
            original(path, state)
        with patch('retro_stage.atomic_json', side_effect=persist), patch('retro_stage.send_photo', return_value={'status': 'published', 'message_id': 1}) as sender:
            with self.assertRaises(OSError): self.run_stage(True)
            self.assertEqual(self.run_stage(True)['previous_status'], 'sending')
        sender.assert_called_once()

    def test_failed_pre_send_persistence_prevents_send(self):
        with patch('retro_stage.atomic_json', side_effect=OSError('disk')), patch('retro_stage.send_photo') as sender:
            with self.assertRaises(OSError): self.run_stage(True)
        sender.assert_not_called()

    def test_lock_blocks_concurrent_process(self):
        self.storage.mkdir()
        (self.storage/'retro.lock').touch()
        with patch('retro_stage.send_photo') as sender:
            self.assertEqual(self.run_stage(True)['status'], 'locked')
        sender.assert_not_called()
        self.assertTrue((self.storage/'retro.lock').exists())

    def test_replacement_photo_is_used_even_with_resize_variant(self):
        (self.production/'retro_creek_replacement.json').write_text(json.dumps({'status': 'published', 'image_url': PHOTO+'?width=2'}))
        with patch('retro_stage.send_photo') as sender:
            result = self.run_stage(True)
        self.assertEqual(result['status'], 'no_verified_photo')
        self.assertEqual(result['diagnostics']['items'][0]['reason'], 'already_used')
        sender.assert_not_called()

    def test_rights_are_exact_destination_and_photo_specific(self):
        self.assertFalse(usage_allowed(dict(self.item, usage_review='source link'), CHANNEL))
        for key, value in [('basis', 'source_link'), ('image_url', PHOTO+'?different'),
                           ('channel_id', '-999'), ('evidence', ''), ('telegram_republication', False)]:
            item = copy.deepcopy(self.item)
            item['usage_review'][key] = value
            self.assertFalse(usage_allowed(item, CHANNEL))
        self.item.pop('usage_review')
        self.assertEqual(self.run_stage()['diagnostics']['items'][0]['reason'], 'usage_not_approved')

    def test_production_credentials_destination_and_storage_blocked(self):
        with patch.dict(os.environ, {'TEST_TELEGRAM_BOT_TOKEN': 'production-secret'}):
            with self.assertRaises(ValueError): self.run_stage(True)
        with self.assertRaises(ValueError):
            retro_stage.run([], '-999', storage=self.storage, production=self.production)
        with self.assertRaises(ValueError):
            retro_stage.run([], CHANNEL, storage=self.production, production=self.production)

    def test_caption_limit_and_attribution_escaped(self):
        self.item['usage_review']['attribution'] = '<script>test</script>'
        self.assertIn('&lt;script&gt;', retro_stage.photo_payload(self.item, CHANNEL)['caption'])
        self.item['usage_review']['attribution'] = 'x'*1100
        with self.assertRaisesRegex(ValueError, 'caption_too_long'):
            retro_stage.photo_payload(self.item, CHANNEL)

    def test_sendphoto_transport_and_response(self):
        response = Mock(status_code=200)
        response.json.return_value = {'ok': True, 'result': {'chat': {'id': int(CHANNEL)}, 'message_id': 3,
                                                              'photo': [{'file_id': 'fixture'}]}}
        with patch('retro_stage.requests.post', return_value=response) as post:
            self.assertEqual(self.run_stage(True)['status'], 'published')
        self.assertTrue(post.call_args.args[0].endswith('/sendPhoto'))
        self.assertEqual(post.call_args.kwargs['json']['photo'], PHOTO)
        response.close.assert_called_once()

    def test_all_candidates_diagnosed_no_silent_source_filter_or_first30_cutoff(self):
        unsupported = dict(self.item, source_url='https://other.example.com/photo')
        used = dict(self.item, image_url='https://images.example.com/used.jpg')
        result, source, report = select_photo([unsupported]*31+[used, self.item], {image_identity(used['image_url'])})
        self.assertEqual(result, self.item)
        self.assertEqual(len(report['items']), 33)
        self.assertEqual(report['evidence_attempts'], 1)
        self.assertEqual(report['items'][0]['reason'], 'unsupported_archive_source')

    def test_discovery_keeps_unsupported_candidate_with_reason(self):
        row = {'link': 'https://other.example.com/photo', 'imageUrl': PHOTO}
        self.assertEqual(candidates([row], set())[0]['publication_eligibility'], 'unsupported_archive_source')
