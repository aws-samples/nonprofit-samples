# tests/test_dashboard.py
# Unit tests for dashboard.py — FastAPI SSE Dashboard Server
#
# New reporting contract (see design.md "Testing Strategy" item 2 and "Data Models"):
# each payload declares naive_cost_usd; the server computes
# request_savings = max(0, naive_cost - cost_usd), accumulates cumulative_savings_usd
# and cumulative_naive_usd, and projects daily metrics using ASSUMED_REQUESTS_PER_DAY.
# Validates: Requirements 1.4, 5.1, 5.2, 5.4, 6.5, 7.3

import sys
import os
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


try:
    from fastapi.testclient import TestClient
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

pytestmark = pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not installed")


def get_test_client():
    """Import dashboard app and return a TestClient. Resets state each time."""
    import importlib
    import dashboard as dashboard_module
    importlib.reload(dashboard_module)
    return TestClient(dashboard_module.app), dashboard_module


@pytest.fixture
def client():
    test_client, _ = get_test_client()
    return test_client


@pytest.fixture
def client_and_module():
    return get_test_client()


# ── POST /api/cost ────────────────────────────────────────────────────────────

class TestPostApiCost:

    def test_post_cost_returns_200(self, client):
        payload = {
            "model_name": "amazon.nova-lite-v1:0",
            "input_tokens": 500,
            "output_tokens": 200,
            "cost_usd": "0.0001",
            "naive_cost_usd": "0.005",
            "request_label": "01_naive.py",
        }
        response = client.post("/api/cost", json=payload)
        assert response.status_code == 200

    def test_post_cost_rejects_payload_missing_naive_cost(self, client):
        """naive_cost_usd is a required field in the new reporting contract."""
        payload = {
            "model_name": "amazon.nova-lite-v1:0",
            "input_tokens": 500,
            "output_tokens": 200,
            "cost_usd": "0.0001",
            "request_label": "01_naive.py",
        }
        response = client.post("/api/cost", json=payload)
        assert response.status_code == 422

    def test_post_cost_updates_cumulative_cost(self, client_and_module):
        test_client, module = client_and_module
        payload = {
            "model_name": "anthropic.claude-opus-4-8",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cost_usd": "0.0225",
            "naive_cost_usd": "0.0225",
            "request_label": "01_naive.py",
        }
        test_client.post("/api/cost", json=payload)
        assert module.session_state["cumulative_cost_usd"] == Decimal("0.0225")

    def test_post_cost_increments_request_count(self, client_and_module):
        test_client, module = client_and_module
        payload = {
            "model_name": "amazon.nova-lite-v1:0",
            "input_tokens": 200,
            "output_tokens": 100,
            "cost_usd": "0.000024",
            "naive_cost_usd": "0.0015",
            "request_label": "02_router.py",
        }
        test_client.post("/api/cost", json=payload)
        assert module.session_state["request_count"] == 1

    def test_post_cost_savings_from_reported_naive(self, client_and_module):
        """request_savings = max(0, naive_cost - cost_usd), taken from the payload
        rather than recomputed from tokens."""
        test_client, module = client_and_module
        payload = {
            "model_name": "amazon.nova-lite-v1:0",
            "input_tokens": 200,
            "output_tokens": 100,
            "cost_usd": "0.0010",
            "naive_cost_usd": "0.0250",
            "request_label": "02_router.py",
        }
        test_client.post("/api/cost", json=payload)
        assert module.session_state["cumulative_savings_usd"] == Decimal("0.0240")

    def test_post_cost_accumulates_cumulative_naive(self, client_and_module):
        """The server accumulates the reported naive baseline separately."""
        test_client, module = client_and_module
        payload = {
            "model_name": "amazon.nova-lite-v1:0",
            "input_tokens": 200,
            "output_tokens": 100,
            "cost_usd": "0.0010",
            "naive_cost_usd": "0.0250",
            "request_label": "02_router.py",
        }
        test_client.post("/api/cost", json=payload)
        assert module.session_state["cumulative_naive_usd"] == Decimal("0.0250")

    def test_post_cost_savings_clamped_to_zero_when_cost_exceeds_naive(self, client_and_module):
        """Savings never go negative: cost above the declared naive baseline yields $0."""
        import config
        test_client, module = client_and_module
        payload = {
            "model_name": config.CHAMPAGNE_MODEL_ID,
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": "999.99",
            "naive_cost_usd": "0.01",
            "request_label": "01_naive.py",
        }
        test_client.post("/api/cost", json=payload)
        assert module.session_state["cumulative_savings_usd"] == Decimal("0")
        # naive still accumulates even when per-request savings is clamped
        assert module.session_state["cumulative_naive_usd"] == Decimal("0.01")

    def test_post_cost_naive_equal_to_cost_yields_zero_savings(self, client_and_module):
        """Naive-baseline page (Req 1.5): naive == cost, so savings is $0."""
        test_client, module = client_and_module
        payload = {
            "model_name": "amazon.nova-lite-v1:0",
            "input_tokens": 100,
            "output_tokens": 50,
            "cost_usd": "0.0075",
            "naive_cost_usd": "0.0075",
            "request_label": "01_naive.py",
        }
        test_client.post("/api/cost", json=payload)
        assert module.session_state["cumulative_savings_usd"] == Decimal("0")
        assert module.session_state["cumulative_cost_usd"] == Decimal("0.0075")
        assert module.session_state["cumulative_naive_usd"] == Decimal("0.0075")

    def test_post_cost_updates_active_file(self, client_and_module):
        """active_file reflects the most recent request_label."""
        test_client, module = client_and_module
        payload = {
            "model_name": "amazon.nova-lite-v1:0",
            "input_tokens": 50,
            "output_tokens": 25,
            "cost_usd": "0.000003",
            "naive_cost_usd": "0.000075",
            "request_label": "03_cache.py",
        }
        test_client.post("/api/cost", json=payload)
        assert module.session_state["active_file"] == "03_cache.py"


# ── POST /api/reset ───────────────────────────────────────────────────────────

class TestPostApiReset:

    def test_reset_clears_cumulative_cost(self, client_and_module):
        test_client, module = client_and_module
        test_client.post("/api/cost", json={
            "model_name": "amazon.nova-lite-v1:0", "input_tokens": 100,
            "output_tokens": 50, "cost_usd": "0.005", "naive_cost_usd": "0.05",
            "request_label": "01_naive.py"
        })
        test_client.post("/api/reset")
        assert module.session_state["cumulative_cost_usd"] == Decimal("0")

    def test_reset_clears_savings(self, client_and_module):
        test_client, module = client_and_module
        test_client.post("/api/cost", json={
            "model_name": "amazon.nova-lite-v1:0", "input_tokens": 100,
            "output_tokens": 50, "cost_usd": "0.000006", "naive_cost_usd": "0.0005",
            "request_label": "02_router.py"
        })
        test_client.post("/api/reset")
        assert module.session_state["cumulative_savings_usd"] == Decimal("0")

    def test_reset_clears_cumulative_naive(self, client_and_module):
        test_client, module = client_and_module
        test_client.post("/api/cost", json={
            "model_name": "amazon.nova-lite-v1:0", "input_tokens": 100,
            "output_tokens": 50, "cost_usd": "0.000006", "naive_cost_usd": "0.0005",
            "request_label": "02_router.py"
        })
        test_client.post("/api/reset")
        assert module.session_state["cumulative_naive_usd"] == Decimal("0")

    def test_reset_clears_request_count(self, client_and_module):
        test_client, module = client_and_module
        test_client.post("/api/cost", json={
            "model_name": "amazon.nova-lite-v1:0", "input_tokens": 50,
            "output_tokens": 25, "cost_usd": "0.000003", "naive_cost_usd": "0.000075",
            "request_label": "01_naive.py"
        })
        test_client.post("/api/reset")
        assert module.session_state["request_count"] == 0

    def test_reset_returns_200(self, client):
        response = client.post("/api/reset")
        assert response.status_code == 200


# ── spend_color logic ─────────────────────────────────────────────────────────

class TestSpendColor:

    def test_below_warning_returns_normal(self, client_and_module):
        _, module = client_and_module
        color = module.get_spend_color(Decimal("0.50"))
        assert color == "normal"

    def test_zero_returns_normal(self, client_and_module):
        _, module = client_and_module
        color = module.get_spend_color(Decimal("0"))
        assert color == "normal"

    def test_at_warning_returns_yellow(self, client_and_module):
        import config
        _, module = client_and_module
        color = module.get_spend_color(config.WARNING_SPEND_USD)
        assert color == "yellow"

    def test_between_warning_and_danger_returns_yellow(self, client_and_module):
        import config
        _, module = client_and_module
        midpoint = (config.WARNING_SPEND_USD + config.DANGER_SPEND_USD) / 2
        color = module.get_spend_color(midpoint)
        assert color == "yellow"

    def test_at_danger_returns_red(self, client_and_module):
        import config
        _, module = client_and_module
        color = module.get_spend_color(config.DANGER_SPEND_USD)
        assert color == "red"

    def test_above_danger_returns_red(self, client_and_module):
        import config
        _, module = client_and_module
        color = module.get_spend_color(config.DANGER_SPEND_USD + Decimal("1.00"))
        assert color == "red"


# ── GET / (dashboard HTML page) ──────────────────────────────────────────────

class TestDashboardHtml:

    def test_get_root_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_get_root_returns_html(self, client):
        response = client.get("/")
        assert "text/html" in response.headers.get("content-type", "")


# ── Accumulation over a known sequence ────────────────────────────────────────

class TestAccumulationSequence:
    """Feed a known sequence of (cost_usd, naive_cost_usd) payloads and assert the
    cumulative totals and daily projections are exact. (Testing Strategy item 2)"""

    # (cost_usd, naive_cost_usd) for three logical requests:
    #   1. optimized win:  naive 0.0300, cost 0.0050  -> savings 0.0250
    #   2. naive baseline: naive 0.0200, cost 0.0200  -> savings 0.0000
    #   3. clamped:        naive 0.0100, cost 0.0400  -> savings 0.0000 (max(0, -0.03))
    SEQUENCE = [
        ("0.0050", "0.0300"),
        ("0.0200", "0.0200"),
        ("0.0400", "0.0100"),
    ]
    EXPECTED_COST    = Decimal("0.0650")   # 0.0050 + 0.0200 + 0.0400
    EXPECTED_SAVINGS = Decimal("0.0250")   # 0.0250 + 0 + 0 (per-request clamp)
    EXPECTED_NAIVE   = Decimal("0.0600")   # 0.0300 + 0.0200 + 0.0100 (never clamped)
    EXPECTED_COUNT   = 3

    def _post_sequence(self, test_client):
        for cost, naive in self.SEQUENCE:
            test_client.post("/api/cost", json={
                "model_name": "amazon.nova-lite-v1:0",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": cost,
                "naive_cost_usd": naive,
                "request_label": "02_router.py",
            })

    def test_cumulative_cost_exact(self, client_and_module):
        test_client, module = client_and_module
        self._post_sequence(test_client)
        assert module.session_state["cumulative_cost_usd"] == self.EXPECTED_COST

    def test_cumulative_savings_exact(self, client_and_module):
        test_client, module = client_and_module
        self._post_sequence(test_client)
        assert module.session_state["cumulative_savings_usd"] == self.EXPECTED_SAVINGS

    def test_cumulative_naive_exact(self, client_and_module):
        test_client, module = client_and_module
        self._post_sequence(test_client)
        assert module.session_state["cumulative_naive_usd"] == self.EXPECTED_NAIVE

    def test_request_count_exact(self, client_and_module):
        test_client, module = client_and_module
        self._post_sequence(test_client)
        assert module.session_state["request_count"] == self.EXPECTED_COUNT

    def test_snapshot_cumulative_naive_serialized(self, client_and_module):
        """The SSE snapshot exposes cumulative_naive for the banner percentage."""
        test_client, module = client_and_module
        self._post_sequence(test_client)
        snapshot = module.build_sse_snapshot()
        assert "cumulative_naive" in snapshot
        assert snapshot["cumulative_naive"] == f"{self.EXPECTED_NAIVE:.4f}"


class TestDailyProjection:
    """Daily projections must use config.ASSUMED_REQUESTS_PER_DAY (== 10,000)."""

    SEQUENCE = TestAccumulationSequence.SEQUENCE

    def _post_sequence(self, test_client):
        for cost, naive in self.SEQUENCE:
            test_client.post("/api/cost", json={
                "model_name": "amazon.nova-lite-v1:0",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": cost,
                "naive_cost_usd": naive,
                "request_label": "02_router.py",
            })

    def test_daily_savings_uses_assumed_requests_per_day(self, client_and_module):
        import config
        test_client, module = client_and_module
        self._post_sequence(test_client)

        state = module.session_state
        savings_per_request = state["cumulative_savings_usd"] / state["request_count"]
        expected_daily = savings_per_request * config.ASSUMED_REQUESTS_PER_DAY

        snapshot = module.build_sse_snapshot()
        actual_daily = Decimal(snapshot["daily_savings"])

        assert actual_daily == expected_daily.quantize(Decimal("0.01")), (
            f"Daily savings {actual_daily} does not match projection {expected_daily}"
        )
        # Sanity: config constant is the agreed 10K/day workload.
        assert config.ASSUMED_REQUESTS_PER_DAY == 10_000

    def test_estimated_daily_cost_uses_assumed_requests_per_day(self, client_and_module):
        import config
        test_client, module = client_and_module
        self._post_sequence(test_client)

        state = module.session_state
        cost_per_request = state["cumulative_cost_usd"] / state["request_count"]
        expected_daily = cost_per_request * config.ASSUMED_REQUESTS_PER_DAY

        snapshot = module.build_sse_snapshot()
        actual_daily = Decimal(snapshot["estimated_daily_cost"])

        assert actual_daily == expected_daily.quantize(Decimal("0.01")), (
            f"Daily cost {actual_daily} does not match projection {expected_daily}"
        )

    def test_projections_zero_before_any_request(self, client_and_module):
        test_client, module = client_and_module
        snapshot = module.build_sse_snapshot()
        assert Decimal(snapshot["daily_savings"]) == Decimal("0")
        assert Decimal(snapshot["estimated_daily_cost"]) == Decimal("0")
