"""The console's certificate is pinned: no request reaches a server with any other."""
import hashlib
import re
import socket
import ssl
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netbox_unifi_sync.configuration import normalize_plugin_settings, plugin_settings_to_env
from netbox_unifi_sync.services import sync_engine
from netbox_unifi_sync.services._validation import (
    SyncConfigurationError,
    validate_runtime_config,
)
from netbox_unifi_sync.services.auth import UnifiAuthSettings
from netbox_unifi_sync.services.runtime import auth_signature, to_controller_runtime
from netbox_unifi_sync.services.sync import ipam
from netbox_unifi_sync.services.unifi.pinning import (
    CertificatePinError,
    normalize_pin,
    pinned_session,
)
from netbox_unifi_sync.services.unifi.unifi import Unifi
from tests.test_review_regressions import _load_function

PACKAGE = Path(__file__).resolve().parents[1] / "netbox_unifi_sync"


class Server:
    """Count connections and keep the first bytes of each request that gets through TLS."""

    def __init__(self, certificate, key):
        self.connections, self.requests, self.replies = 0, [], []
        self.use(certificate, key)
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"https://127.0.0.1:{self.listener.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def use(self, certificate, key):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certificate, key)

    def _serve(self):
        while True:
            try:
                raw, _ = self.listener.accept()
            except OSError:
                return
            self.connections += 1
            try:
                raw.settimeout(5)
                with self.context.wrap_socket(raw, server_side=True) as stream:
                    data = stream.recv(65536)
                    if data:
                        self.requests.append(data)
                        stream.sendall(self._reply())
            except OSError:
                pass

    def _reply(self):
        if self.replies:
            return self.replies.pop(0)
        body = b'{"applicationVersion": "9", "data": []}'
        return reply(b"200 OK", b"Content-Type: application/json", body)


def reply(status, header, body=b""):
    return b"HTTP/1.1 %s\r\n%s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (
        status, header, len(body), body)


@pytest.fixture(scope="module")
def certificates(tmp_path_factory):
    folder = tmp_path_factory.mktemp("certificates")
    made = []
    for name in ("a", "b"):
        certificate, key = folder / f"{name}.pem", folder / f"{name}.key"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
             "-nodes", "-days", "2", "-subj", "/CN=127.0.0.1", "-keyout", key, "-out", certificate],
            check=True, capture_output=True)
        der = ssl.PEM_cert_to_DER_cert(certificate.read_text())
        made.append(SimpleNamespace(
            files=(certificate, key), pin=hashlib.sha256(der).hexdigest()))
    return made


@pytest.fixture
def servers(certificates):
    made = [Server(*item.files) for item in certificates]
    yield made
    for server in made:
        server.listener.close()


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    monkeypatch.setattr("netbox_unifi_sync.services.unifi.unifi.time.sleep", lambda seconds: None)
    for name in ("UNIFI_PERSIST_SESSION", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)


def client(server, pin, **credentials):
    credentials = credentials or {"api_key": "key", "allow_login_fallback": False}
    return Unifi(server.url, verify_ssl=False, cert_sha256=pin, **credentials)


LOGIN = {"username": "u", "password": "p"}


def test_accepts_the_pinned_certificate(servers, certificates):
    made = client(servers[0], certificates[0].pin)
    assert made.integration_api_base.startswith(servers[0].url)
    assert servers[0].requests


def test_accepts_a_pin_with_colons_and_capitals(servers, certificates):
    pin = certificates[0].pin.upper()
    client(servers[0], ":".join(pin[i:i + 2] for i in range(0, 64, 2)))


@pytest.mark.parametrize("credentials", [{}, LOGIN, {"api_key": "key", **LOGIN}])
def test_refuses_another_certificate_before_any_request(servers, certificates, credentials):
    with pytest.raises(CertificatePinError, match=certificates[0].pin):
        client(servers[0], certificates[1].pin, **credentials)
    assert (servers[0].connections, servers[0].requests) == (1, [])


@pytest.mark.parametrize("pin", [None, "", " ", "ab" * 20, "ab" * 16, "zz" * 32, "ab" * 33])
@pytest.mark.parametrize("credentials", [{}, LOGIN])
def test_refuses_a_missing_or_bad_pin_before_connecting(servers, pin, credentials):
    with pytest.raises(CertificatePinError, match="SHA-256"):
        client(servers[0], pin, **credentials)
    assert servers[0].connections == 0


def test_the_environment_does_not_supply_a_pin(servers, certificates, monkeypatch):
    monkeypatch.setenv("UNIFI_CERT_SHA256", certificates[0].pin)
    with pytest.raises(CertificatePinError):
        Unifi(servers[0].url, api_key="key", verify_ssl=False)


def test_refuses_a_redirect_to_another_certificate(servers, certificates):
    servers[0].replies = [reply(b"302 Found", b"Location: %s/x" % servers[1].url.encode())]
    with pytest.raises(CertificatePinError):
        pinned_session(certificates[0].pin).get(servers[0].url, verify=False, timeout=5)
    assert (servers[1].connections, servers[1].requests) == (1, [])


def test_refuses_a_redirect_to_plain_http(servers, certificates):
    plain = socket.create_server(("127.0.0.1", 0))
    target = b"http://127.0.0.1:%d/x" % plain.getsockname()[1]
    servers[0].replies = [reply(b"302 Found", b"Location: " + target)]
    with pytest.raises(CertificatePinError, match="https"):
        pinned_session(certificates[0].pin).get(servers[0].url, verify=False, timeout=5)
    plain.settimeout(0.2)
    with pytest.raises(TimeoutError):
        plain.accept()
    plain.close()


def test_refuses_plain_http(certificates):
    with pytest.raises(CertificatePinError, match="https"):
        pinned_session(certificates[0].pin).get("http://127.0.0.1:9/", timeout=5)


def test_refuses_a_retry_when_the_certificate_changed(servers, certificates):
    made = client(servers[0], certificates[0].pin)
    sent = len(servers[0].requests)

    def swap():
        servers[0].use(*certificates[1].files)
        return reply(b"503 Busy", b"X: y")

    servers[0]._reply = swap
    with pytest.raises(CertificatePinError):
        made.make_request("/sites")
    assert len(servers[0].requests) == sent + 1


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_refuses_every_api_when_the_certificate_changed(servers, certificates, method):
    made = client(servers[0], certificates[0].pin)
    made.session.cookies.set("TOKEN", "t")
    before = len(servers[0].requests)
    servers[0].use(*certificates[1].files)
    for style in ("integration", "legacy"):
        made.api_style = style
        with pytest.raises(CertificatePinError):
            made.make_request("/x", method, data={})
    with pytest.raises(CertificatePinError):
        made.configure_integration_api()
    made.username, made.password = "u", "p"
    with pytest.raises(CertificatePinError):
        made.authenticate()
    assert ipam._fetch_legacy_networkconf(made, SimpleNamespace(name="default")) is None
    assert len(servers[0].requests) == before


def test_uses_no_proxy(servers, certificates, monkeypatch):
    proxy = socket.create_server(("127.0.0.1", 0))
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{proxy.getsockname()[1]}")
    session = pinned_session(certificates[0].pin)
    assert session.get(servers[0].url, verify=False, timeout=5).ok
    with pytest.raises(CertificatePinError, match="proxy"):
        session.get(servers[0].url, verify=False, timeout=5,
                    proxies={"https": f"http://127.0.0.1:{proxy.getsockname()[1]}"})
    proxy.close()


def test_no_other_code_makes_requests():
    found = {
        str(path.relative_to(PACKAGE)): sorted(set(re.findall(
            r"requests\.(?:Session|get|post|put|patch|delete|request)\b|urlopen|http\.client"
            r"|PoolManager|\.mount\(", path.read_text())))
        for path in PACKAGE.rglob("*.py")
    }
    assert {name: uses for name, uses in found.items() if uses} == {
        "services/unifi/pinning.py": [".mount(", "requests.Session"],
        # Downloads public specs, not from the console; refused while specs_auto_refresh must be off.
        "services/unifi/spec_refresh.py": ["requests.get"],
    }


def controller(pin):
    return SimpleNamespace(name="c", base_url="https://c", auth_mode="api_key", api_key_ref="k",
                           verify_ssl=False, cert_sha256=pin)


def test_each_controller_has_its_own_pin():
    runtimes = [to_controller_runtime(controller(pin), {}) for pin in ("aa" * 32, "bb" * 32)]
    assert [runtime.cert_sha256 for runtime in runtimes] == ["aa" * 32, "bb" * 32]
    assert auth_signature(runtimes[0]) != auth_signature(runtimes[1])


@pytest.mark.parametrize("pin", ["", "ab" * 20])
def test_a_run_is_refused_when_a_controller_has_no_pin(pin):
    settings = SimpleNamespace(tenant_name="T", netbox_roles={"LAN": "Switch"})
    rows = [{"runtime": to_controller_runtime(controller(p), {})} for p in ("aa" * 32, pin)]
    with pytest.raises(SyncConfigurationError, match="Controller c"):
        validate_runtime_config(settings, {("g",): rows}, False)


def test_the_run_passes_the_pin_on(monkeypatch):
    build = _load_function("services/orchestrator.py", "_build_override", {
        "GlobalSyncSettings": object, "Any": object, "DEFAULT_ROLES": {},
        "_migrate_role_keys": lambda roles: (roles, False),
    })
    saved = Mock(netbox_roles={"LAN": "Switch"}, dhcp_ranges="")
    runtime = to_controller_runtime(controller("aa" * 32), {})
    override = build(saved, [{"runtime": runtime}], {}, cleanup_enabled=False)
    env = plugin_settings_to_env(normalize_plugin_settings(override))
    assert env["UNIFI_CERT_SHA256"] == "aa" * 32
    made = Mock()
    monkeypatch.setattr("netbox_unifi_sync.services.auth.Unifi", made)
    for mode in ("api_key", "login"):
        settings = {**override, "auth_mode": mode, "unifi_api_key": "k", "unifi_username": "u",
                    "unifi_password": "p"}
        UnifiAuthSettings.from_plugin_settings(settings).build_client(base_url="https://c")
        assert made.call_args.kwargs["cert_sha256"] == "aa" * 32


def test_no_pin_clears_one_left_in_the_environment():
    assert plugin_settings_to_env(normalize_plugin_settings({}))["UNIFI_CERT_SHA256"] == ""


@pytest.mark.parametrize("mode", ["api_key", "login"])
def test_connection_test_and_site_discovery_use_the_pin(mode):
    made = Mock()
    build = _load_function("services/orchestrator.py", "_build_unifi_client", {
        "AuthMode": SimpleNamespace(API_KEY="api_key"), "Unifi": made,
    })
    runtime = to_controller_runtime(controller("aa" * 32), {})
    build(SimpleNamespace(**{**vars(runtime), "auth_mode": mode}))
    assert made.call_args.kwargs["cert_sha256"] == "aa" * 32


def test_the_engine_uses_the_pin_and_stops_the_run_on_a_mismatch(monkeypatch):
    made = Mock(side_effect=CertificatePinError("other"))
    monkeypatch.setattr(sync_engine, "Unifi", made)
    monkeypatch.setenv("UNIFI_CERT_SHA256", "aa" * 32)
    with pytest.raises(CertificatePinError):
        sync_engine.process_all_controllers(
            ["https://c"], None, None, None, "k", None, Mock(), Mock(), Mock(), {})
    assert made.call_args.kwargs["cert_sha256"] == "aa" * 32


def test_normalizes():
    assert normalize_pin("AA:" * 31 + "AA") == "aa" * 32
