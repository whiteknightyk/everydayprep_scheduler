import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app
import db
import seed
from db import _postgresql_sql
from settings import load_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SettingsTests(unittest.TestCase):
    def test_production_requires_a_stable_session_secret(self):
        settings = load_settings(
            {
                "SAT_SCHEDULER_ENV": "production",
                "SAT_SCHEDULER_HTTPS_ONLY": "1",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "SESSION_SECRET"):
            settings.validate()

    def test_production_rejects_documented_placeholder_secret(self):
        settings = load_settings(
            {
                "SAT_SCHEDULER_ENV": "production",
                "SAT_SCHEDULER_SESSION_SECRET": (
                    "replace-with-at-least-32-random-characters"
                ),
                "SAT_SCHEDULER_HTTPS_ONLY": "1",
            }
        )

        with self.assertRaisesRegex(RuntimeError, "randomly generated"):
            settings.validate()

    def test_postgresql_settings_are_validated(self):
        settings = load_settings(
            {
                "SAT_SCHEDULER_ENV": "production",
                "SAT_SCHEDULER_SESSION_SECRET": "s" * 48,
                "SAT_SCHEDULER_HTTPS_ONLY": "1",
                "SAT_SCHEDULER_DB_ENGINE": "postgresql",
                "SAT_SCHEDULER_DB_HOST": "database.internal",
                "SAT_SCHEDULER_DB_NAME": "sat_scheduler",
                "SAT_SCHEDULER_DB_USER": "sat_scheduler",
                "SAT_SCHEDULER_DB_PASSWORD": "secret-password",
            }
        )

        settings.validate()
        self.assertEqual(settings.database_port, 5432)

    def test_database_url_selects_postgresql_for_render(self):
        settings = load_settings(
            {
                "SAT_SCHEDULER_ENV": "production",
                "SAT_SCHEDULER_SESSION_SECRET": "s" * 48,
                "SAT_SCHEDULER_HTTPS_ONLY": "1",
                "DATABASE_URL": (
                    "postgresql://sat_scheduler:secret@database.internal:5432/"
                    "sat_scheduler"
                ),
            }
        )

        settings.validate()
        self.assertEqual(settings.database_engine, "postgresql")
        self.assertEqual(
            settings.database_url,
            "postgresql://sat_scheduler:secret@database.internal:5432/sat_scheduler",
        )

    def test_app_specific_database_url_takes_precedence(self):
        settings = load_settings(
            {
                "DATABASE_URL": "postgresql://generic.example/generic",
                "SAT_SCHEDULER_DATABASE_URL": (
                    "postgresql://specific.example/sat_scheduler"
                ),
            }
        )

        self.assertEqual(
            settings.database_url,
            "postgresql://specific.example/sat_scheduler",
        )

    def test_non_postgresql_database_url_is_rejected(self):
        settings = load_settings({"DATABASE_URL": "mysql://database.example/app"})

        with self.assertRaisesRegex(RuntimeError, "PostgreSQL connection URL"):
            settings.validate()

    def test_sqlite_database_path_can_be_overridden(self):
        settings = load_settings(
            {"SAT_SCHEDULER_DATABASE_PATH": "/data/scheduler.db"}
        )

        self.assertEqual(settings.database_path, Path("/data/scheduler.db"))


class SeedSafetyTests(unittest.TestCase):
    def test_demo_seed_refuses_to_run_in_production(self):
        production_settings = load_settings(
            {
                "SAT_SCHEDULER_ENV": "production",
                "SAT_SCHEDULER_SESSION_SECRET": "s" * 48,
                "SAT_SCHEDULER_HTTPS_ONLY": "1",
            }
        )

        with (
            patch.object(seed, "SETTINGS", production_settings),
            self.assertRaisesRegex(RuntimeError, "demo accounts"),
        ):
            seed.main()


class PostgreSQLCompatibilityTests(unittest.TestCase):
    def test_begin_immediate_uses_postgresql_transaction_syntax(self):
        sql, returns_id = _postgresql_sql("BEGIN IMMEDIATE")

        self.assertEqual(sql, "BEGIN ISOLATION LEVEL SERIALIZABLE")
        self.assertFalse(returns_id)

    def test_qmark_parameters_and_insert_id_are_translated(self):
        sql, returns_id = _postgresql_sql(
            "INSERT INTO users(name, role) VALUES(?, ?)"
        )

        self.assertEqual(
            sql,
            "INSERT INTO users(name, role) VALUES(%s, %s) RETURNING id",
        )
        self.assertTrue(returns_id)

    def test_insert_or_ignore_uses_on_conflict(self):
        sql, returns_id = _postgresql_sql(
            "INSERT OR IGNORE INTO availability(user_id, weekday) VALUES(?, ?)"
        )

        self.assertEqual(
            sql,
            "INSERT INTO availability(user_id, weekday) VALUES(%s, %s) "
            "ON CONFLICT DO NOTHING",
        )
        self.assertFalse(returns_id)

    def test_connect_uses_managed_database_url(self):
        settings = load_settings(
            {"DATABASE_URL": "postgresql://user:secret@database.internal/app"}
        )
        raw_connection = Mock()

        with (
            patch.object(db, "DB_ENGINE", "postgresql"),
            patch.object(db, "SETTINGS", settings),
            patch.object(db.psycopg, "connect", return_value=raw_connection) as connect,
        ):
            connection = db.connect()

        connect.assert_called_once_with(
            "postgresql://user:secret@database.internal/app",
            connect_timeout=10,
            row_factory=db.dict_row,
        )
        self.assertIs(connection._connection, raw_connection)


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "scheduler.db"
        db.init_db()
        self.client = TestClient(app.app, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_health_endpoints_do_not_require_login_or_setup(self):
        health = self.client.get("/healthz")
        ready = self.client.get("/readyz")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(health.headers["x-content-type-options"], "nosniff")
        self.assertEqual(health.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", health.headers["content-security-policy"])
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json(), {"status": "ready"})


class AwsInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (PROJECT_ROOT / "deploy/aws/cloudformation.yml").read_text(
            encoding="utf-8"
        )
        cls.deploy_script = (PROJECT_ROOT / "deploy/aws/deploy.ps1").read_text(
            encoding="utf-8"
        )

    def test_cloudformation_runs_the_application_on_ec2(self):
        self.assertIn("AWS::EC2::LaunchTemplate", self.template)
        self.assertIn("AWS::AutoScaling::AutoScalingGroup", self.template)
        self.assertIn("AutoScalingInstanceRefresh", self.template)
        self.assertIn("TargetType: instance", self.template)
        self.assertNotIn("AWS::ECS::", self.template)

    def test_ec2_bootstrap_uses_managed_secrets_and_health_check(self):
        self.assertIn("AmazonSSMManagedInstanceCore", self.template)
        self.assertIn("secretsmanager:GetSecretValue", self.template)
        self.assertIn("SAT_SCHEDULER_DB_ENGINE=postgresql", self.template)
        self.assertIn("http://127.0.0.1:8000/readyz", self.template)

    def test_deploy_script_passes_ec2_and_ecr_parameters(self):
        self.assertIn('"RepositoryArn=$repositoryArn"', self.deploy_script)
        self.assertIn('"InstanceType=$InstanceType"', self.deploy_script)
        self.assertIn('"DatabaseInstanceClass=$DatabaseInstanceClass"', self.deploy_script)


if __name__ == "__main__":
    unittest.main()
