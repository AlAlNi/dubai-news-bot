import pathlib
import sys
import unittest
from unittest.mock import patch
from contextlib import ExitStack

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

    def test_invalid_image_does_not_block_verified_text(self):
        image = next(iter(next_draft.BLOCKED_PUBLICATION_IMAGE_URLS))
        draft = {"title": "Dubai update", "summary_ru": "Verified text", "image_url": image,
                 "source_urls": ["https://example.com/news"]}
        with ExitStack() as stack:
            for name, value in {"load_drafts": [draft], "load_source_stats": [],
                                "load_published_history": [], "is_verified_draft": True,
                                "is_source_recent": False, "is_draft_already_published": False,
                                "save_source_stats": True, "format_draft_for_telegram": "Verified text"}.items():
                stack.enter_context(patch("next_draft." + name, return_value=value))
            stack.enter_context(patch("next_draft.os.path.exists", return_value=True))
            save = stack.enter_context(patch("next_draft.save_drafts", return_value=True))
            request = stack.enter_context(patch("next_draft.request_with_retry"))
            text, selected_image, selected = next_draft.get_next_post_payload_with_image()
        self.assertEqual(text, "Verified text")
        self.assertEqual(selected_image, "")
        self.assertEqual(selected["image_url"], "")
        self.assertEqual(draft["image_url"], image)
        save.assert_called_once_with([])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
