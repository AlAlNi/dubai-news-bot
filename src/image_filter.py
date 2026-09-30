"""Bounded image-header inspection before Telegram publication."""
from PIL import ImageFile
from urllib.parse import urljoin
from http_client import request_with_retry
from search_sources import allowed_url, public_destination

MAX_HEADER_BYTES = 256 * 1024
MIN_ASPECT = 0.5
MAX_ASPECT = 2.0


def acceptable_dimensions(width, height):
    return width > 0 and height > 0 and MIN_ASPECT <= width / height <= MAX_ASPECT


def acceptable_image(url):
    """Unknown size or inaccessible image means publish the verified text alone."""
    parser = ImageFile.Parser()
    try:
        for _ in range(4):
            if not allowed_url(url) or not public_destination(url):
                return False
            response = request_with_retry(
                "GET", url, timeout=5, max_attempts=1, stream=True, allow_redirects=False,
                headers={"User-Agent": "DubaiNewsBot/1.0",
                         "Range": f"bytes=0-{MAX_HEADER_BYTES - 1}"},
            )
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return False
                    url = urljoin(url, location)
                    continue
                if response.status_code not in {200, 206}:
                    return False
                total = 0
                for chunk in response.iter_content(chunk_size=8192):
                    remaining = MAX_HEADER_BYTES - total
                    if remaining <= 0:
                        break
                    chunk = chunk[:remaining]
                    total += len(chunk)
                    parser.feed(chunk)
                    if parser.image:
                        width, height = parser.image.size
                        valid = acceptable_dimensions(width, height)
                        print(f"Image dimensions: {width}x{height}; accepted={valid}")
                        return valid
                return False
            finally:
                response.close()
        return False
    except Exception as exc:
        print(f"Image dimensions unavailable: {type(exc).__name__}; using text only")
        return False
    finally:
        if parser.image:
            parser.image.close()
