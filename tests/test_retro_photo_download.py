"""Offline regression checks for the bounded approved-photo downloader."""
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import requests
from retro_photo import PHOTO_USER_AGENT, download_approved_photo


class PhotoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.content = b'approved original photo'
        self.item = {
            'source_url': 'https://www.thenationalnews.com/offline-fixture/',
            'image_url': 'https://images.example.com/photo.jpg?private=not-for-logs',
            'publication_review': {
                'image_sha256': hashlib.sha256(self.content).hexdigest(),
            },
        }

    def response(self, status=200, chunks=None):
        response = Mock(status_code=status)
        response.iter_content.return_value = chunks if chunks is not None else [self.content]
        return response

    def test_identifies_bot_and_preserves_download_guards(self):
        response = self.response()
        with patch('retro_photo.requests.get', return_value=response) as get:
            self.assertEqual(download_approved_photo(self.item), self.content)
        get.assert_called_once_with(
            self.item['image_url'], headers={'User-Agent': PHOTO_USER_AGENT},
            stream=True, timeout=30, allow_redirects=False)
        self.assertIn('DubaiNewsBot', PHOTO_USER_AGENT)
        self.assertIn('https://github.com/AlAlNi/dubai-news-bot', PHOTO_USER_AGENT)
        response.iter_content.assert_called_once_with(chunk_size=65536)
        response.close.assert_called_once_with()

    def test_http_failures_log_status_without_retry_or_body_read(self):
        for status in (301, 302, 403, 404, 429, 500):
            with self.subTest(status=status):
                response = self.response(status)
                with patch('retro_photo.requests.get', return_value=response) as get:
                    with self.assertLogs('retro_photo', level='WARNING') as logs:
                        with self.assertRaisesRegex(ValueError, '^image_download_failed$'):
                            download_approved_photo(self.item)
                self.assertIn('HTTP ' + str(status), ' '.join(logs.output))
                self.assertNotIn(self.item['image_url'], ' '.join(logs.output))
                get.assert_called_once()
                response.iter_content.assert_not_called()
                response.close.assert_called_once_with()

    def test_transport_error_is_sanitized_and_not_retried(self):
        with patch('retro_photo.requests.get',
                   side_effect=requests.Timeout('private URL or credential')) as get:
            with self.assertLogs('retro_photo', level='WARNING') as logs:
                with self.assertRaisesRegex(ValueError, '^image_download_failed$'):
                    download_approved_photo(self.item)
        get.assert_called_once()
        self.assertIn('Timeout', ' '.join(logs.output))
        self.assertNotIn('credential', ' '.join(logs.output))

    def test_stream_error_closes_response_and_preserves_error_code(self):
        response = self.response()
        response.iter_content.side_effect = requests.ConnectionError('private detail')
        with patch('retro_photo.requests.get', return_value=response):
            with self.assertLogs('retro_photo', level='WARNING') as logs:
                with self.assertRaisesRegex(ValueError, '^image_download_failed$'):
                    download_approved_photo(self.item)
        response.close.assert_called_once_with()
        self.assertNotIn('private detail', ' '.join(logs.output))

    def test_empty_and_changed_bytes_are_rejected(self):
        for chunks in ([], [b'changed photo']):
            with self.subTest(chunks=chunks):
                response = self.response(chunks=chunks)
                with patch('retro_photo.requests.get', return_value=response):
                    with self.assertRaisesRegex(ValueError, '^approved_image_bytes_changed$'):
                        download_approved_photo(self.item)
                response.close.assert_called_once_with()

    def test_size_limit_remains_ten_mib(self):
        response = self.response(chunks=[b'x' * 65536] * 161)
        with patch('retro_photo.requests.get', return_value=response):
            with self.assertRaisesRegex(ValueError, '^image_download_too_large$'):
                download_approved_photo(self.item)
        response.close.assert_called_once_with()

    def test_missing_hash_prevents_any_request(self):
        self.item['publication_review'] = {}
        with patch('retro_photo.requests.get') as get:
            with self.assertRaisesRegex(ValueError, '^approved_image_hash_required$'):
                download_approved_photo(self.item)
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
