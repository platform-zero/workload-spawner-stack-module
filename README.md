# Workload Spawner Stack Module

Generic RBAC/SSO workload spawner for operator-managed per-user services.

The initial template is `n8n`, using wildcard routes under `*.apps.$DOMAIN`,
edge SSO headers from Caddy, a dedicated `workload-spawner` rootless Podman
socket domain, and per-instance Postgres roles/databases.

Jupyter notebook spawning and Forgejo runner job spawning are documented as
future adapters so this module can become the shared control plane after parity
tests are in place. This module does not replace JupyterHub or Forgejo runner
yet.
