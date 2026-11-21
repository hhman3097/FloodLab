"""Regression tests for user-configurable providers without spending API credits."""
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

spec = importlib.util.spec_from_file_location("floodlab_server", Path(__file__).with_name("server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_connection_configuration_and_redaction(monkeypatch):
    requests = []
    def fake_request(self, route, body=None):
        requests.append((self.base, self.model, route, body))
        if self.key == "rejected-private-token":
            raise RuntimeError("Rejected " + self.key)
        assert route=="/responses"
        return {"output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}
    monkeypatch.setattr(server.API, "request", fake_request)
    monkeypatch.setattr(server, "KEY", "")
    monkeypatch.setattr(server, "BASE", "https://api.clawlab.chat/v1")
    monkeypatch.setattr(server, "MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("AI4S_DESKTOP_TOKEN", "test-session-token")
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    def post(route, data, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["X-FloodLab-Token"] = "test-session-token"
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_port}{route}",
                                         json.dumps(data).encode(), headers)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, response.read().decode()
    try:
        body = {"key": "test-private-token", "base_url": "https://example.test/v1/", "model": "custom-6.2-sol"}
        status, text = post("/api/connection", body)
        assert status == 200 and "test-private-token" not in text
        assert server.BASE == "https://example.test/v1"
        assert server.MODEL == "custom-6.2-sol"
        assert requests[-1][3]["model"] == "custom-6.2-sol"
        status, text = post("/api/connection", {**body, "key": "rejected-private-token"})
        assert status == 400 and "rejected-private-token" not in text
        assert server.KEY == "test-private-token"
        assert post("/api/connection", {**body, "base_url": "http://example.test/v1"})[0] == 400
        assert post("/api/connection", body, authenticated=False)[0] == 403
        assert post("/api/disconnect", {})[0] == 200
        assert not server.KEY
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)
