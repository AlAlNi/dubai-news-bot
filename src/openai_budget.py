"""Conservative spend reservations, durable before an OpenAI request is sent."""
import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


# GPT-4.1 mini standard prices: $0.40/M input, $1.60/M output (2026-09-15).
# Pinned/allowlisted model; other models require a price/cap review.
MODEL = "gpt-4.1-mini-2025-04-14"
INPUT_TOKEN_CEILING = 20000
OUTPUT_TOKEN_CEILING = 1600
RESERVATION_MICROUSD = 10560
SEARCH_RESERVATION_MICROUSD = 25000
# Astra: one web-search call, <=8K prompt, <=2K output, 128K search context.
# Includes prompt replay and the higher cache-write input rate ($12.50/M).
# Reserve before sending; settle only from a complete, validated API receipt.
ASTRA_MODEL = "gpt-6-astra"
ASTRA_OUTPUT_TOKENS = 2000
ASTRA_RESERVATION_MICROUSD = 2000000
LEDGER_FILENAME = "openai_budget.json"


class BudgetUnavailable(RuntimeError):
    pass


def manual_daily_limit_bypass():
    return (os.getenv("GITHUB_ACTIONS") == "true"
            and os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.getenv("GITHUB_REF") == "refs/heads/main"
            and os.getenv("OPENAI_BYPASS_DAILY_LIMIT") == "true")


def limits():
    try:
        monthly = Decimal(os.getenv("OPENAI_MONTHLY_BUDGET_USD", "3"))
        daily = int(os.getenv("OPENAI_MAX_CALLS_PER_DAY", "8"))
        if not monthly.is_finite() or not 0 <= monthly <= 3 or not 0 <= daily <= 8:
            raise ValueError()
        return int(monthly * 1000000), daily
    except (ValueError, ArithmeticError):
        raise BudgetUnavailable("Invalid budget config: maximum $3/month and 8 calls/day")


def search_limit():
    try:
        value = int(os.getenv("OPENAI_MAX_SEARCHES_PER_DAY", "2"))
        if not 0 <= value <= 2:
            raise ValueError()
        return value
    except ValueError:
        raise BudgetUnavailable("Invalid search limit: maximum 2 searches/day")


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Budget:
    def __init__(self, storage_dir, now=None):
        self.path = Path(storage_dir) / LEDGER_FILENAME
        self.now = now or datetime.now(timezone.utc)
        self.month = self.now.strftime("%Y-%m")
        self.day = self.now.strftime("%Y-%m-%d")

    @contextmanager
    def locked(self):
        lock = self.path.with_suffix(".lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            raise BudgetUnavailable("Budget ledger unavailable or locked")
        try:
            os.close(fd)
            yield
        finally:
            lock.unlink(missing_ok=True)

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("months"), dict):
                raise ValueError()
            for month in data["months"].values():
                if not isinstance(month, dict) or not isinstance(month.get("days"), dict):
                    raise ValueError()
                if type(month.get("reserved_microusd")) is not int or month["reserved_microusd"] < 0:
                    raise ValueError()
                for day in month["days"].values():
                    for field in ("calls", "reserved_microusd", "input_tokens", "output_tokens", "estimated_microusd"):
                        if type(day.get(field)) is not int or day[field] < 0:
                            raise ValueError()
                    searches = day.get("search_calls", 0)  # Existing v1 ledgers contain verification only.
                    if type(searches) is not int or searches < 0:
                        raise ValueError()
                    expected = day["calls"] * RESERVATION_MICROUSD + searches * SEARCH_RESERVATION_MICROUSD
                    requests = day.get("astra_requests", {})
                    if not isinstance(requests, dict):
                        raise ValueError()
                    for request in requests.values():
                        if not isinstance(request, dict):
                            raise ValueError()
                        cost = request.get("charged_microusd")
                        if cost is not None and (type(cost) is not int or cost < 10000):
                            raise ValueError()
                        expected += ASTRA_RESERVATION_MICROUSD if cost is None else cost
                    if day["reserved_microusd"] != expected:
                        raise ValueError()
                if month["reserved_microusd"] != sum(d["reserved_microusd"] for d in month["days"].values()):
                    raise ValueError()
            return data
        except (OSError, ValueError, TypeError, AttributeError):
            raise BudgetUnavailable("Budget ledger missing or corrupt; refusing to reset it")

    def persist_before_spend(self):
        if os.getenv("GITHUB_ACTIONS") != "true":
            return
        if os.getenv("GITHUB_REF") != "refs/heads/main":
            raise BudgetUnavailable("OpenAI spending is enabled only on main")
        root = Path(__file__).resolve().parents[1]
        try:
            relative = self.path.resolve().relative_to(root).as_posix()
            for args in (
                ["add", "--", relative],
                ["commit", "--only", "-m", "Reserve OpenAI budget [skip ci]", "--", relative],
                ["push", "origin", "HEAD:main"],
            ):
                subprocess.run(["git", "-C", str(root), *args], check=True,
                               capture_output=True, timeout=60)
        except (ValueError, OSError, subprocess.SubprocessError):
            raise BudgetUnavailable("Budget reservation could not be pushed; OpenAI request cancelled")

    def reserve(self, kind="verification"):
        if kind not in {"verification", "search", "astra_search"}:
            raise BudgetUnavailable("Unknown OpenAI operation")
        monthly_limit, daily_limit = limits()
        bypass_daily = manual_daily_limit_bypass()
        amount = {"search": SEARCH_RESERVATION_MICROUSD,
                  "astra_search": ASTRA_RESERVATION_MICROUSD,
                  "verification": RESERVATION_MICROUSD}[kind]
        reservation_id = None
        with self.locked():
            data = self.read()
            month = data["months"].setdefault(self.month, {"reserved_microusd": 0, "days": {}})
            day = month["days"].setdefault(self.day, {
                "calls": 0, "reserved_microusd": 0, "input_tokens": 0,
                "output_tokens": 0, "estimated_microusd": 0,
            })
            if not bypass_daily and day["calls"] >= daily_limit:
                raise BudgetUnavailable("Daily OpenAI call limit reached")
            # Do not buy discovery if no budget remains to verify even one resulting post.
            headroom = RESERVATION_MICROUSD if kind != "verification" else 0
            if month["reserved_microusd"] + amount + headroom > monthly_limit:
                raise BudgetUnavailable("Monthly OpenAI budget reached")
            if kind != "verification":
                daily_search_limit = search_limit()
                search_count = day.get("search_calls", 0) + len(day.get("astra_requests", {}))
                if not bypass_daily and search_count >= daily_search_limit:
                    raise BudgetUnavailable("Daily OpenAI search limit reached")
                if kind == "astra_search":
                    reservation_id = uuid.uuid4().hex
                    day.setdefault("astra_requests", {})[reservation_id] = {"charged_microusd": None}
                else:
                    day["search_calls"] = day.get("search_calls", 0) + 1
            else:
                day["calls"] += 1
            day["reserved_microusd"] += amount
            month["reserved_microusd"] += amount
            atomic_json(self.path, data)
            self.persist_before_spend()
        return reservation_id

    def settle_astra(self, reservation_id, response):
        """Settle once; absent/invalid receipts and interrupted requests keep the reserve."""
        if (not isinstance(response, dict) or response.get("status") != "completed"
                or response.get("model") != ASTRA_MODEL
                or response.get("service_tier", "default") != "default"):
            return False
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return False
        incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
        if (type(incoming) is not int or type(outgoing) is not int
                or incoming <= 0 or outgoing < 0
                or outgoing > ASTRA_OUTPUT_TOKENS or incoming > 150000):
            return False
        # Includes reasoning tokens in output. Never assume cached-input discounts;
        # use cache-write rate for ALL input tokens. One hosted search costs $0.01.
        cost = (incoming * 25 + 1) // 2 + outgoing * 50 + 10000
        with self.locked():
            data = self.read()
            month = data["months"][self.month]
            day = month["days"][self.day]
            request = day.get("astra_requests", {}).get(reservation_id)
            if request is None or request["charged_microusd"] is not None:
                return False
            request["charged_microusd"] = cost
            delta = cost - ASTRA_RESERVATION_MICROUSD
            month["reserved_microusd"] += delta
            day["reserved_microusd"] += delta
            day["input_tokens"] += incoming
            day["output_tokens"] += outgoing
            day["estimated_microusd"] += cost
            atomic_json(self.path, data)
        return True

    def record_usage(self, usage, search_calls=0):
        # Reservations are NEVER refunded, including on timeout or runner crash.
        if not isinstance(usage, dict):
            return
        incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if type(incoming) is not int or type(outgoing) is not int or min(incoming, outgoing) < 0:
            return
        with self.locked():
            data = self.read()
            day = data["months"][self.month]["days"][self.day]
            day["input_tokens"] += incoming
            day["output_tokens"] += outgoing
            # Round UP; do not assume the cached-input discount.
            day["estimated_microusd"] += (incoming * 4 + outgoing * 16 + 9) // 10
            # Search content is billed as 8,000 input tokens/call for this model.
            # Conservatively add it even if a response's usage already includes it.
            day["estimated_microusd"] += search_calls * 13200
            atomic_json(self.path, data)
