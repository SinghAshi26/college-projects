"""Regression coverage with Streamlit magic enabled: no accidental object rendering."""
import ast
import datetime
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import psycopg
from streamlit.testing.v1 import AppTest

from storage import DatabaseConfig, DatabaseNotConfigured, MigrationRequired, StorageError, Store


def rendered_sidebar(app):
    # Inspect actual protobuf output, including help_info fields that have no .value.
    return "\n".join(str(node.proto) for node in app.sidebar if getattr(node, "proto", None) is not None)


class SidebarTests(unittest.TestCase):
    def app(self, factory_error=None, check_error=None):
        store = Mock()
        store.settings.return_value = {"daily_limit":20,"monthly_limit":900,"preferences":{},
                                       "tracking_started_at":datetime.datetime.now(datetime.timezone.utc)}
        store.load_leads.return_value = []
        store.usage.return_value = {period:{"request_count":0,"pending_count":0} for period in ("day","month")}
        store.recent_searches.return_value = []
        store.check_connection.side_effect = check_error
        with patch("configuration.configured_store",return_value=store,side_effect=factory_error):
            app = AppTest.from_file("app.py",default_timeout=20)
            app.secrets.update(APP_PASSWORD="test-only",SUPABASE_DB_URL="configured",GOOGLE_API_KEY="",PSI_API_KEY="")
            app.session_state["authed"] = True
            app.run()
        self.assertFalse(app.exception)
        return app, store

    def assert_clean(self, app):
        output=rendered_sidebar(app)
        for marker in ("DeltaGenerator","LockedContainer","st.success(","st.warning(","Traceback", "postgresql://", "secret-value"):
            self.assertNotIn(marker,output)
        for element_type in ("help_info","code","exception","json"):
            self.assertEqual(len(app.sidebar.get(element_type)),0)

    def test_all_connection_states_without_debug_output(self):
        cases=[(None,"Connected"), (DatabaseNotConfigured("secret-value"),"Not configured"),
               (StorageError("postgresql://user:secret-value@host/db"),"Connection failed"),
               (MigrationRequired("SELECT * FROM private_table; secret-value"),"Migration required")]
        for error, label in cases:
            with self.subTest(label=label):
                app,store=self.app(factory_error=error)
                self.assert_clean(app)
                statuses=[node.value for kind in ("success","info","warning") for node in app.sidebar.get(kind)]
                self.assertIn(label,statuses)
                self.assertEqual("Connected" in statuses,error is None)
                if error is None:
                    store.check_connection.assert_called_once()
                self.assertEqual([tab.label for tab in app.tabs],["Dashboard","Find Leads","Leads","Outreach","Email","Activity"])

    def test_failed_probe_cannot_show_connected_or_leak_driver_error(self):
        app,_=self.app(check_error=RuntimeError("password=secret-value SQL error LockedContainer"))
        self.assert_clean(app)
        self.assertFalse(app.sidebar.success)
        self.assertIn("Connection failed",[w.value for w in app.sidebar.warning])
        self.assertTrue(next(b for b in app.button if b.label=="Search Leads").disabled)

    def test_missing_migration_after_connect_is_distinguished(self):
        app,_=self.app(check_error=MigrationRequired("secret-value"))
        self.assert_clean(app)
        self.assertIn("Migration required",[w.value for w in app.sidebar.warning])

    def test_no_standalone_conditional_ui_calls(self):
        tree=ast.parse(Path("app.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.IfExp):
                self.fail(f"Standalone conditional expression can trigger magic rendering at line {node.lineno}")


class ConnectionClassificationTests(unittest.TestCase):
    def test_sql_errors_are_classified_without_raw_diagnostics(self):
        store=Store(DatabaseConfig("postgresql://test@127.0.0.1/appnix_test?sslmode=disable",allow_local_test=True))
        for error in (psycopg.errors.UndefinedTable("secret-value"),psycopg.errors.UndefinedColumn("secret-value"),psycopg.errors.InvalidSchemaName("secret-value")):
            with self.subTest(error=type(error).__name__), patch("storage.psycopg.connect",side_effect=error):
                with self.assertLogs("appnix") as logs:
                    with self.assertRaises(MigrationRequired) as caught:
                        store.check_connection()
                self.assertNotIn("secret-value",str(caught.exception)+str(logs.output))
                self.assertIn("migration_required",str(logs.output))

    def test_connection_error_is_not_misclassified_as_migration(self):
        store=Store(DatabaseConfig("postgresql://test@127.0.0.1/appnix_test?sslmode=disable",allow_local_test=True))
        with patch("storage.psycopg.connect",side_effect=psycopg.OperationalError("secret-value")):
            with self.assertRaises(StorageError) as caught:
                store.check_connection()
        self.assertNotIsInstance(caught.exception,MigrationRequired)
        self.assertNotIn("secret-value",str(caught.exception))


if __name__ == "__main__":
    unittest.main()
