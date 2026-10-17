"""Streamlit workflow regressions with isolated PostgreSQL and mocked external services."""
import datetime
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from lead_finder import place_to_lead
from lead_model import csv_export, dashboard_metrics, lead_frame
from storage import DatabaseConfig, DatabaseNotConfigured, StorageError, Store


class MissingConfigUITests(unittest.TestCase):
    def test_startup_without_configuration(self):
        with patch("configuration.configured_store", side_effect=DatabaseNotConfigured("Database not configured")):
            app = AppTest.from_file("app.py", default_timeout=20)
            app.secrets["APP_PASSWORD"] = ""
            app.secrets["SUPABASE_DB_URL"] = ""
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual([t.label for t in app.tabs], ["Dashboard","Find Leads","Leads","Outreach","Email","Activity"])
            self.assertTrue(next(b for b in app.button if b.label == "Search Leads").disabled)
            self.assertTrue(any("Not configured" in info.value for info in app.sidebar.info))

    def test_database_requires_password_gate(self):
        app = AppTest.from_file("app.py", default_timeout=20)
        app.secrets["APP_PASSWORD"] = ""
        app.secrets["SUPABASE_DB_URL"] = "configured-but-never-connected"
        app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Set APP_PASSWORD" in e.value for e in app.error))


@unittest.skipUnless(os.environ.get("APPNIX_TEST_DB_URL"), "Requires isolated local PostgreSQL")
class PersistentUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(DatabaseConfig(os.environ["APPNIX_TEST_DB_URL"], allow_local_test=True))
        if cls.store._options["host"] not in ("127.0.0.1","localhost") or not cls.store._options["dbname"].startswith("appnix_test"):
            raise RuntimeError("Refusing non-test database")
        with cls.store.transaction() as conn:
            conn.execute(Path("migrations/001_persistence.sql").read_text())

    def setUp(self):
        with self.store.transaction() as conn:
            conn.execute("TRUNCATE appnix.api_usage_logs,appnix.search_history,appnix.leads,appnix.app_settings,appnix.usage_counters")
        self.store.initialize(20,900)
        self.factory = self.enterContext(patch("configuration.configured_store", return_value=self.store))
        self.post = self.enterContext(patch("lead_finder.requests.post"))
        self.enterContext(patch("lead_finder.time.sleep"))

    def app(self):
        # Multi-page workflows open several real local PostgreSQL connections;
        # allow Windows CI startup overhead without changing production timeouts.
        app = AppTest.from_file("app.py",default_timeout=60)
        app.secrets.update(APP_PASSWORD="test-password", SUPABASE_DB_URL="test-only", GOOGLE_API_KEY="fake-key", PSI_API_KEY="")
        app.session_state["authed"] = True
        app.run()
        self.assertFalse(app.exception)
        return app

    def seed(self):
        lead = place_to_lead({"id":"one","displayName":{"text":"Clinic"},"nationalPhoneNumber":"9876543210"},"clinic","Noida")
        lead["email"] = "hello@clinic.test"
        self.store.save_new_leads([lead])

    def submit(self, app):
        for widget in app.text_input:
            if widget.label == "Business Category": widget.set_value("clinic")
            if widget.label == "City or Neighborhood": widget.set_value("Noida")
        next(b for b in app.button if b.label == "Search Leads").click().run()
        self.assertFalse(app.exception)

    def test_search_persistence_reload_metrics_and_csv(self):
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {"places":[{"id":"one","displayName":{"text":"Clinic"},"nationalPhoneNumber":"9876543210"}]}
        app = self.app()
        self.submit(app)
        self.assertEqual(self.post.call_count,1)
        self.assertTrue(any("Retrieved 1 leads" in s.value for s in app.success))
        fresh = self.app()
        frame = fresh.session_state["leads"]
        self.assertEqual(len(frame),1)
        self.assertEqual(dashboard_metrics(frame),dict(total=1,hot=1,with_phone=1,with_email=0,contacted=0))
        exported = csv_export(frame).decode()
        self.assertIn("Clinic",exported)
        self.assertNotIn("_id",exported.splitlines()[0].split(","))
        self.assertTrue(any("Download Leads CSV" in b.label for b in fresh.get("download_button")))
        fresh.run()
        self.assertEqual(self.post.call_count,1)

    def test_provider_failure_and_empty_results_are_distinct(self):
        for status,payload,expected in [(400,{"error":{"status":"INVALID_ARGUMENT","message":"API key not valid"}},"valid Google Cloud API key"),(200,{},"No matching listings")]:
            with self.subTest(status=status):
                self.post.return_value = Mock(status_code=status)
                self.post.return_value.json.return_value = payload
                app=self.app()
                self.submit(app)
                self.assertTrue(any(expected in w.value for w in app.warning))

    def test_partial_results_preserved(self):
        first = Mock(status_code=200)
        first.json.return_value = {"places":[{"id":"one","displayName":{"text":"Clinic"}}],"nextPageToken":"next"}
        second = Mock(status_code=429)
        second.json.return_value = {"error":{"status":"RESOURCE_EXHAUSTED","message":"Quota exceeded"}}
        self.post.side_effect=[first,second]
        app=self.app()
        self.submit(app)
        self.assertEqual(len(app.session_state["leads"]),1)
        self.assertTrue(any("Search was incomplete" in w.value for w in app.warning))

    def test_whatsapp_and_email_contact_status_persist(self):
        self.seed()
        app=self.app()
        self.assertTrue(any("wa.me" in str(b.url) for b in app.get("link_button")))
        self.assertTrue(any("mail.google.com" in str(b.url) for b in app.get("link_button")))
        next(b for b in app.button if b.label=="Mark as Contacted Today").click().run()
        self.assertFalse(app.exception)
        row=self.store.load_leads()[0]
        self.assertEqual(row["status"],"Contacted")
        self.assertIn("WhatsApp",row["outreach_log"])
        email_button=next(b for b in app.button if b.key and b.key.startswith("btn_contact_email_"))
        email_button.click().run()
        self.assertFalse(app.exception)
        self.assertIn("Email",self.store.load_leads()[0]["outreach_log"])
        self.assertEqual(dashboard_metrics(self.app().session_state["leads"])["contacted"],1)

    def test_sender_settings_restore_and_template_update(self):
        self.seed()
        app=self.app()
        app.text_input(key="sender_name").set_value("Alex").run()
        self.assertFalse(app.exception)
        self.assertEqual(self.store.settings()["preferences"]["sender_name"],"Alex")
        fresh=self.app()
        self.assertEqual(fresh.text_input(key="sender_name").value,"Alex")
        self.assertTrue(any("Alex" in area.value for area in fresh.text_area))

    def test_audit_results_persist(self):
        self.seed()
        # No website: exercises real audit_batch workers without external network calls.
        app=self.app()
        next(b for b in app.button if b.label=="Audit Websites & Find Emails").click().run()
        self.assertFalse(app.exception)
        saved=self.store.load_leads()[0]
        self.assertEqual(saved["findings"][0]["code"],"NO_WEBSITE")
        self.assertEqual(self.app().session_state["leads"].iloc[0]["problem"],"No website found")

    def test_database_outage_keeps_snapshot_and_blocks_search(self):
        self.seed()
        app=self.app()
        self.factory.side_effect=StorageError("Database unavailable")
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state["leads"]),1)
        self.assertTrue(next(b for b in app.button if b.label=="Search Leads").disabled)
        self.assertTrue(any("last saved snapshot" in c.value for c in app.caption))

    def test_failed_edit_can_be_retried_without_false_success(self):
        self.seed()
        app=self.app()
        with patch.object(self.store,"patch_leads",side_effect=StorageError("Write failed")):
            next(b for b in app.button if b.label=="Mark as Contacted Today").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.store.load_leads()[0]["status"],"New")
        self.assertTrue(app.session_state["pending_changes"])
        app.run()
        next(b for b in app.button if b.label=="Retry unsaved changes").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(self.store.load_leads()[0]["status"],"Contacted")

    def test_inline_status_edit_persists(self):
        self.seed()
        app=self.app()
        app.session_state["lead_editor"]={"edited_rows":{0:{"status":"Meeting"}},"added_rows":[],"deleted_rows":[]}
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(self.store.load_leads()[0]["status"],"Meeting")


if __name__ == "__main__":
    unittest.main()
