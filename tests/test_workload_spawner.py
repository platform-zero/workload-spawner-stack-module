import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workload_spawner import api, common


class WorkloadSpawnerTest(unittest.TestCase):
    def test_slug_and_principal_boundaries(self):
        self.assertEqual("alice-example", common.slugify(" Alice Example "))
        self.assertEqual("alice", common.user_slug("alice@example.test"))
        with mock.patch.dict(os.environ, {"WORKLOAD_SPAWNER_ALLOWED_GROUPS": "admins,operators"}):
            principal = common.principal_from_headers({
                "Remote-User": "alice",
                "Remote-Email": "alice@example.test",
                "Remote-Groups": "users operators",
            })
            self.assertTrue(principal.allowed)
            self.assertEqual({"operators", "users"}, principal.groups)

    def test_state_round_trip_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"WORKLOAD_SPAWNER_STATE_DIR": temp_dir}):
                common.save_instances({"n8n-alice-demo": {"owner": "alice"}})
                self.assertEqual("alice", common.load_instances()["n8n-alice-demo"]["owner"])
                self.assertEqual([], list(Path(temp_dir).glob("tmp*")))

    def test_container_uses_template_networks_port_and_secret_context(self):
        instance = {
            "id": "n8n-alice-demo",
            "owner": "alice",
            "containerName": "workload-n8n-alice-demo",
            "volumeName": "workload_n8n_alice_demo_data",
            "templateContext": {"db_password": "secret-value", "host": "n8n-alice.apps.example.test"},
        }
        template = {
            "image": "example.test/n8n:pinned",
            "dataVolume": "/data",
            "environment": {"DB_PASSWORD": "{{db_password}}", "HOST": "{{host}}"},
        }
        env = {
            "WORKLOAD_SPAWNER_PODMAN_SOCKET": "unix:///run/podman/podman.sock",
            "WORKLOAD_SPAWNER_PODMAN_NETWORKS": "spawner_caddy,spawner_postgres",
        }
        with mock.patch.dict(os.environ, env), mock.patch.object(api, "run_checked") as run_checked:
            api.ensure_container(instance, template)
        args = run_checked.call_args.args[0]
        self.assertIn("unix:///run/podman/podman.sock", args)
        self.assertIn("spawner_caddy", args)
        self.assertIn("spawner_postgres", args)
        self.assertIn("DB_PASSWORD=secret-value", args)

    def test_postgres_values_are_passed_as_psql_variables(self):
        with mock.patch.object(api, "run_checked") as run_checked:
            api.provision_postgres("n8n_alice", "n8n_alice", "quoted'password")
        first_args = run_checked.call_args_list[0].args[0]
        self.assertIn("db_password=quoted'password", first_args)
        self.assertNotIn("quoted'password", run_checked.call_args_list[0].kwargs["input_text"])


if __name__ == "__main__":
    unittest.main()
