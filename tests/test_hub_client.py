import json
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import pytest
from urllib.parse import urlparse
import time
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))
import hub_client

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

class FakeHubHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        self.server.requests.append({
            "method": "GET",
            "path": self.path,
            "headers": dict(self.headers),
        })

        
        if "/agents" in self.path:
            self._send_json(200, {"agents": getattr(self.server, "agents", [])})
            return

        
        if "/schedules" in self.path:
            self._send_json(200, [])
            return

        if self.path == "/health":
            if getattr(self.server, "fail_health_404", False):
                self._send_json(404, {"error": "not found"})
            else:
                self._send_json(200, {"status": "healthy", "scionVersion": "0.9.0"})
            return

        if self.path == "/healthz":
            if getattr(self.server, "fail_healthz_404", False):
                self._send_json(404, {"error": "not found"})
            else:
                self._send_json(200, {"status": "healthy", "scionVersion": "0.9.0"})
            return
            
        if self.path == "/non-utf8":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"hello \xff world")
            return
            
        if self.path == "/error-non-utf8":
            self.send_response(500)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"error \xff page")
            return

        if self.path == "/api/v1/projects":
            self._send_json(200, {"projects": [{"slug": "proj-1"}]})
            return
            
        if self.path.startswith("/api/v1/projects/proj-1/shared-dirs/"):
            parts = self.path.split("/")
            if len(parts) >= 7 and parts[7:] and parts[7] != "files":
                # GET file content
                dirname = parts[6]
                rel_path = urllib.parse.unquote("/".join(parts[8:]))
                files_dict = self.server.shared_dir_files.get(("proj-1", dirname), {})
                self._send_json(200, {"content": files_dict.get(rel_path, "")})
                return
            dirname = parts[6]
            files_dict = self.server.shared_dir_files.get(("proj-1", dirname), {})
            # Return list of files
            # The structure returned by the actual API: items -> list of dicts with name, content if it's small, etc.
            # But the test sync_shared_dir fetches each file or uses list to see what exists.
            items = []
            for name, content in files_dict.items():
                items.append({"name": name, "content": content})
            self._send_json(200, {"items": items})
            return

        if self.path.startswith("/api/v1/projects/proj-1/agents/lead"):
            if not getattr(self.server, "agent_created", False) and getattr(self.server, "cold_start_503_once", False):
                self._send_json(404, {"error": "not found"})
                return
            phase = getattr(self.server, "agent_phase", "running")
            self._send_json(200, {"phase": phase})
            return

        self._send_json(404, {"error": "not found"})

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.server.requests.append({
            "method": "PUT",
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        
        if self.path.startswith("/api/v1/projects/proj-1/shared-dirs/"):
            parts = self.path.split("/")
            dirname = parts[6]
            rel_path = urllib.parse.unquote("/".join(parts[8:]))
            files_dict = self.server.shared_dir_files.setdefault(("proj-1", dirname), {})
            data = json.loads(body.decode("utf-8"))
            files_dict[rel_path] = data.get("content", "")
            self._send_json(200, {"status": "ok"})
            return

        self._send_json(200, {"status": "ok"})
        
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.server.requests.append({
            "method": "POST",
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        

        if self.path.endswith("/exec"):
            parts = self.path.split("/")
            pid, slug = parts[4], parts[6]
            import json
            b = json.loads(body.decode("utf-8")) if body else {}
            self.server.exec_commands.setdefault((pid, slug), []).append(b.get("command", []))
            self._send_json(200, {"output": self.server.exec_outputs.get((pid, slug), "")})
            return
        if "/agents/" in self.path and self.path.endswith("/start"):
            self._send_json(200, {})
            return

        if self.path == "/api/v1/projects/proj-1/agents":
            self.server.agent_created = True
            if getattr(self.server, "cold_start_503_once", False):
                self.server.cold_start_503_once = False
                self.server.agent_phase = "running" # simulates background provisioning succeeding
                self.send_response(503)
                self.end_headers()
                return
            self._send_json(200, {"phase": "provisioning"})
            return

        self._send_json(200, {"status": "ok"})
        
    def do_DELETE(self):
        self.server.requests.append({
            "method": "DELETE",
            "path": self.path,
            "headers": dict(self.headers),
        })
        if self.path.startswith("/api/v1/projects/proj-1/shared-dirs/"):
            parts = self.path.split("/")
            dirname = parts[6]
            rel_path = urllib.parse.unquote("/".join(parts[8:]))
            files_dict = self.server.shared_dir_files.get(("proj-1", dirname), {})
            if rel_path in files_dict:
                del files_dict[rel_path]
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(200, {"status": "ok"})

class CrossOriginHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self.server.cross_origin_requests.append({
            "method": "PUT",
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })
        self.send_response(200)
        self.end_headers()


@pytest.fixture
def fake_hub():
    port = get_free_port()
    server = HTTPServer(("127.0.0.1", port), FakeHubHandler)
    server.agents = []
    server.exec_outputs = {}
    server.exec_commands = {}
    server.started_agents = []
    def seed_agent(pid, **kwargs):
        server.agents.append({"id": "ag-1", "slug": kwargs.get("slug"), "phase": kwargs.get("phase"), "activity": kwargs.get("activity"), "harnessConfig": kwargs.get("harness"), "taskSummary": kwargs.get("task_summary"), "runtime": kwargs.get("runtime"), "pod_name": kwargs.get("pod_name")})
    server.seed_agent = seed_agent
    def set_agent_phase(pid, slug, phase):
        for a in server.agents:
            if a.get("slug") == slug:
                a["phase"] = phase
    server.set_agent_phase = set_agent_phase
    server.url = f"http://127.0.0.1:{port}"
    server.requests = []
    server.shared_dir_files = {}
    
    cross_port = get_free_port()
    cross_server = HTTPServer(("127.0.0.1", cross_port), CrossOriginHandler)
    cross_server.cross_origin_requests = []
    server.cross_origin_upload_url = f"http://127.0.0.1:{cross_port}/upload"
    server.cross_origin_requests = cross_server.cross_origin_requests
    
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    
    t2 = threading.Thread(target=cross_server.serve_forever, daemon=True)
    t2.start()
    
    yield server
    
    server.shutdown()
    server.server_close()
    cross_server.shutdown()
    cross_server.server_close()

def test_health_falls_back_between_health_and_healthz(fake_hub):
    fake_hub.fail_health_404 = True
    fake_hub.fail_healthz_404 = False
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-123")
    assert client.health() == {"status": "healthy", "scionVersion": "0.9.0"}

def test_load_credentials_token_normalizes_endpoint(tmp_path):
    creds = {
        "version": 1,
        "hubs": {
            "https://hub.example.com": {
                "accessToken": "scion_oauth_tok",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        },
    }
    (tmp_path / "credentials.json").write_text(json.dumps(creds))
    assert hub_client.load_credentials_token(str(tmp_path), "https://HUB.example.com/") == "scion_oauth_tok"

def test_iap_proxy_authorization_keeps_hub_bearer_and_strips_on_cross_origin(fake_hub):
    client = hub_client.HubClient(
        base_url=fake_hub.url,
        token="hub-jwt",
        transport_token="iap-oidc-jwt",
        transport_mode="iap",
    )
    status, data = client.request("GET", "/api/v1/projects")
    assert status == 200
    last = fake_hub.requests[-1]["headers"]
    assert last["Authorization"] == "Bearer hub-jwt"
    assert last["Proxy-Authorization"] == "Bearer iap-oidc-jwt"

    status, _ = client.request("PUT", fake_hub.cross_origin_upload_url, raw=b"hello")
    assert status == 200
    upload_hdrs = fake_hub.cross_origin_requests[-1]["headers"]
    assert "Authorization" not in upload_hdrs
    assert "Proxy-Authorization" not in upload_hdrs

def test_ensure_agent_started_polls_after_cold_autopilot_503_without_duplicate_create(fake_hub):
    fake_hub.cold_start_503_once = True
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-123")
    agent = client.ensure_agent_started(
        "proj-1",
        "lead",
        create_body={"name": "lead", "template": "lead", "task": "Wake up."},
        wait_sec=5,
        poll_sec=0.05,
    )
    assert agent["phase"] == "running"
    create_posts = [r for r in fake_hub.requests if r["method"] == "POST" and r["path"] == "/api/v1/projects/proj-1/agents"]
    assert len(create_posts) == 1

def test_sync_shared_dir_uploads_changed_and_deletes_stale_files(fake_hub, tmp_path):
    src = tmp_path / "knowledge-plane"
    (src / "adrs").mkdir(parents=True)
    (src / "index.md").write_text("# Index\n")
    (src / "adrs" / "ADR-001.md").write_text("# ADR 1\n")
    fake_hub.shared_dir_files[("proj-1", "knowledge-plane")] = {"stale.md": "old", "index.md": "# Index\n"}

    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-123")
    plan = client.sync_shared_dir("proj-1", "knowledge-plane", str(src), apply=False)
    assert plan == {"uploaded": ["adrs/ADR-001.md"], "deleted": ["stale.md"], "unchanged": ["index.md"]}
    assert "stale.md" in fake_hub.shared_dir_files[("proj-1", "knowledge-plane")]

    applied = client.sync_shared_dir("proj-1", "knowledge-plane", str(src), apply=True)
    assert applied == plan
    assert fake_hub.shared_dir_files[("proj-1", "knowledge-plane")] == {
        "index.md": "# Index\n",
        "adrs/ADR-001.md": "# ADR 1\n",
    }

def test_non_utf8_response_and_error(fake_hub):
    client = hub_client.HubClient(base_url=fake_hub.url, token="tok-123")
    status, data = client.request("GET", "/non-utf8")
    assert status == 200
    assert "hello \ufffd world" in data

    with pytest.raises(hub_client.HubAPIError) as exc_info:
        client.request("GET", "/error-non-utf8")
    assert exc_info.value.status == 500
    assert "error \ufffd page" in exc_info.value.body
