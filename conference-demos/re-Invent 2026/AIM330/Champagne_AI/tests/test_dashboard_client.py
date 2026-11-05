# tests/test_dashboard_client.py
# Unit tests for dashboard_client.py
# Tests: report_cost() returns normally in all dashboard states and payload validation.
#
# Reporting contract (see design.md "Testing Strategy" item 1 and "Data Models"):
# report_cost() takes a required naive_cost_usd: Decimal after cost_usd and
# serializes it in the payload as the string "naive_cost_usd", mirroring cost_usd.
# Validates: Requirements 1.1, 1.2

import sys
import os
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

# Make sure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard_client


class TestReportCostSuccess:
    """report_cost() should return None when dashboard responds 200."""

    def test_returns_none_on_200(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("dashboard_client.requests.post", return_value=mock_response) as mock_post:
            result = dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=500,
                output_tokens=200,
                cost_usd=Decimal("0.0001"),
                naive_cost_usd=Decimal("0.005"),
                request_label="01_naive.py",
            )

        assert result is None

    def test_post_called_with_correct_url(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("dashboard_client.requests.post", return_value=mock_response) as mock_post:
            dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.0002"),
                naive_cost_usd=Decimal("0.004"),
                request_label="02_router.py",
            )

        call_args = mock_post.call_args
        import config
        assert call_args[0][0] == config.DASHBOARD_URL

    def test_payload_contains_all_six_fields(self):
        """Verify the POST payload contains all 6 required fields, including naive_cost_usd."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_payload = {}

        def capture_post(url, json=None, timeout=None):
            captured_payload.update(json or {})
            return mock_response

        with patch("dashboard_client.requests.post", side_effect=capture_post):
            dashboard_client.report_cost(
                model_name="anthropic.claude-opus-4-8",
                input_tokens=1000,
                output_tokens=300,
                cost_usd=Decimal("0.0075"),
                naive_cost_usd=Decimal("0.0300"),
                request_label="05_pipeline.py",
            )

        assert "model_name" in captured_payload
        assert "input_tokens" in captured_payload
        assert "output_tokens" in captured_payload
        assert "cost_usd" in captured_payload
        assert "naive_cost_usd" in captured_payload
        assert "request_label" in captured_payload

    def test_cost_usd_serialized_as_string(self):
        """cost_usd must be serialized as str(cost_usd) to preserve Decimal precision."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_payload = {}

        def capture_post(url, json=None, timeout=None):
            captured_payload.update(json or {})
            return mock_response

        cost = Decimal("0.00314159")
        with patch("dashboard_client.requests.post", side_effect=capture_post):
            dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=200,
                output_tokens=100,
                cost_usd=cost,
                naive_cost_usd=Decimal("0.02718281"),
                request_label="03_cache.py",
            )

        assert isinstance(captured_payload["cost_usd"], str)
        assert captured_payload["cost_usd"] == str(cost)

    def test_naive_cost_usd_serialized_as_string(self):
        """naive_cost_usd must be serialized as str(naive_cost_usd) to preserve Decimal precision."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_payload = {}

        def capture_post(url, json=None, timeout=None):
            captured_payload.update(json or {})
            return mock_response

        naive = Decimal("0.02718281")
        with patch("dashboard_client.requests.post", side_effect=capture_post):
            dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=200,
                output_tokens=100,
                cost_usd=Decimal("0.00314159"),
                naive_cost_usd=naive,
                request_label="03_cache.py",
            )

        assert isinstance(captured_payload["naive_cost_usd"], str)
        assert captured_payload["naive_cost_usd"] == str(naive)

    @pytest.mark.parametrize(
        "naive",
        [
            Decimal("0"),
            Decimal("0.00002"),
            Decimal("0.0075"),
            Decimal("0.030000"),
            Decimal("1.23456789"),
            Decimal("999.99"),
        ],
    )
    def test_naive_cost_usd_string_round_trips_for_representative_decimals(self, naive):
        """For representative Decimal baselines, the serialized string must equal str(naive)
        and re-parse to the same Decimal without loss."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_payload = {}

        def capture_post(url, json=None, timeout=None):
            captured_payload.update(json or {})
            return mock_response

        with patch("dashboard_client.requests.post", side_effect=capture_post):
            dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.0001"),
                naive_cost_usd=naive,
                request_label="01_naive.py",
            )

        assert captured_payload["naive_cost_usd"] == str(naive)
        assert Decimal(captured_payload["naive_cost_usd"]) == naive

    def test_payload_field_types(self):
        """Verify each field in the payload has the correct type."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        captured_payload = {}

        def capture_post(url, json=None, timeout=None):
            captured_payload.update(json or {})
            return mock_response

        with patch("dashboard_client.requests.post", side_effect=capture_post):
            dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=750,
                output_tokens=250,
                cost_usd=Decimal("0.0005"),
                naive_cost_usd=Decimal("0.0125"),
                request_label="04_budget.py",
            )

        assert isinstance(captured_payload["model_name"], str)
        assert isinstance(captured_payload["input_tokens"], int)
        assert isinstance(captured_payload["output_tokens"], int)
        assert isinstance(captured_payload["cost_usd"], str)
        assert isinstance(captured_payload["naive_cost_usd"], str)
        assert isinstance(captured_payload["request_label"], str)

    def test_timeout_is_1_second(self):
        """POST request must use timeout=1.0 to avoid blocking the demo."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("dashboard_client.requests.post", return_value=mock_response) as mock_post:
            dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.0001"),
                naive_cost_usd=Decimal("0.005"),
                request_label="01_naive.py",
            )

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs.get("timeout") == 1.0


class TestReportCostDashboardUnreachable:
    """report_cost() must never raise even when dashboard is unreachable."""

    def test_returns_normally_on_connection_refused(self, capsys):
        import requests as req
        with patch(
            "dashboard_client.requests.post",
            side_effect=req.exceptions.ConnectionError("Connection refused"),
        ):
            result = dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.0001"),
                naive_cost_usd=Decimal("0.005"),
                request_label="01_naive.py",
            )

        assert result is None

    def test_prints_warning_on_connection_refused(self, capsys):
        import requests as req
        with patch(
            "dashboard_client.requests.post",
            side_effect=req.exceptions.ConnectionError("Connection refused"),
        ):
            dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.0001"),
                naive_cost_usd=Decimal("0.005"),
                request_label="01_naive.py",
            )

        captured = capsys.readouterr()
        assert "warning" in captured.out.lower() or "dashboard" in captured.out.lower()

    def test_returns_normally_on_timeout(self):
        import requests as req
        with patch(
            "dashboard_client.requests.post",
            side_effect=req.exceptions.Timeout("Request timed out"),
        ):
            result = dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=100,
                output_tokens=50,
                cost_usd=Decimal("0.0001"),
                naive_cost_usd=Decimal("0.005"),
                request_label="01_naive.py",
            )

        assert result is None

    def test_returns_normally_on_500_response(self):
        """Dashboard returning HTTP 500 should not raise."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("dashboard_client.requests.post", return_value=mock_response):
            result = dashboard_client.report_cost(
                model_name="anthropic.claude-opus-4-8",
                input_tokens=1000,
                output_tokens=400,
                cost_usd=Decimal("0.009"),
                naive_cost_usd=Decimal("0.036"),
                request_label="05_pipeline.py",
            )

        assert result is None

    def test_returns_normally_on_generic_exception(self):
        """Any exception from requests.post must be swallowed."""
        with patch(
            "dashboard_client.requests.post",
            side_effect=Exception("Unexpected error"),
        ):
            result = dashboard_client.report_cost(
                model_name="amazon.nova-lite-v1:0",
                input_tokens=50,
                output_tokens=25,
                cost_usd=Decimal("0.00003"),
                naive_cost_usd=Decimal("0.0015"),
                request_label="02_router.py",
            )

        assert result is None


class TestResetDashboard:

    def test_reset_posts_to_reset_endpoint(self):
        """reset_dashboard() must POST to /api/reset."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("dashboard_client.requests.post", return_value=mock_response) as mock_post:
            dashboard_client.reset_dashboard()

        call_args = mock_post.call_args
        assert "/api/reset" in call_args[0][0]

    def test_reset_returns_normally_when_unreachable(self):
        """reset_dashboard() must never raise."""
        import requests as req
        with patch(
            "dashboard_client.requests.post",
            side_effect=req.exceptions.ConnectionError("refused"),
        ):
            result = dashboard_client.reset_dashboard()
        assert result is None
