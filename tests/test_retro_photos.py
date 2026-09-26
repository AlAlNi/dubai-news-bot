import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from retro_photos import collect, candidates, gallery

ROW = {'title': '<script>test</script>', 'imageUrl': 'https://images.example.com/dubai.jpg',
       'link': 'https://archive.example.com/photo'}


class RetroTests(unittest.TestCase):
    def test_dedupe_and_no_unverified_year(self):
        seen = set()
        result = candidates([ROW, dict(ROW, imageUrl=ROW['imageUrl']+'?width=600')], seen)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]['year'])
        self.assertEqual(candidates([ROW], seen), [])
        self.assertEqual(candidates([dict(ROW, imageUrl='http://127.0.0.1/x')], set()), [])

    def test_weekly_cache_and_secret_not_saved(self):
        response = Mock(status_code=200)
        response.json.return_value = {'images': [ROW]}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SERPER_API_KEY': 'secret-value', 'GITHUB_ACTIONS': 'false'}), patch('retro_photos.requests.post', return_value=response) as post:
            path = Path(d)/'state.json'
            now = datetime(2026, 9, 26, tzinfo=timezone.utc)
            state, cached = collect(path, now)
            self.assertFalse(cached)
            self.assertTrue(collect(path, now)[1])
            post.assert_called_once()
            self.assertNotIn('secret-value', path.read_text())
            out = Path(d)/'gallery.html'
            gallery(state, out)
            self.assertNotIn('<script>', out.read_text(encoding='utf-8'))

    def test_failed_reservation_prevents_paid_call(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SERPER_API_KEY': 'key', 'GITHUB_ACTIONS': 'false'}), patch('retro_photos.persist', side_effect=RuntimeError('push failed')), patch('retro_photos.requests.post') as post:
            with self.assertRaises(RuntimeError): collect(Path(d)/'state.json')
            post.assert_not_called()

    def test_error_consumes_slot_without_saving_response_body(self):
        response = Mock(status_code=401, text='sensitive')
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'SERPER_API_KEY': 'key', 'GITHUB_ACTIONS': 'false'}), patch('retro_photos.requests.post', return_value=response) as post:
            path = Path(d)/'state.json'
            with self.assertRaises(RuntimeError): collect(path)
            self.assertTrue(collect(path)[1])
            post.assert_called_once()
            self.assertNotIn('sensitive', path.read_text())
