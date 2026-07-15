package org.webservices.testrunner

import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class WorkloadSpawnerConfigTest {

    @Test
    fun `workload spawner socket consumer uses dedicated rootless domain`() {
        val runtime = TestSourceFiles.moduleText("workload-spawner", "stack.runtime.yaml")

        assertTrue(runtime.contains("workload-spawner-api:"))
        assertTrue(runtime.contains("workload-spawner-router:"))
        assertTrue(runtime.contains("rootlessDomain: \"workload-spawner\""))
        assertTrue(runtime.contains("%t/podman/podman.sock:/run/podman/podman.sock"))
        assertFalse(runtime.contains("/run/user/999/podman/podman.sock"))
    }

    @Test
    fun `n8n template uses wildcard subdomain and per instance postgres context`() {
        val template = TestSourceFiles.moduleText("workload-spawner", "stack.config/workload-spawner/templates/n8n.json")

        assertTrue(template.contains("\"routePrefix\": \"n8n\""))
        assertTrue(template.contains("\"DB_TYPE\": \"postgresdb\""))
        assertTrue(template.contains("\"DB_POSTGRESDB_DATABASE\": \"{{db_name}}\""))
        assertTrue(template.contains("\"DB_POSTGRESDB_USER\": \"{{db_user}}\""))
        assertTrue(template.contains("\"DB_POSTGRESDB_PASSWORD\": \"{{db_password}}\""))
        assertTrue(template.contains("\"N8N_ENCRYPTION_KEY\": \"{{encryption_key}}\""))
        assertTrue(template.contains("\"WEBHOOK_URL\": \"https://{{host}}/\""))
    }

    @Test
    fun `caddy protects spawner API and wildcard workload routes`() {
        val caddy = TestSourceFiles.moduleText("caddy", "stack.config/caddy/Caddyfile")

        assertTrue(caddy.contains("spawner.{${'$'}DOMAIN}"))
        assertTrue(caddy.contains("*.apps.{${'$'}DOMAIN}"))
        assertTrue(caddy.contains("import keycloak_group_allow workload-spawner admins|operators"))
        assertTrue(caddy.contains("import keycloak_group_allow workload-spawner-app admins|operators"))
        assertTrue(caddy.contains("reverse_proxy workload-spawner-api:8080"))
        assertTrue(caddy.contains("reverse_proxy workload-spawner-router:8081"))
        assertTrue(caddy.contains("header_up Remote-User {header.Remote-User}"))
        assertTrue(caddy.contains("header_up Remote-Groups {header.Remote-Groups}"))
    }

    @Test
    fun `postgres provisioning uses psql variables and server side quoting`() {
        val api = TestSourceFiles.moduleText("workload-spawner", "stack.containers/workload-spawner/workload_spawner/api.py")

        assertTrue(api.contains("-v"))
        assertTrue(api.contains("format('CREATE ROLE %I LOGIN PASSWORD %L'"))
        assertTrue(api.contains("format('CREATE DATABASE %I OWNER %I'"))
        assertFalse(api.contains("CREATE ROLE {"))
        assertFalse(api.contains("shell=True"))
    }

    @Test
    fun `authenticated spawner root exposes a discoverable service contract`() {
        val api = TestSourceFiles.moduleText("workload-spawner", "stack.containers/workload-spawner/workload_spawner/api.py")

        assertTrue(api.contains("if path == \"/\":"))
        assertTrue(api.contains("\"service\": \"Workload Spawner\""))
        assertTrue(api.contains("\"templates\": \"/api/templates\""))
        assertTrue(api.contains("\"instances\": \"/api/instances\""))
    }

}
