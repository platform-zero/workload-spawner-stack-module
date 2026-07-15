from __future__ import annotations

from http import HTTPStatus
from http.server import ThreadingHTTPServer
from typing import Any

from .common import (
    JsonHandler,
    INSTANCE_STATE_LOCK,
    env,
    load_instances,
    load_templates,
    random_token,
    require_principal,
    run_checked,
    save_instances,
    slugify,
    user_slug,
    route_path,
)


def render_template_value(value: str, context: dict[str, str]) -> str:
    rendered = value
    for key, replacement in context.items():
        rendered = rendered.replace("{{" + key + "}}", replacement)
    return rendered


def provision_postgres(db_name: str, db_user: str, db_password: str) -> None:
    sql = """
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = :'db_user')\\gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'db_user', :'db_password')\\gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db_name')\\gexec
SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', :'db_name', :'db_user')\\gexec
"""
    run_checked(
        [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"db_name={db_name}",
            "-v",
            f"db_user={db_user}",
            "-v",
            f"db_password={db_password}",
            "--host",
            env("POSTGRES_HOST", "postgres"),
            "--port",
            env("POSTGRES_PORT", "5432"),
            "--username",
            env("POSTGRES_ADMIN_USER", "postgres"),
            "--dbname",
            env("POSTGRES_ADMIN_DB", "postgres"),
        ],
        env_overrides={"PGPASSWORD": env("POSTGRES_ADMIN_PASSWORD")},
        input_text=sql,
    )
    run_checked(
        [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"db_user={db_user}",
            "--host",
            env("POSTGRES_HOST", "postgres"),
            "--port",
            env("POSTGRES_PORT", "5432"),
            "--username",
            env("POSTGRES_ADMIN_USER", "postgres"),
            "--dbname",
            db_name,
        ],
        env_overrides={"PGPASSWORD": env("POSTGRES_ADMIN_PASSWORD")},
        input_text="SELECT format('GRANT ALL ON SCHEMA public TO %I', :'db_user')\\gexec\n",
    )


def podman_network_args() -> list[str]:
    networks = [part.strip() for part in env("WORKLOAD_SPAWNER_PODMAN_NETWORKS").split(",") if part.strip()]
    args: list[str] = []
    for network in networks:
        args += ["--network", network]
    return args


def ensure_container(instance: dict[str, Any], template: dict[str, Any]) -> None:
    env_map = template.get("environment") or {}
    if not isinstance(env_map, dict):
        raise ValueError("template environment must be an object")
    context = instance["templateContext"]
    args = [
        "podman",
        "--url",
        env("WORKLOAD_SPAWNER_PODMAN_SOCKET", "unix:///run/podman/podman.sock"),
        "run",
        "-d",
        "--replace",
        "--name",
        instance["containerName"],
        "--label",
        "org.webservices.workload-spawner=true",
        "--label",
        f"org.webservices.workload-spawner.instance={instance['id']}",
        "--label",
        f"org.webservices.workload-spawner.owner={instance['owner']}",
        "-v",
        f"{instance['volumeName']}:{template['dataVolume']}:Z",
    ]
    args += podman_network_args()
    for key, value in sorted(env_map.items()):
        args += ["-e", f"{key}={render_template_value(str(value), context)}"]
    args += [template["image"]]
    run_checked(args)


def stop_container(container_name: str) -> None:
    run_checked(["podman", "--url", env("WORKLOAD_SPAWNER_PODMAN_SOCKET", "unix:///run/podman/podman.sock"), "stop", "--ignore", container_name])


class ApiHandler(JsonHandler):
    def do_GET(self) -> None:
        path = route_path(self)
        if path == "/healthz":
            self.write_json(HTTPStatus.OK, {"ok": True})
            return
        principal = require_principal(self)
        if principal is None:
            return
        if path == "/api/templates":
            templates = load_templates()
            public = [
                {key: template[key] for key in ("id", "name", "description", "persistent") if key in template}
                for template in templates.values()
            ]
            self.write_json(HTTPStatus.OK, {"templates": public})
            return
        if path == "/api/instances":
            instances = [
                instance
                for instance in load_instances().values()
                if instance.get("owner") == principal.username or "admins" in principal.groups
            ]
            self.write_json(HTTPStatus.OK, {"instances": instances})
            return
        self.write_error_json(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        path = route_path(self)
        principal = require_principal(self)
        if principal is None:
            return
        if path == "/api/instances":
            self.create_instance(principal.username)
            return
        if path.startswith("/api/instances/") and path.endswith("/stop"):
            instance_id = path.split("/")[3]
            self.stop_instance(principal.username, principal.groups, instance_id)
            return
        self.write_error_json(HTTPStatus.NOT_FOUND, "not found")

    def create_instance(self, owner: str) -> None:
        payload = self.read_json()
        template_id = str(payload.get("template", "n8n"))
        templates = load_templates()
        template = templates.get(template_id)
        if template is None:
            self.write_error_json(HTTPStatus.BAD_REQUEST, "unknown template")
            return

        name = slugify(str(payload.get("name") or template_id), template_id)
        owner_slug = user_slug(owner)
        instance_id = f"{template_id}-{owner_slug}-{name}"
        host = f"{template['routePrefix']}-{owner_slug}-{name}.{env('WORKLOAD_SPAWNER_ROUTE_SUFFIX')}"
        db_name = slugify(f"{template_id}_{owner_slug}_{name}", "workload").replace("-", "_")[:63]
        db_user = slugify(f"{template_id}_{owner_slug}_{name}", "workload").replace("-", "_")[:63]
        db_password = random_token()
        context = {
            "host": host,
            "db_name": db_name,
            "db_user": db_user,
            "db_password": db_password,
            "encryption_key": random_token(48),
        }
        instance = {
            "id": instance_id,
            "template": template_id,
            "owner": owner,
            "host": host,
            "url": f"https://{host}/",
            "containerName": f"workload-{instance_id}",
            "volumeName": f"workload_{instance_id}_data",
            "port": int(template["port"]),
            "status": "creating",
            "templateContext": context,
        }
        with INSTANCE_STATE_LOCK:
            instances = load_instances()
            if instance_id in instances:
                self.write_error_json(HTTPStatus.CONFLICT, "instance already exists")
                return
            provision_postgres(db_name, db_user, db_password)
            ensure_container(instance, template)
            instance["status"] = "running"
            instances[instance_id] = instance
            save_instances(instances)
        public = {key: value for key, value in instance.items() if key != "templateContext"}
        self.write_json(HTTPStatus.CREATED, public)

    def stop_instance(self, owner: str, groups: set[str], instance_id: str) -> None:
        with INSTANCE_STATE_LOCK:
            instances = load_instances()
            instance = instances.get(instance_id)
            if instance is None:
                self.write_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if instance.get("owner") != owner and "admins" not in groups:
                self.write_error_json(HTTPStatus.FORBIDDEN, "instance belongs to another user")
                return
            stop_container(instance["containerName"])
            instance["status"] = "stopped"
            save_instances(instances)
        self.write_json(HTTPStatus.OK, {"id": instance_id, "status": "stopped"})


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 8080), ApiHandler).serve_forever()


if __name__ == "__main__":
    main()
