"""Integration tests run ONLY against a dedicated loopback appnix_test* database."""
from concurrent.futures import ThreadPoolExecutor
import json
import datetime
import logging
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

import psycopg
import requests

from configuration import configured_store
from lead_finder import PlacesSearchError, search
from safe_logging import safe_event
from storage import DatabaseConfig, EditConflict, QuotaReached, StorageError, Store


class ConfigurationTests(unittest.TestCase):
    def test_missing_configuration(self):
        with self.assertRaisesRegex(StorageError, "not configured"):
            configured_store(lambda key, default="": default)

    def test_invalid_connection_does_not_leak(self):
        secret = "postgresql://user:super-secret@host/db?sslmode=disable"
        with self.assertRaises(StorageError) as caught:
            DatabaseConfig(secret).options()
        self.assertNotIn("super-secret", str(caught.exception))

    def test_remote_tls_cannot_be_disabled_in_test_mode(self):
        with self.assertRaisesRegex(StorageError, "TLS"):
            DatabaseConfig("postgresql://user:secret@remote/appnix_test?sslmode=disable", allow_local_test=True).options()

    def test_safe_logging_discards_credentials_and_exception_text(self):
        with self.assertLogs("appnix", logging.INFO) as captured:
            safe_event("request_completed", provider="google_places", outcome="success", api_key="TOP_SECRET",
                       headers={"Authorization":"TOP_SECRET"}, error_category="postgresql://user:TOP_SECRET@host/db")
        self.assertNotIn("TOP_SECRET", str(captured.output))
        self.assertIn("google_places", str(captured.output))


@unittest.skipUnless(os.environ.get("APPNIX_TEST_DB_URL"), "Set APPNIX_TEST_DB_URL to an isolated local PostgreSQL database")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = DatabaseConfig(os.environ["APPNIX_TEST_DB_URL"], allow_local_test=True)
        options = config.options()
        if options["host"] not in ("127.0.0.1", "localhost") or not options["dbname"].startswith("appnix_test"):
            raise RuntimeError("Tests refuse non-local or non-test databases")
        cls.store = Store(config)
        with cls.store.transaction() as conn:
            for _ in range(2):
                conn.execute(Path("migrations/001_persistence.sql").read_text())

    def setUp(self):
        with self.store.transaction() as conn:
            conn.execute("TRUNCATE appnix.api_usage_logs,appnix.search_history,appnix.leads,appnix.app_settings,appnix.usage_counters")
        self.store.initialize(50,900)

    def test_create_reload_duplicate_and_patch_preserve_fields(self):
        lead = {"place_id":"one","name":"Clinic","status":"Contacted","outreach_log":"history", "findings":[{"code":"NO_HTTPS"}],"custom_future_field":42}
        self.store.save_new_leads([lead])
        self.store.save_new_leads([dict(lead, status="New")])
        reload = Store(self.store.config).load_leads()
        self.assertEqual(len(reload),1)
        self.assertEqual(reload[0]["status"],"Contacted")
        self.store.patch_leads([(reload[0]["_id"],reload[0],{"email":"hello@clinic.test"})])
        saved = self.store.load_leads()[0]
        self.assertEqual(saved["custom_future_field"],42)
        self.assertEqual(saved["findings"],lead["findings"])
        self.assertEqual(saved["email"],"hello@clinic.test")

    def test_missing_source_ids_do_not_collapse_different_leads(self):
        self.store.save_new_leads([{"name":"One"},{"name":"Two"},{"name":"One"}])
        self.assertEqual(len(self.store.load_leads()),2)

    def test_concurrent_field_conflict_is_visible(self):
        self.store.save_new_leads([{"name":"One","status":"New"}])
        before = self.store.load_leads()[0]
        self.store.patch_leads([(before["_id"],before,{"status":"Contacted"})])
        with self.assertRaises(EditConflict):
            self.store.patch_leads([(before["_id"],before,{"status":"Closed"})])
        self.store.patch_leads([(before["_id"],before,{"email":"hello@one.test"})])
        self.assertEqual(self.store.load_leads()[0]["status"],"Contacted")

    def test_settings_allowlist_and_reload(self):
        self.store.save_settings({"sender_name":"Saved"},daily=7,monthly=20)
        self.store.initialize(999,999)
        self.assertEqual(self.store.settings()["daily_limit"],7)
        self.assertEqual(self.store.settings()["preferences"]["sender_name"],"Saved")
        with self.assertRaises(StorageError):
            self.store.save_settings({"GOOGLE_API_KEY":"secret"})

    def test_concurrent_daily_and_monthly_quota(self):
        for daily, monthly in ((5,50),(50,7)):
            with self.subTest(daily=daily,monthly=monthly):
                self.setUp()
                self.store.save_settings(daily=daily,monthly=monthly)
                def reserve(_):
                    try:
                        return self.store.reserve_request("google_places","text_search")
                    except QuotaReached:
                        return None
                with ThreadPoolExecutor(max_workers=12) as executor:
                    admitted = [x for x in executor.map(reserve,range(24)) if x]
                self.assertEqual(len(admitted),min(daily,monthly))
                usage = self.store.usage()
                for period in ("day","month"):
                    self.assertEqual(usage[period]["pending_count"],len(admitted))
                    self.assertEqual(usage[period]["request_count"],0)

    def test_completion_is_idempotent_and_uses_reservation_period(self):
        request_id = self.store.reserve_request("google_places","text_search")
        self.store.complete_request(request_id,"success",200)
        self.store.complete_request(request_id,"success",200)
        for counter in self.store.usage().values():
            self.assertEqual(counter["request_count"],1)
            self.assertEqual(counter["successful_requests"],1)
            self.assertEqual(counter["pending_count"],0)

    def test_completion_across_period_boundary(self):
        old_day=datetime.datetime(2020,1,31,tzinfo=datetime.timezone.utc)
        old_month=old_day.replace(day=1)
        end=datetime.datetime(2020,2,1,tzinfo=datetime.timezone.utc)
        with patch.object(self.store,"periods",return_value=[("day",old_day,end),("month",old_month,end)]):
            request_id=self.store.reserve_request("google_places","text_search")
        self.store.complete_request(request_id,"success",200)
        self.assertEqual(self.store.usage()["day"]["request_count"],0)
        with self.store.transaction() as conn:
            counters=conn.execute("SELECT * FROM appnix.usage_counters WHERE provider='google_places'").fetchall()
        self.assertTrue(all(row["request_count"]==1 and row["pending_count"]==0 for row in counters))

    def test_connection_refusal_is_safe(self):
        unavailable=Store(DatabaseConfig("postgresql://appnix_test:do-not-print@127.0.0.1:1/appnix_test_missing?sslmode=disable",allow_local_test=True))
        with self.assertRaises(StorageError) as caught:
            unavailable.load_leads()
        self.assertNotIn("do-not-print",str(caught.exception))
        self.assertIn("Database unavailable",str(caught.exception))

    def test_search_history_pages_and_actual_requests(self):
        first = Mock(status_code=200)
        first.json.return_value = {"places":[{"id":"one","displayName":{"text":"One"}}],"nextPageToken":"next"}
        second = Mock(status_code=200)
        second.json.return_value = {"places":[{"id":"two","displayName":{"text":"Two"}}]}
        search_id = str(uuid.uuid4())
        with patch("lead_finder.requests.post", side_effect=[first,second]) as post, patch("lead_finder.time.sleep"):
            results = search("clinic in Noida","fake-key",3,store=self.store,category="clinic",city="Noida",search_id=search_id)
            self.assertEqual(post.call_count,2)
            with self.assertRaisesRegex(PlacesSearchError,"already recorded"):
                search("clinic in Noida","fake-key",3,store=self.store,search_id=search_id)
            self.assertEqual(post.call_count,2)
        self.assertEqual(len(results),2)
        self.assertEqual(len(self.store.load_leads()),2)
        history = Store(self.store.config).recent_searches()[0]
        self.assertEqual((history["status"],history["requests"],history["leads_returned"]),("success",2,2))
        for counter in self.store.usage().values():
            self.assertEqual((counter["request_count"],counter["search_jobs"],counter["leads_returned"]),(2,1,2))

    def test_quota_before_outbound_call(self):
        self.store.save_settings(daily=0)
        with patch("lead_finder.requests.post") as post:
            with self.assertRaisesRegex(PlacesSearchError,"limit reached"):
                search("clinic","fake-key",1,store=self.store)
            post.assert_not_called()
        self.assertEqual(self.store.recent_searches()[0]["status"],"blocked")

    def test_failure_counted_without_provider_retries(self):
        with patch("lead_finder.requests.post",side_effect=requests.Timeout) as post:
            with self.assertRaisesRegex(PlacesSearchError,"timed out"):
                search("clinic","fake-key",3,store=self.store)
            post.assert_called_once()
        self.assertEqual(self.store.usage()["day"]["failed_requests"],1)
        self.assertEqual(self.store.recent_searches()[0]["status"],"failed")

    def test_database_failure_stops_requests(self):
        with patch.object(self.store,"reserve_request",side_effect=StorageError("Database unavailable")), patch("lead_finder.requests.post") as post:
            with self.assertRaisesRegex(PlacesSearchError,"Database unavailable"):
                search("clinic","fake-key",1,store=self.store)
            post.assert_not_called()

    def test_failed_completion_is_recoverable_without_new_http_call(self):
        response = Mock(status_code=200)
        response.json.return_value = {"places":[{"id":"one"}]}
        with patch.object(self.store,"complete_request",side_effect=StorageError("Database unavailable")), patch("lead_finder.requests.post",return_value=response) as post:
            with self.assertRaises(PlacesSearchError) as caught:
                search("clinic","fake-key",3,store=self.store)
            post.assert_called_once()
        self.assertEqual(len(caught.exception.partial_results),1)
        self.store.complete_request(**caught.exception.pending_completion)
        self.store.complete_request(**caught.exception.pending_completion)
        self.assertEqual(len(self.store.load_leads()),1)
        self.assertEqual(self.store.usage()["day"]["request_count"],1)

    def test_public_role_has_no_access(self):
        with self.store.transaction() as conn:
            conn.execute("SET LOCAL ROLE appnix_backend")
            self.assertIsNotNone(conn.execute("SELECT * FROM appnix.app_settings").fetchone())
        with self.store.transaction() as conn:
            tables = conn.execute("SELECT relrowsecurity FROM pg_class JOIN pg_namespace n ON n.oid=relnamespace WHERE n.nspname='appnix' AND relkind='r'").fetchall()
            self.assertTrue(all(r["relrowsecurity"] for r in tables))


if __name__ == "__main__":
    unittest.main()
