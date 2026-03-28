import random
import time
from typing import Any, Optional

import requests

DEFAULT_TIMEOUT = 25
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.8
DEFAULT_MAX_BACKOFF_SECONDS = 8.0
DEFAULT_JITTER_SECONDS = 0.35
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRYABLE_METHODS = {"GET", "HEAD"}


def _build_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    jitter_seconds: float,
) -> float:
    exponential = base_delay * (2 ** max(0, attempt - 1))
    delay = min(max_delay, exponential)
    jitter = random.uniform(0, jitter_seconds) if jitter_seconds > 0 else 0
    return delay + jitter


def _is_retryable_method(method: str) -> bool:
    return method.upper() in RETRYABLE_METHODS


def request_with_retry(
    method: str,
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    jitter_seconds: float = DEFAULT_JITTER_SECONDS,
    retryable_methods: Optional[set[str]] = None,
    **kwargs: Any,
) -> requests.Response:
    """
    Единый HTTP wrapper с retry/backoff/jitter и базовой классификацией ошибок.
    Повторяем запрос для:
    - сетевых ошибок (Timeout, ConnectionError),
    - HTTP 429/5xx.
    """
    normalized_method = method.upper()
    allowed_methods = retryable_methods if retryable_methods is not None else RETRYABLE_METHODS
    retryable_method = normalized_method in {m.upper() for m in allowed_methods}
    last_exception: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.request(
                normalized_method,
                url,
                timeout=timeout,
                **kwargs,
            )
        except requests.exceptions.Timeout as exc:
            last_exception = exc
            error_class = "timeout"
        except requests.exceptions.ConnectionError as exc:
            last_exception = exc
            error_class = "connection_error"
        except requests.exceptions.RequestException as exc:
            last_exception = exc
            error_class = "request_exception"
        else:
            last_exception = None
            status_retryable = response.status_code in RETRYABLE_STATUS_CODES
            if not (retryable_method and status_retryable and attempt < max_attempts):
                return response

            delay = _build_delay(
                attempt=attempt,
                base_delay=backoff_seconds,
                max_delay=max_backoff_seconds,
                jitter_seconds=jitter_seconds,
            )
            print(
                f"🔁 HTTP {normalized_method} retry {attempt}/{max_attempts} "
                f"для {url} (status={response.status_code}, delay={delay:.2f}s)"
            )
            time.sleep(delay)
            continue

        if not (retryable_method and attempt < max_attempts):
            raise last_exception

        delay = _build_delay(
            attempt=attempt,
            base_delay=backoff_seconds,
            max_delay=max_backoff_seconds,
            jitter_seconds=jitter_seconds,
        )
        print(
            f"🔁 HTTP {normalized_method} retry {attempt}/{max_attempts} "
            f"для {url} ({error_class}, delay={delay:.2f}s)"
        )
        time.sleep(delay)

    if last_exception:
        raise last_exception

    raise RuntimeError(f"Не удалось выполнить HTTP {normalized_method} {url}")
