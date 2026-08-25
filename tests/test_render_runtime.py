import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RenderBlueprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blueprint = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")
        cls.dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    def test_blueprint_uses_docker_and_readiness_check(self):
        self.assertIn("runtime: docker", self.blueprint)
        self.assertIn("dockerfilePath: ./Dockerfile", self.blueprint)
        self.assertIn("healthCheckPath: /readyz", self.blueprint)
        self.assertIn("os.environ.get('PORT', '8000')", self.dockerfile)

    def test_blueprint_generates_secret_and_connects_managed_database(self):
        self.assertIn("key: SAT_SCHEDULER_SESSION_SECRET", self.blueprint)
        self.assertIn("generateValue: true", self.blueprint)
        self.assertIn("key: SAT_SCHEDULER_DATABASE_URL", self.blueprint)
        self.assertIn("property: connectionString", self.blueprint)

    def test_database_is_private_and_in_the_same_region(self):
        self.assertEqual(self.blueprint.count("region: singapore"), 2)
        self.assertIn("ipAllowList: []", self.blueprint)


if __name__ == "__main__":
    unittest.main()
