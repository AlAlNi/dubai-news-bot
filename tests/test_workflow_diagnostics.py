import json
import pathlib
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from api_diagnostics import openai_error
from workflow_run import report_result


class DiagnosticsTests(unittest.TestCase):
    def test_api_error_is_actionable_and_redacts_keys(self):
        response = Mock(status_code=400)
        response.json.return_value = {"error": {"code": "unsupported_parameter",
            "param": "tools", "message": "Invalid test-secret sk-another-secret\nvalue"}}
        result = openai_error(response, "test-secret")
        self.assertIn("param=tools", result)
        self.assertIn("unsupported_parameter", result)
        self.assertNotIn("test-secret", result)
        self.assertNotIn("sk-another-secret", result)
        self.assertNotIn("\n", result)

    def test_non_json_error(self):
        response = Mock(status_code=502)
        response.json.side_effect = ValueError()
        self.assertEqual(openai_error(response, "key"), "OpenAI HTTP 502")

    def test_exit_status_distinguishes_empty_queue_from_failure(self):
        for status, body, expected in [
            (200, {"published": False, "reason": "no_posts"}, 0),
            (200, {"published": True}, 0),
            (200, {"new_draft": True}, 0),
            (200, {"technical_errors": 1, "new_draft": False}, 1),
            (400, {"error": "Missing Telegram token"}, 1),
            (500, {"published": False}, 1),
        ]:
            with self.subTest(body=body):
                self.assertEqual(report_result({"statusCode": status, "body": json.dumps(body)}), expected)
