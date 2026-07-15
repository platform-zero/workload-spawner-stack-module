from __future__ import annotations

import http.client
from http import HTTPStatus
from http.server import ThreadingHTTPServer

from .common import JsonHandler, load_instances, require_principal, route_path


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class RouterHandler(JsonHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self.proxy()

    def do_POST(self) -> None:
        self.proxy()

    def do_PUT(self) -> None:
        self.proxy()

    def do_PATCH(self) -> None:
        self.proxy()

    def do_DELETE(self) -> None:
        self.proxy()

    def proxy(self) -> None:
        if route_path(self) == "/healthz":
            self.write_json(HTTPStatus.OK, {"ok": True})
            return
        principal = require_principal(self)
        if principal is None:
            return
        host = self.headers.get("Host", "").split(":", 1)[0]
        instance = next((item for item in load_instances().values() if item.get("host") == host), None)
        if instance is None:
            self.write_error_json(HTTPStatus.NOT_FOUND, "unknown workload")
            return
        if instance.get("owner") != principal.username and "admins" not in principal.groups:
            self.write_error_json(HTTPStatus.FORBIDDEN, "workload belongs to another user")
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        upstream = http.client.HTTPConnection(instance["containerName"], 5678, timeout=120)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and not key.lower().startswith("remote-")
        }
        headers["Host"] = host
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Host"] = host
        upstream.request(self.command, self.path, body=body, headers=headers)
        response = upstream.getresponse()
        response_body = response.read()
        self.send_response(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 8081), RouterHandler).serve_forever()


if __name__ == "__main__":
    main()
