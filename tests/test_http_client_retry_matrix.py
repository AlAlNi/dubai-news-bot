import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import http_client  # noqa: E402


class RetryMatrixTests(unittest.TestCase):
    def _response(self, status_code: int) -> Mock:
        response = Mock(spec=requests.Response)
        response.status_code = status_code
        response.headers = {}
        return response

    @patch("http_client.time.sleep")
    @patch("http_client.requests.request")
    def test_get_retries_on_retryable_status(self, request_mock: Mock, sleep_mock: Mock) -> None:
        request_mock.side_effect = [
            self._response(500),
            self._response(200),
        ]

        response = http_client.request_with_retry(
            "GET",
            "https://example.com/news",
            max_attempts=3,
            backoff_seconds=0,
            max_backoff_seconds=0,
            jitter_seconds=0,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0)

    @patch("http_client.time.sleep")
    @patch("http_client.requests.request")
    def test_head_retries_on_connection_error(self, request_mock: Mock, sleep_mock: Mock) -> None:
        request_mock.side_effect = [
            requests.exceptions.ConnectionError("temporary network issue"),
            self._response(200),
        ]

        response = http_client.request_with_retry(
            "HEAD",
            "https://example.com/feed",
            max_attempts=3,
            backoff_seconds=0,
            max_backoff_seconds=0,
            jitter_seconds=0,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0)

    @patch("http_client.time.sleep")
    @patch("http_client.requests.request")
    def test_post_has_no_retry_by_default(self, request_mock: Mock, sleep_mock: Mock) -> None:
        request_mock.return_value = self._response(500)

        response = http_client.request_with_retry(
            "POST",
            "https://example.com/publish",
            max_attempts=3,
            backoff_seconds=0,
            max_backoff_seconds=0,
            jitter_seconds=0,
        )

        self.assertEqual(response.status_code, 500)
        request_mock.assert_called_once()
        sleep_mock.assert_not_called()

    @patch("http_client.time.sleep")
    @patch("http_client.requests.request")
    def test_post_retries_when_opted_in(self, request_mock: Mock, sleep_mock: Mock) -> None:
        request_mock.side_effect = [
            self._response(503),
            self._response(201),
        ]

        response = http_client.request_with_retry(
            "POST",
            "https://example.com/publish",
            max_attempts=3,
            backoff_seconds=0,
            max_backoff_seconds=0,
            jitter_seconds=0,
            retryable_methods={"GET", "POST"},
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0)

    @patch("http_client.time.sleep")
    @patch("http_client.requests.request")
    def test_get_retry_honors_retry_after_seconds(self, request_mock: Mock, sleep_mock: Mock) -> None:
        first = self._response(429)
        first.headers = {"Retry-After": "3"}
        request_mock.side_effect = [first, self._response(200)]

        response = http_client.request_with_retry(
            "GET",
            "https://example.com/news",
            max_attempts=3,
            backoff_seconds=0.2,
            max_backoff_seconds=1,
            jitter_seconds=0,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once_with(3.0)

    @patch("http_client.time.sleep")
    @patch("http_client.requests.request")
    def test_get_retry_honors_retry_after_http_date(self, request_mock: Mock, sleep_mock: Mock) -> None:
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        retry_after_value = retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        first = self._response(503)
        first.headers = {"Retry-After": retry_after_value}
        request_mock.side_effect = [first, self._response(200)]

        response = http_client.request_with_retry(
            "GET",
            "https://example.com/news",
            max_attempts=3,
            backoff_seconds=0.1,
            max_backoff_seconds=1,
            jitter_seconds=0,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request_mock.call_count, 2)
        sleep_mock.assert_called_once()
        self.assertGreaterEqual(sleep_mock.call_args.args[0], 1.0)


if __name__ == "__main__":
    unittest.main()
