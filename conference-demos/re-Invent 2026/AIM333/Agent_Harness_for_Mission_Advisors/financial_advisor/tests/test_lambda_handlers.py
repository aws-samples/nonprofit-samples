# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Tests for the real, deployed Lambda handlers under lambda/.

These call lambda_handler(event, context) directly against the same code
that AgentCore Gateway invokes in AWS — not a parallel reimplementation.
`lambda` is a reserved word, so the handler modules are loaded by file path
with importlib rather than a normal package import.

No AWS credentials or network access required: these are plain Python
functions with no boto3 calls inside them.
"""

import importlib.util
import os
import types
import unittest

_LAMBDA_DIR = os.path.join(os.path.dirname(__file__), "..", "lambda")


def _load_handler_module(module_name: str, filename: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(_LAMBDA_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


member_lookup = _load_handler_module("member_lookup", "member_lookup.py")
rate_engine = _load_handler_module("rate_engine", "rate_engine.py")
advisor_library = _load_handler_module("advisor_library", "advisor_library.py")


def _gateway_context(target_name: str, tool_name: str) -> types.SimpleNamespace:
    """Build a fake Lambda context matching what AgentCore Gateway supplies:
    context.client_context.custom["bedrockAgentCoreToolName"] formatted as
    "{target_name}___{tool_name}" (see gateway-tool-naming docs).
    """
    custom = {"bedrockAgentCoreToolName": f"{target_name}___{tool_name}"}
    client_context = types.SimpleNamespace(custom=custom)
    return types.SimpleNamespace(client_context=client_context)


class MemberLookupHandlerTests(unittest.TestCase):
    def test_known_member_returns_full_record(self) -> None:
        context = _gateway_context("member-lookup", "member_lookup")
        result = member_lookup.lambda_handler({"member_id": "member-101"}, context)

        self.assertEqual(result["member_id"], "member-101")
        self.assertEqual(result["display_name"], "John Miller")
        self.assertIn("financial_goal", result)

    def test_second_known_member(self) -> None:
        context = _gateway_context("member-lookup", "member_lookup")
        result = member_lookup.lambda_handler({"member_id": "member-102"}, context)

        self.assertEqual(result["display_name"], "Jane Carter")

    def test_unknown_member_returns_error_not_invented_data(self) -> None:
        context = _gateway_context("member-lookup", "member_lookup")
        result = member_lookup.lambda_handler({"member_id": "member-999"}, context)

        self.assertIn("error", result)
        self.assertIn("available_member_ids", result)

    def test_unknown_tool_name_is_rejected(self) -> None:
        context = _gateway_context("member-lookup", "some_other_tool")
        result = member_lookup.lambda_handler({"member_id": "member-101"}, context)

        self.assertIn("error", result)


class RateEngineHandlerTests(unittest.TestCase):
    def test_retirement_purpose_returns_apy_product(self) -> None:
        context = _gateway_context("rate-engine", "rate_engine")
        result = rate_engine.lambda_handler({"purpose": "retirement-savings"}, context)

        self.assertIn("annual_percentage_yield", result)
        self.assertNotIn("annual_percentage_rate", result)

    def test_home_purchase_purpose_returns_apr_product(self) -> None:
        context = _gateway_context("rate-engine", "rate_engine")
        result = rate_engine.lambda_handler({"purpose": "home-purchase"}, context)

        self.assertIn("annual_percentage_rate", result)
        self.assertNotIn("annual_percentage_yield", result)

    def test_unknown_purpose_returns_error_not_invented_rate(self) -> None:
        context = _gateway_context("rate-engine", "rate_engine")
        result = rate_engine.lambda_handler({"purpose": "debt-consolidation"}, context)

        self.assertIn("error", result)
        self.assertIn("available_purposes", result)


class AdvisorLibraryHandlerTests(unittest.TestCase):
    def test_retirement_template_has_five_ordered_milestones(self) -> None:
        context = _gateway_context("advisor-library", "advisor_library")
        result = advisor_library.lambda_handler({"template_id": "retirement-readiness"}, context)

        self.assertEqual(len(result["milestones"]), 5)
        self.assertTrue(result["milestones"][0].startswith("Confirm current retirement"))

    def test_home_buying_template_has_five_ordered_milestones(self) -> None:
        context = _gateway_context("advisor-library", "advisor_library")
        result = advisor_library.lambda_handler({"template_id": "home-buying-readiness"}, context)

        self.assertEqual(len(result["milestones"]), 5)
        self.assertTrue(result["milestones"][0].startswith("Confirm current savings"))

    def test_unknown_template_returns_error_not_invented_milestones(self) -> None:
        context = _gateway_context("advisor-library", "advisor_library")
        result = advisor_library.lambda_handler({"template_id": "debt-payoff"}, context)

        self.assertIn("error", result)
        self.assertIn("available_template_ids", result)

    def test_returned_milestones_are_a_copy_not_the_shared_module_list(self) -> None:
        """Mutating one call's result must not affect the next call — the
        handler returns list(template["milestones"]), not the same list
        object, since AgentCore Gateway may reuse a warm Lambda execution
        environment across invocations."""
        context = _gateway_context("advisor-library", "advisor_library")
        first = advisor_library.lambda_handler({"template_id": "retirement-readiness"}, context)
        first["milestones"].append("mutated")

        second = advisor_library.lambda_handler({"template_id": "retirement-readiness"}, context)
        self.assertNotIn("mutated", second["milestones"])


if __name__ == "__main__":
    unittest.main()
