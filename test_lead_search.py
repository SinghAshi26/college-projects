import unittest
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from lead_finder import PlacesSearchError, search


def response(status, payload):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.post = self.enterContext(patch("lead_finder.requests.post"))
        self.usage = self.enterContext(patch("lead_finder.get_monthly_usage", return_value=(0, True)))
        self.record = self.enterContext(patch("lead_finder.record_api_call"))
        self.enterContext(patch("lead_finder.time.sleep"))

    def test_successful_empty_search(self):
        self.post.return_value = response(200, {})
        self.assertEqual(search("clinics in Noida", "key", 3), [])
        self.record.assert_called_once_with(1)

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
        self.usage.return_value = (900, True)
        with self.assertRaisesRegex(PlacesSearchError, "safety limit"):
            search("clinics in Noida", "key", 3, usage_limit=900)
        self.post.assert_not_called()

    def test_limit_between_pages_preserves_results(self):
        self.usage.side_effect = [(899, True), (900, True)]
        self.post.return_value = response(200, {"places": [{"id": "one"}], "nextPageToken": "next"})
        with self.assertRaises(PlacesSearchError) as caught:
            search("clinics in Noida", "key", 3, usage_limit=900)
        self.assertEqual(caught.exception.partial_results, [{"id": "one"}])
        self.post.assert_called_once()

    def test_blank_key_is_rejected_without_request(self):
        with self.assertRaisesRegex(PlacesSearchError, "key is missing"):
            search("clinics in Noida", " \n", 1)
        self.post.assert_not_called()


class SearchUITests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("lead_finder.get_monthly_usage", return_value=(0, True)))
        self.search = self.enterContext(patch("lead_finder.search"))
        self.app = AppTest.from_file("app.py", default_timeout=15)
        self.app.secrets["GOOGLE_API_KEY"] = "test-key"
        self.app.secrets["APP_PASSWORD"] = ""
        self.app.run()
        for widget in self.app.text_input:
            if widget.label == "Business Category":
                widget.set_value("clinic")
            elif widget.label == "City or Neighborhood":
                widget.set_value("Noida")

    def run_search(self):
        next(button for button in self.app.button if button.label == "Search Leads").click().run()
        self.assertEqual(len(self.app.exception), 0)

    def test_error_is_shown_instead_of_empty_results_warning(self):
        self.search.side_effect = PlacesSearchError("Google rejected the key: enable Places API (New).")
        self.run_search()
        self.assertTrue(any("enable Places API (New)" in error.value for error in self.app.error))
        self.assertFalse(any("no matching" in warning.value for warning in self.app.warning))

    def test_empty_results_show_query_guidance(self):
        self.search.return_value = []
        self.run_search()
        self.assertTrue(any("no matching listings" in warning.value for warning in self.app.warning))
        self.assertEqual(len(self.app.error), 0)

    def test_partial_results_and_warning_survive_rerun(self):
        self.search.side_effect = PlacesSearchError("Quota exceeded", [{"id": "one", "displayName": {"text": "Clinic"}}])
        self.run_search()
        self.assertEqual(len(self.app.session_state["leads"]), 1)
        self.assertTrue(any("Search was incomplete" in warning.value for warning in self.app.warning))


if __name__ == "__main__":
    unittest.main()
