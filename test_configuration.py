"""Local configuration refresh, verified TLS and sanitized connection regressions."""
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psycopg
from streamlit.testing.v1 import AppTest

import configuration
import diagnose_db
from storage import DatabaseConfig, DatabaseConnectionError, DatabaseNotConfigured, MigrationRequired, Store
import test_sidebar


class LocalConfigurationTests(unittest.TestCase):
    def test_env_refresh_update_delete_and_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env = Path(directory)/'.env'
            with patch.object(configuration, 'ENV_FILE', env):
                self.assertEqual(configuration.config_value('SUPABASE_DB_URL'), '')
                env.write_text('\ufeffSUPABASE_DB_URL=first\n', encoding='utf-8')
                self.assertEqual(configuration.config_value('SUPABASE_DB_URL'), 'first')
                env.write_text('SUPABASE_DB_URL=second\n', encoding='utf-8')
                self.assertEqual(configuration.config_value('SUPABASE_DB_URL'), 'second')
                self.assertNotIn('SUPABASE_DB_URL', os.environ)
                with patch.dict(os.environ, {'SUPABASE_DB_URL':'deployment'}):
                    self.assertEqual(configuration.config_value('SUPABASE_DB_URL'), 'deployment')
                env.unlink()
                self.assertEqual(configuration.config_value('SUPABASE_DB_URL'), '')

    def test_streamlit_secret_precedes_environment_and_local_file(self):
        with patch('configuration.configured_store', side_effect=RuntimeError('safe-test')), patch.dict(os.environ, {'APP_PASSWORD':'environment-password'}):
            app=AppTest.from_string('from app import secret\nimport streamlit as st\nst.text(secret("APP_PASSWORD"))')
            app.secrets.update(APP_PASSWORD='deployment-password',SUPABASE_DB_URL='')
            app.session_state['authed']=True
            app.run(timeout=20)
            self.assertFalse(app.exception)
            self.assertEqual(app.text[-1].value,'deployment-password')

    def test_diagnostic_never_initializes_or_migrates(self):
        with patch('diagnose_db.config_value',side_effect=lambda key: 'postgresql://appnix_app@host/db' if key=='SUPABASE_DB_URL' else ''), patch('diagnose_db.Store') as factory:
            factory.return_value._options={'user':'appnix_app'}
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(diagnose_db.main(),0)
            factory.return_value.check_connection.assert_called_once_with(require_restricted=True)
            factory.return_value.initialize.assert_not_called()
            self.assertIn('Connected',output.getvalue())

    def test_refresh_connection_reads_edited_env(self):
        seen=[]
        def factory(getter):
            seen.append(getter('SUPABASE_DB_URL'))
            raise DatabaseNotConfigured('test')
        with tempfile.TemporaryDirectory() as directory:
            env=Path(directory)/'.env'
            env.write_text('',encoding='utf-8')
            process_env={k:v for k,v in os.environ.items() if k!='SUPABASE_DB_URL'}
            with patch.object(configuration,'ENV_FILE',env), patch.dict(os.environ,process_env,clear=True), patch('configuration.configured_store',side_effect=factory):
                app=AppTest.from_file('app.py',default_timeout=20)
                app.secrets['APP_PASSWORD']='test-only'
                app.session_state['authed']=True
                app.run()
                env.write_text('SUPABASE_DB_URL=edited-local-value\n',encoding='utf-8')
                next(b for b in app.button if b.label=='Refresh connection').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(seen[0],'')
                self.assertEqual(seen[-1],'edited-local-value')


class TLSAndDiagnosticTests(unittest.TestCase):
    def test_missing_certificate(self):
        with self.assertRaises(DatabaseConnectionError) as caught:
            DatabaseConfig('postgresql://appnix_app:secret-value@host/db', 'nonexistent-secret-value.crt').options()
        self.assertEqual(caught.exception.category,'certificate_missing')
        self.assertNotIn('secret-value',str(caught.exception))

    def test_certificate_is_passed_to_driver_with_verify_full(self):
        with tempfile.TemporaryDirectory() as directory:
            cert=Path(directory)/'test.crt'
            cert.write_text('mock-certificate')
            store=Store(DatabaseConfig('postgresql://appnix_app:secret-value@host/db',str(cert)))
            with patch('storage.psycopg.connect') as connect:
                conn=connect.return_value.__enter__.return_value
                conn.execute.return_value.fetchone.return_value={'version':'001_persistence'}
                store.check_connection()
                self.assertEqual(connect.call_args.kwargs['sslmode'],'verify-full')
                self.assertEqual(connect.call_args.kwargs['sslrootcert'],str(cert.resolve()))
                self.assertEqual(conn.execute.call_args_list[0].args[0],'SET TRANSACTION READ ONLY')

    def test_failures_are_distinct_and_sanitized(self):
        errors=[(psycopg.OperationalError('password authentication failed secret-value'),'authentication'),
                (psycopg.OperationalError('could not translate host name secret-value'),'network'),
                (psycopg.OperationalError('certificate verify failed secret-value'),'tls'),
                (psycopg.OperationalError('root certificate file secret-value does not exist'),'certificate_missing'),
                (psycopg.errors.InsufficientPrivilege('secret-value'),'permission')]
        store=Store(DatabaseConfig('postgresql://test@127.0.0.1/appnix_test?sslmode=disable',allow_local_test=True))
        for error, category in errors:
            with self.subTest(category=category), patch('storage.psycopg.connect',side_effect=error), self.assertLogs('appnix') as logs:
                with self.assertRaises(DatabaseConnectionError) as caught:
                    store.check_connection()
                self.assertEqual(caught.exception.category,category)
                self.assertNotIn('secret-value',str(caught.exception)+str(logs.output))

    def test_missing_migration_marker(self):
        store=Store(DatabaseConfig('postgresql://test@127.0.0.1/appnix_test?sslmode=disable',allow_local_test=True))
        with patch('storage.psycopg.connect') as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=None
            with self.assertRaises(MigrationRequired):
                store.check_connection()

    def test_ui_shows_sanitized_diagnostic(self):
        # Reuse the actual magic-enabled app fixture without inheriting its tests.
        helper=test_sidebar.SidebarTests()
        for category in ('certificate_missing','authentication','network','tls','permission'):
            with self.subTest(category=category):
                app,_=helper.app(check_error=DatabaseConnectionError(category))
                helper.assert_clean(app)
                self.assertIn(str(DatabaseConnectionError(category)),[node.value for node in app.sidebar.caption])


if __name__=='__main__':
    unittest.main()
