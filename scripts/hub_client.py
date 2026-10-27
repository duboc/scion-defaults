import urllib.request
import urllib.parse
import urllib.error
import json
import hmac
import hashlib
import base64
import sqlite3
import subprocess
import time
import os
from pathlib import Path
from typing import Mapping, Any

def normalize_endpoint(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    return f"{scheme}://{netloc}"

def load_credentials_token(scion_home: str, hub_url: str) -> str | None:
    creds_file = Path(scion_home) / "credentials.json"
    if not creds_file.exists():
        return None
    try:
        creds = json.loads(creds_file.read_text())
        hubs = creds.get("hubs", {})
        norm = normalize_endpoint(hub_url)
        # Use exact match or lower-cased endpoint. 
        # Actually creds.json might store it exactly as norm.
        for k, v in hubs.items():
            if normalize_endpoint(k) == norm:
                # Check expiration
                expires = v.get("expiresAt")
                # Basic check, just comparing strings works for ISO8601 if we format it right, 
                # but better to parse or let string compare work since Z time is used.
                import datetime
                if expires:
                    expires_dt = datetime.datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                    if expires_dt < datetime.datetime.now(datetime.timezone.utc):
                        return None
                return v.get("accessToken")
    except Exception:
        return None
    return None

def mint_local_admin_jwt(scion_home: str, email: str = "", ttl: int = 300) -> str | None:
    db_path = Path(scion_home) / "hub.db"
    env_path = Path(scion_home) / "hub.env"
    if not db_path.exists() or not env_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT id, email, role FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            # Fallback to an admin role or return None
            return None
            
        secret = None
        for line in env_path.read_text().splitlines():
            if line.startswith("SCION_SERVER_SESSION_SECRET="):
                secret = line.split("=", 1)[1].strip()
        if not secret:
            return None
            
        key = hashlib.sha256(("scion-hub-signing-key:user_signing_key:" + secret).encode()).digest()
        
        def b64(data):
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
            
        now = int(time.time())
        claims = {
            "iss": "scion-hub", "sub": user["id"], "aud": ["scion-hub-api"], "exp": now + ttl, "nbf": now - 10,
            "iat": now, "uid": user["id"], "email": user["email"], "name": user["email"], "role": user["role"],
            "type": "access", "client": "web",
        }
        signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
        signature = hmac.new(key, signing_input.encode(), hashlib.sha256).digest()
        return signing_input + "." + b64(signature)
    except Exception:
        return None


class HubAPIError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, body: str):
        super().__init__(f"Hub API Error {status}: {method} {path} - {body}")
        self.status = status
        self.method = method
        self.path = path
        self.body = body


class HubClient:
    def __init__(self, base_url: str, token: str | None = None, transport_token: str | None = None, transport_mode: str = "iap"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport_token = transport_token
        self.transport_mode = transport_mode
        self.is_remote = not (self.base_url.startswith("http://127.0.0.1") or self.base_url.startswith("http://localhost"))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, scion_home: str | None = None) -> "HubClient":
        if env is None:
            env = os.environ
        if scion_home is None:
            scion_home = os.path.expanduser(env.get("SCION_HOME", "~/.scion"))
            
        hub_url = env.get("HUB_URL", "").strip()
        is_remote = bool(hub_url)
        base_url = hub_url or env.get("HUB_LOCAL_URL", "http://127.0.0.1:8080")
        
        token = env.get("SCION_HUB_TOKEN", "").strip()
        if not token:
            token = load_credentials_token(scion_home, base_url)
        if not token:
            email = env.get("PO_EMAIL") or env.get("ADMIN_EMAIL") or ""
            token = mint_local_admin_jwt(scion_home, email)
            
        transport_token = env.get("HUB_TRANSPORT_TOKEN", "").strip()
        transport_mode = env.get("HUB_TRANSPORT_MODE", "iap").strip() or "iap"
        
        audience = env.get("HUB_IAP_AUDIENCE") or env.get("SCION_TRANSPORT_AUDIENCE")
        if not transport_token and audience:
            # Try metadata server
            try:
                req = urllib.request.Request(
                    f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={audience}&format=full",
                    headers={"Metadata-Flavor": "Google"}
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    transport_token = resp.read().decode("utf-8", errors="replace").strip()
            except Exception:
                # fallback to gcloud
                try:
                    out = subprocess.check_output(
                        ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
                        text=True, timeout=10
                    )
                    transport_token = out.strip()
                except Exception:
                    pass
                    
        return cls(base_url, token, transport_token, transport_mode)
        
    def _is_same_origin(self, url: str) -> bool:
        if url.startswith("/"):
            return True
        p1 = urllib.parse.urlparse(self.base_url)
        p2 = urllib.parse.urlparse(url)
        # Check scheme, hostname, and effective port
        def eff_port(p):
            if p.port: return p.port
            return 443 if p.scheme == "https" else 80
        return p1.scheme == p2.scheme and p1.hostname == p2.hostname and eff_port(p1) == eff_port(p2)

    def request(self, method: str, path_or_url: str, body: Any = None, raw: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 60) -> tuple[int, Any]:
        url = self.base_url + path_or_url if path_or_url.startswith("/") else path_or_url
        
        req_headers = {}
        if headers:
            req_headers.update(headers)
            
        if self._is_same_origin(url):
            if self.token:
                req_headers["Authorization"] = f"Bearer {self.token}"
            if self.transport_token:
                if self.transport_mode == "iap":
                    req_headers["Proxy-Authorization"] = f"Bearer {self.transport_token}"
                elif self.transport_mode == "serverless":
                    req_headers["X-Serverless-Authorization"] = f"Bearer {self.transport_token}"
                    
        data = None
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
            
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_body = resp.read()
                if resp_body:
                    try:
                        return resp.status, json.loads(resp_body.decode("utf-8", errors="replace"))
                    except Exception:
                        return resp.status, resp_body.decode("utf-8", errors="replace")
                return resp.status, {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            raise HubAPIError(e.code, method, url, err_body)

    def health(self) -> dict | None:
        try:
            return self.request("GET", "/health")[1]
        except HubAPIError as e:
            if e.status == 404:
                try:
                    return self.request("GET", "/healthz")[1]
                except Exception:
                    return None
            return None
        except Exception:
            return None

    def current_user(self) -> dict | None:
        try:
            return self.request("GET", "/api/v1/auth/me")[1]
        except Exception:
            return None
            
    def find_user(self, email: str) -> dict | None:
        try:
            res = self.request("GET", f"/api/v1/users?search={urllib.parse.quote(email)}")[1]
            items = res.get("users") or res.get("items") or res
            for u in items:
                if u.get("email", "").lower() == email.lower():
                    return u
            return None
        except Exception:
            return None

    def list_projects(self) -> list[dict]:
        res = self.request("GET", "/api/v1/projects")[1]
        return res.get("projects") or res.get("items") or res

    def get_project_by_slug(self, slug: str) -> dict | None:
        for p in self.list_projects():
            if p.get("slug") == slug:
                return p
        return None
        
    def project_id(self, slug: str) -> str:
        p = self.get_project_by_slug(slug)
        if not p:
            raise ValueError(f"Project not found: {slug}")
        return p["id"]

    def create_project(self, body: dict, wait_sec: int = 1800, poll_sec: float = 5.0) -> dict:
        try:
            res = self.request("POST", "/api/v1/projects", body=body, timeout=60)[1]
            return res.get("project") or res
        except urllib.error.URLError as e:
            # write timeout / 503 / 504 handling
            pass
        except HubAPIError as e:
            if e.status not in (503, 504):
                raise
                
        slug = body.get("slug") or body.get("name")
        start = time.time()
        while time.time() - start < wait_sec:
            p = self.get_project_by_slug(slug)
            if p:
                return p
            time.sleep(poll_sec)
        raise RuntimeError("Timeout waiting for project creation")
            
    def get_project_settings(self, project_id: str) -> dict:
        return self.request("GET", f"/api/v1/projects/{project_id}/settings")[1]
        
    def put_project_settings(self, project_id: str, patch: dict) -> dict:
        current = self.get_project_settings(project_id)
        current.update(patch)
        return self.request("PUT", f"/api/v1/projects/{project_id}/settings", body=current)[1]
        
    def list_project_service_accounts(self, project_id: str) -> list[dict]:
        res = self.request("GET", f"/api/v1/projects/{project_id}/gcp-service-accounts")[1]
        return res.get("items") or res.get("serviceAccounts") or res
        
    def register_project_service_account(self, project_id: str, email: str, gcp_project: str, name: str = "") -> dict:
        for sa in self.list_project_service_accounts(project_id):
            if sa.get("email", "").lower() == email.lower():
                return sa
        body = {"email": email, "projectId": gcp_project, "name": name or email.split("@")[0]}
        return self.request("POST", f"/api/v1/projects/{project_id}/gcp-service-accounts", body=body)[1]
        
    def list_agents(self, project_id: str) -> list[dict]:
        res = self.request("GET", f"/api/v1/projects/{project_id}/agents")[1]
        return res.get("agents") or res.get("items") or res
        
    def get_agent(self, project_id: str, slug: str) -> dict | None:
        try:
            return self.request("GET", f"/api/v1/projects/{project_id}/agents/{slug}")[1]
        except HubAPIError as e:
            if e.status == 404:
                return None
            raise
            
    def ensure_agent_started(self, project_id: str, slug: str, create_body: dict | None = None, force_resume: bool = False, wait_sec: int = 180, poll_sec: float = 2.0) -> dict:
        agent = self.get_agent(project_id, slug)
        
        if not agent and create_body:
            try:
                self.request("POST", f"/api/v1/projects/{project_id}/agents", body=create_body)
            except urllib.error.URLError:
                pass
            except HubAPIError as e:
                if e.status not in (503, 504):
                    raise
        elif agent and agent.get("phase") != "running":
            body = {"forceResume": force_resume} if force_resume else {}
            try:
                self.request("POST", f"/api/v1/projects/{project_id}/agents/{slug}/start", body=body)
            except urllib.error.URLError:
                pass
            except HubAPIError as e:
                if e.status not in (503, 504):
                    raise

        start = time.time()
        while time.time() - start < wait_sec:
            agent = self.get_agent(project_id, slug)
            if agent:
                phase = agent.get("phase")
                if phase == "running":
                    return agent
                if phase == "error":
                    raise HubAPIError(500, "GET", f"/agents/{slug}", f"Agent in error phase: {agent}")
            time.sleep(poll_sec)
        raise RuntimeError("Timeout waiting for agent to start")

    def exec_agent(self, project_id: str, slug: str, command: list[str], timeout: int = 10) -> tuple[int, str]:
        body = {"command": command, "timeout": timeout}
        res = self.request("POST", f"/api/v1/projects/{project_id}/agents/{slug}/exec", body=body)[1]
        return int(res.get("exitCode", 0)), str(res.get("output", ""))

    def list_shared_dir_files(self, project_id: str, dir_name: str) -> list[dict]:
        res = self.request("GET", f"/api/v1/projects/{project_id}/shared-dirs/{dir_name}/files")[1]
        return res.get("files") or res.get("items") or res

    def sync_shared_dir(self, project_id: str, dir_name: str, src_dir: str, apply: bool = False) -> dict:
        remote_files = self.list_shared_dir_files(project_id, dir_name)
        remote_dict = {}
        for f in remote_files:
            remote_dict[f["name"]] = f

        local_files = {}
        src_path = Path(src_dir)
        for p in src_path.rglob("*"):
            if p.is_file():
                rel_path = p.relative_to(src_path).as_posix()
                local_files[rel_path] = p

        uploaded = []
        deleted = []
        unchanged = []

        # Upload modified or new files
        for rel_path, path in local_files.items():
            content = path.read_text()
            remote_f = remote_dict.get(rel_path)
            
            needs_upload = True
            if remote_f:
                remote_content = remote_f.get("content")
                if remote_content is None:
                    try:
                        remote_content = self.request("GET", f"/api/v1/projects/{project_id}/shared-dirs/{dir_name}/files/{urllib.parse.quote(rel_path, safe='')}")[1].get("content")
                    except Exception:
                        pass
                
                if remote_content == content:
                    needs_upload = False

            if needs_upload:
                uploaded.append(rel_path)
                if apply:
                    self.request("PUT", f"/api/v1/projects/{project_id}/shared-dirs/{dir_name}/files/{urllib.parse.quote(rel_path, safe='')}", body={"content": content})
            else:
                unchanged.append(rel_path)

        # Delete remote files that do not exist locally
        for rel_path in remote_dict.keys():
            if rel_path not in local_files:
                deleted.append(rel_path)
                if apply:
                    self.request("DELETE", f"/api/v1/projects/{project_id}/shared-dirs/{dir_name}/files/{urllib.parse.quote(rel_path, safe='')}")

        return {
            "uploaded": sorted(uploaded),
            "deleted": sorted(deleted),
            "unchanged": sorted(unchanged),
        }