import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import next_draft  # noqa: E402


class NextDraftImageBlacklistTests(unittest.TestCase):
    def test_blocked_image_url_is_rejected_without_network_call(self) -> None:
        blocked_url = "https://lh3.googleusercontent.com/J6_coFbogxhRI9iM864NL_liGXvsQp2AupsKei7z0cNNfDvGUmWUy20nuUhkREQyrpY4bEeIBuc=s0-w300"

        with patch("next_draft.request_with_retry") as request_mock:
            is_valid = next_draft.is_valid_image_url(blocked_url)

        self.assertFalse(is_valid)
        request_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
