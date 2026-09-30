import io
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from image_filter import acceptable_dimensions, acceptable_image, MAX_HEADER_BYTES
import next_draft
import auto_notify


def image_bytes(size, fmt="PNG"):
    data = io.BytesIO()
    Image.new("RGB", size).save(data, format=fmt)
    return data.getvalue()


class ImageFilterTests(TestCase):
    def test_ratios_include_normal_portraits_and_landscapes_not_banners(self):
        for size in [(1200, 630), (1920, 1080), (1080, 1920), (600, 600), (1000, 500), (500, 1000)]:
            self.assertTrue(acceptable_dimensions(*size), size)
        for size in [(441, 64), (64, 441), (1001, 500), (500, 1001), (0, 300), (300, 0)]:
            self.assertFalse(acceptable_dimensions(*size), size)

    def check(self, content, status=200):
        response = Mock(status_code=status, headers={})
        response.iter_content.return_value = [content]
        with patch("image_filter.public_destination", return_value=True), patch(
                "image_filter.request_with_retry", return_value=response) as request:
            result = next_draft.is_valid_image_url("https://example.com/photo.jpg")
        response.close.assert_called_once()
        self.assertEqual(request.call_args.args[0], "GET")
        self.assertTrue(request.call_args.kwargs["stream"])
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        return result

    def test_jpg_suffix_does_not_bypass_actual_dimensions(self):
        self.assertFalse(self.check(image_bytes((441, 64))))
        self.assertFalse(self.check(image_bytes((64, 441), "JPEG")))
        self.assertTrue(self.check(image_bytes((640, 480), "JPEG")))
        self.assertTrue(self.check(image_bytes((480, 640), "WEBP")))

    def test_bad_or_blocked_images_are_skipped(self):
        self.assertFalse(self.check(b"<html>not an image</html>"))
        self.assertFalse(self.check(b"denied", status=403))
        self.assertFalse(self.check(b"x" * (MAX_HEADER_BYTES + 50)))

    def test_private_redirect_not_fetched(self):
        response = Mock(status_code=302, headers={"Location": "https://127.0.0.1/image.jpg"})
        with patch("image_filter.public_destination", return_value=True), patch(
                "image_filter.request_with_retry", return_value=response) as request:
            self.assertFalse(acceptable_image("https://example.com/image.png"))
            request.assert_called_once()
        response.close.assert_called_once()

    def test_stream_stops_as_soon_as_dimensions_known(self):
        response = Mock(status_code=200, headers={})
        def chunks():
            yield image_bytes((441, 64))
            raise AssertionError("Should not download the remaining image")
        response.iter_content.side_effect = lambda **kwargs: chunks()
        with patch("image_filter.public_destination", return_value=True), patch(
                "image_filter.request_with_retry", return_value=response):
            self.assertFalse(acceptable_image("https://example.com/banner"))
        response.close.assert_called_once()

    def test_timeout_falls_back_to_text(self):
        with patch("image_filter.public_destination", return_value=True), patch(
                "image_filter.request_with_retry", side_effect=TimeoutError):
            self.assertFalse(acceptable_image("https://example.com/photo.jpg"))

    def test_text_only_publication_disables_link_preview(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"message_id": 1}}
        with patch("auto_notify.request_with_retry", return_value=response) as request:
            result = auto_notify.send_telegram_message("Verified text", None, disable_link_preview=True)
        self.assertTrue(result["success"])
        self.assertEqual(request.call_args.kwargs["json"]["link_preview_options"],
                         {"is_disabled": True})

    def test_normal_text_post_keeps_link_preview(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True, "result": {"message_id": 1}}
        with patch("auto_notify.request_with_retry", return_value=response) as request:
            auto_notify.send_telegram_message("Verified text", None)
        self.assertNotIn("link_preview_options", request.call_args.kwargs["json"])
