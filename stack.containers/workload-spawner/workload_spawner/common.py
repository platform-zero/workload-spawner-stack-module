from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SAFE_SLUG_RE = re.compile(r"[^a-z0-9-]+")
INSTANCE_STATE_LOCK = threading.RLock()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def state_dir() -> Path:
    path = Path(env("WORKLOAD_SPAWNER_STATE_DIR", "/var/lib/workload-spawner"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def templates_dir() -> Path:
    return Path(env("WORKLOAD_SPAWNER_TEMPLATES_DIR", "/etc/workload-spawner/templates"))


def instances_file() -> Path:
    return state_dir() / "instances.json"


def load_instances() -> dict[str, dict[str, Any]]:
    path = instances_file()
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("instances state must be a JSON object")
    return raw


def save_instances(instances: dict[str, dict[str, Any]]) -> None:
    path = instances_file()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(instances, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def load_templates() -> dict[str, dict[str, Any]]:
    templates: dict[str, dict[str, Any]] = {}
    for path in sorted(templates_dir().glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            template = json.load(handle)
        template_id = template.get("id")
        if not isinstance(template_id, str) or not template_id:
            raise ValueError(f"template {path} is missing id")
        templates[template_id] = template
    return templates


def slugify(value: str, fallback: str = "instance") -> str:
    slug = SAFE_SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:48] or fallback


def user_slug(user: str) -> str:
    return slugify(user.split("@", 1)[0], "user")


def random_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def allowed_groups() -> set[str]:
    raw = env("WORKLOAD_SPAWNER_ALLOWED_GROUPS", "operators,admins")
    return {part.strip() for part in raw.replace(" ", ",").split(",") if part.strip()}


@dataclass(frozen=True)
class Principal:
    username: str
    email: str
    groups: set[str]

    @property
    def allowed(self) -> bool:
        return bool(self.groups & allowed_groups())


def parse_groups(value: str) -> set[str]:
    return {part.strip() for part in re.split(r"[, ]+", value or "") if part.strip()}


def principal_from_headers(headers: Any) -> Principal:
    return Principal(
        username=headers.get("Remote-User", ""),
        email=headers.get("Remote-Email", ""),
        groups=parse_groups(headers.get("Remote-Groups", "")),
    )


class JsonHandler(BaseHTTPRequestHandler):
    server_version = "workload-spawner/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def write_json(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def write_error_json(self, status: int, message: str) -> None:
        self.write_json(status, {"error": message})


def route_path(handler: BaseHTTPRequestHandler) -> str:
    return urlparse(handler.path).path


def require_principal(handler: JsonHandler) -> Principal | None:
    principal = principal_from_headers(handler.headers)
    if not principal.username:
        handler.write_error_json(HTTPStatus.UNAUTHORIZED, "missing authenticated user")
        return None
    if not principal.allowed:
        handler.write_error_json(HTTPStatus.FORBIDDEN, "workload spawner requires operators or admins")
        return None
    return principal


def run_checked(args: list[str], *, env_overrides: dict[str, str] | None = None, input_text: str | None = None) -> str:
    merged_env = os.environ.copy()
    if env_overrides:
        merged_env.update(env_overrides)
    result = subprocess.run(
        args,
        check=False,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=merged_env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{args[0]} failed: {detail}")
    return result.stdout.strip()
