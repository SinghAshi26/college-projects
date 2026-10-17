import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from lead_finder import PlacesSearchError, search
from storage import QuotaReached


def response(status, payload):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.post = self.enterContext(patch("lead_finder.requests.post"))
        self.store = Mock()
        self.store.start_search.return_value = True
        self.store.reserve_request.return_value = "request-id"
        self.enterContext(patch("lead_finder.configured_store", return_value=self.store))
        self.record = self.store.complete_request
        self.enterContext(patch("lead_finder.time.sleep"))

    def test_successful_empty_search(self):
        self.post.return_value = response(200, {})
        self.assertEqual(search("clinics in Noida", "key", 3), [])
        self.record.assert_called_once_with(request_id="request-id", outcome="success", http_status=200, leads=[])

    def test_pagination_and_trimmed_key(self):
        self.post.side_effect = [
            response(200, {"places": [{"id": "one"}], "nextPageToken": "next"}),
            response(200, {"places": [{"id": "two"}]}),
        ]
        self.assertEqual(search("clinics in Noida", " key \n", 3), [{"id": "one"}, {"id": "two"}])
        self.assertEqual(self.post.call_args.kwargs["headers"]["X-Goog-Api-Key"], "key")
        self.assertEqual(self.post.call_args.kwargs["json"]["pageToken"], "next")
        self.assertEqual(self.record.call_count, 2)

    def test_google_errors_are_actionable(self):
        cases = [
            (403, "BILLING_DISABLED", "Enable billing"),
            (403, "SERVICE_DISABLED", "Enable Places API (New)"),
            (400, "API_KEY_INVALID", "valid Google Cloud API key"),
            (403, "API_KEY_HTTP_REFERRER_BLOCKED", "app server's public IP"),
            (429, "RESOURCE_EXHAUSTED", "quota"),
            (503, "UNAVAILABLE", "temporarily unavailable"),
        ]
        for status, reason, hint in cases:
            with self.subTest(reason=reason):
                self.post.return_value = response(status, {"error": {
                    "status": reason, "message": "Request rejected", "details": [{"reason": reason}]
                }})
                with self.assertRaises(PlacesSearchError) as caught:
                    search("clinics in Noida", "secret-key", 1)
                self.assertIn(hint, str(caught.exception))
                self.assertIn(f"HTTP {status}", str(caught.exception))
                self.assertEqual(caught.exception.partial_results, [])

    def test_api_key_redacted_from_google_message(self):
        self.post.return_value = response(400, {"error": {"message": "Invalid API key secret-key"}})
        with self.assertRaises(PlacesSearchError) as caught:
            search("clinics in Noida", "secret-key", 1)
        self.assertNotIn("secret-key", str(caught.exception))

    def test_later_page_failure_preserves_results(self):
        self.post.side_effect = [
            response(200, {"places": [{"id": "one"}], "nextPageToken": "next"}),
            response(403, {"error": {"message": "Billing disabled"}}),
        ]
        with self.assertRaises(PlacesSearchError) as caught:
            search("clinics in Noida", "key", 3)
        self.assertEqual(caught.exception.partial_results, [{"id": "one"}])

    def test_network_failures(self):
        for failure, message in [(requests.Timeout(), "timed out"), (requests.ConnectionError(), "Could not connect")]:
            with self.subTest(failure=type(failure).__name__):
                self.post.side_effect = failure
                with self.assertRaisesRegex(PlacesSearchError, message):
                    search("clinics in Noida", "key", 1)

    def test_invalid_json_and_non_json_http_error(self):
        for status in (200, 502):
            with self.subTest(status=status):
                self.post.return_value = response(status, None)
                self.post.return_value.json.side_effect = ValueError("not JSON")
                with self.assertRaises(PlacesSearchError):
                    search("clinics in Noida", "key", 1)

    def test_limit_blocks_requests(self):
        self.store.reserve_request.side_effect = QuotaReached("Monthly safety limit reached")
        with self.assertRaisesRegex(PlacesSearchError, "safety limit"):
            search("clinics in Noida", "key", 3, usage_limit=900)
        self.post.assert_not_called()

    def test_limit_between_pages_preserves_results(self):
        self.store.reserve_request.side_effect = ["request-id", QuotaReached("Monthly safety limit reached")]
        self.post.return_value = response(200, {"places": [{"id": "one"}], "nextPageToken": "next"})
        with self.assertRaises(PlacesSearchError) as caught:
            search("clinics in Noida", "key", 3, usage_limit=900)
        self.assertEqual(caught.exception.partial_results, [{"id": "one"}])
        self.post.assert_called_once()

    def test_blank_key_is_rejected_without_request(self):
        with self.assertRaisesRegex(PlacesSearchError, "key is missing"):
            search("clinics in Noida", " \n", 1)
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
