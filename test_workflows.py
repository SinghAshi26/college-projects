import io
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
import requests

from http_tracking import request_tracking, tracked_http
from lead_model import csv_export, dashboard_metrics, lead_frame
from storage import StorageError
from templates import TEMPLATES, EMAIL_TEMPLATES, api_benefit, format_email, format_message
from website_audit import audit, audit_batch, get, psi_metrics


def http_response(status=200, body=b"", headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = body
    result.headers.update(headers or {})
    result.raw = io.BytesIO(body)
    return result


class HTTPTrackingTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.reserve_request.return_value = "reserved"

    def test_redirect_hops_count_as_separate_requests(self):
        responses = [http_response(302, headers={"Location":"https://public.example/final"}),http_response(200,b"done")]
        def send(adapter, request, **kwargs):
            response=responses.pop(0)
            response.request=request
            response.url=request.url
            return response
        with request_tracking(self.store), patch("requests.adapters.HTTPAdapter.send",new=send):
            result=tracked_http("GET","https://public.example/start",timeout=1)
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.store.reserve_request.call_count,2)
        self.assertEqual(self.store.complete_request.call_count,2)

    def test_bounded_website_retry_counts_each_attempt(self):
        with request_tracking(self.store), patch("requests.adapters.HTTPAdapter.send",side_effect=[requests.Timeout(),http_response(200)]), patch("website_audit.time.sleep"):
            get("https://public.example",retries=1)
        self.assertEqual([call.kwargs["attempt"] for call in self.store.reserve_request.call_args_list],[1,2])
        self.assertEqual(self.store.complete_request.call_args_list[0].args[1],"timeout")

    def test_database_failure_is_not_retried_as_website_failure(self):
        self.store.reserve_request.side_effect=StorageError("Database unavailable")
        with request_tracking(self.store), patch("requests.adapters.HTTPAdapter.send") as send:
            with self.assertRaises(StorageError):
                get("https://public.example",retries=1)
        send.assert_not_called()
        self.store.reserve_request.assert_called_once()

    def test_pagespeed_result_and_safe_metadata(self):
        body=b'{"lighthouseResult":{"categories":{"performance":{"score":0.75}},"audits":{"largest-contentful-paint":{"numericValue":2000},"cumulative-layout-shift":{"numericValue":0.1}}}}'
        with request_tracking(self.store), patch("requests.adapters.HTTPAdapter.send",return_value=http_response(200,body)):
            result=psi_metrics("https://public.example","secret-api-key")
        self.assertEqual(result,{"score":75,"lcp_sec":"2.0s","cls":"0.1"})
        self.assertEqual(self.store.reserve_request.call_args.args,("google_pagespeed","performance"))
        self.assertNotIn("secret-api-key",str(self.store.mock_calls))


class WorkflowTests(unittest.TestCase):
    def test_all_bilingual_templates_format(self):
        for language, templates in TEMPLATES.items():
            for template in templates.values():
                result=format_message(template,"Clinic","Alex","Slow site",api_benefit("clinic",language))
                self.assertIn("Clinic",result)
                self.assertNotIn("{sender}",result)
        for language, templates in EMAIL_TEMPLATES.items():
            for template_name in templates:
                subject,body=format_email(language,template_name,"Clinic","Alex",site="clinic.test",problem="Slow site",benefit=api_benefit("clinic",language))
                self.assertTrue(subject)
                self.assertIn("Alex",body)

    def test_website_audit_extracts_emails_and_findings(self):
        response=http_response(200,b'<html><title>Clinic</title><h1>Clinic</h1><meta name="viewport" content="width=device-width"><meta name="description" content="Clinic"><a href="mailto:hello@clinic.test">Email</a><a href="https://instagram.com/clinic">Social</a></html>')
        response.url="https://clinic.test"
        with patch("website_audit.get",return_value=(response,0.2)),patch("website_audit.check_ssl_expiry",return_value=(100,"valid")),patch("website_audit.check_broken_links",return_value=0):
            result=audit("https://clinic.test")
        self.assertEqual(result["found_email"],"hello@clinic.test")
        self.assertEqual(result["instagram"],"https://instagram.com/clinic")
        self.assertIn("NO_WHATSAPP",[f["code"] for f in result["findings"]])

    def test_audit_cache_avoids_network(self):
        with patch("website_audit.audit") as worker:
            result=audit_batch([{"website":"https://clinic.test","email":""}],cache={"clinic.test":{"found_email":"hello@clinic.test","audit_score":90}})
        worker.assert_not_called()
        self.assertEqual(result[0]["email"],"hello@clinic.test")

    def test_metrics_and_csv_keep_existing_fields(self):
        frame=lead_frame([{"name":"A, B","lead_type":"HOT - no website","phone":"123","email":"a@b.test","status":"Contacted","_id":"internal","findings":[{"code":"NO_WEBSITE"}]}])
        self.assertEqual(dashboard_metrics(frame),dict(total=1,hot=1,with_phone=1,with_email=1,contacted=1))
        content=csv_export(frame).decode()
        self.assertIn('"A, B"',content)
        self.assertNotIn("internal",content)
        self.assertIn("findings",content)


if __name__ == "__main__":
    unittest.main()
