#!/usr/bin/env python3
"""One-command isolated browser proof. Full mode fails closed when lanes are missing."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path(os.getenv("HOUSE_E2E_SCRATCH", "/Volumes/Neural/dishwatch-scratch/houseleague/laneE"))
PYTHON = Path("/Volumes/Neural/WashDishes/.venv/bin/python")


def require(value, message):
    if not value:
        raise RuntimeError(message)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ready(url, ca, process=None):
    import httpx

    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process and process.poll() is not None:
            raise RuntimeError("Jetson app exited during startup; inspect app.log")
        try:
            if httpx.get(url, timeout=1, trust_env=False, verify=ssl.create_default_context(cafile=ca)).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Server did not become ready: " + url)


def locate(option, candidates, message):
    if option:
        candidates = [Path(option)]
    found = next((p.resolve() for p in candidates if p.exists()), None)
    require(found, message)
    return found


def main():
    # Keep imports from generating __pycache__ alongside the committed harness.
    sys.dont_write_bytecode = True
    import httpx
    import jwt
    import uvicorn
    from cryptography.hazmat.primitives import serialization
    from fake_auth import FakeAuth
    from static_site import server as site_server
    from tls import certificates

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--foundation", action="store_true", help="Run only currently available infrastructure checks")
    parser.add_argument("--playwright", default=os.getenv("HOUSE_E2E_PLAYWRIGHT"), help="Existing playwright-core package directory")
    parser.add_argument("--supabase-js", default=os.getenv("HOUSE_E2E_SUPABASE_JS"), help="Existing supabase-js browser ES module")
    parser.add_argument("--site-config", help="Scratch JS config template; substitutes AUTH_URL, API_URL and ANON_KEY")
    parser.add_argument("--config-path", default="/config.js")
    parser.add_argument("--ui-contract", help="JSON selector overrides for the merged site's accessible labels")
    args = parser.parse_args()
    os.umask(0o077)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    run = SCRATCH / (time.strftime("run-%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3))
    run.mkdir()
    report = {"checkout": str(REPO), "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
              "harness_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.iterdir() if p.suffix in (".py", ".mjs")},
              "mode": "foundation" if args.foundation else "full", "executed": [], "pending": [], "result": "failed"}
    product = None
    auth_server = None
    static = None
    threads = []
    try:
        playwright = locate(args.playwright, sorted(Path("/Volumes/Neural/NexaRad/node_modules/.pnpm").glob("playwright-core@*/node_modules/playwright-core")),
                            "No existing playwright-core. Set HOUSE_E2E_PLAYWRIGHT to an installed package under /Volumes/Neural.")
        sdk = locate(args.supabase_js, [REPO / "house-site/vendor/supabase-js-2.118.0.js"],
                     "No vendored supabase-js ES module. Set HOUSE_E2E_SUPABASE_JS to an existing browser module.")
        ports = set()
        while len(ports) < 4:
            ports.add(port())
        site_port, auth_port, app_port, tunnel_port = sorted(ports)
        tls = certificates(run)
        verify = ssl.create_default_context(cafile=tls["ca"])
        site_url, auth_url, app_url = [f"https://127.0.0.1:{p}" for p in (site_port, auth_port, app_port)]
        tunnel_url = f'https://127.0.0.1:{tunnel_port}'
        auth = FakeAuth(auth_url, site_url)
        # Every server is loopback-only, using the same throwaway certificate.
        auth_server = uvicorn.Server(uvicorn.Config(auth.app, host="127.0.0.1", port=auth_port, log_level="error",
                                                  ssl_certfile=tls["cert"], ssl_keyfile=tls["key"]))
        threads.append(threading.Thread(target=auth_server.run, daemon=True))
        config_js = None
        if args.site_config:
            config_js = Path(args.site_config).read_text().replace("AUTH_URL", auth_url).replace("API_URL", tunnel_url).replace("ANON_KEY", auth.anon_key)
        static = site_server(REPO / "house-site", sdk, auth_url, tunnel_url, auth.anon_key, args.config_path, config_js, site_port)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls["cert"], tls["key"])
        static.socket = context.wrap_socket(static.socket, server_side=True)
        threads.append(threading.Thread(target=static.serve_forever, daemon=True))
        for thread in threads:
            thread.start()
        ready(auth_url + "/auth/v1/settings", tls["ca"])
        try:
            httpx.get(auth_url + "/auth/v1/settings", trust_env=False)
        except httpx.ConnectError as exc:
            require("CERTIFICATE_VERIFY_FAILED" in str(exc), "Expected untrusted TLS certificate refusal")
        else:
            raise RuntimeError("Throwaway certificate was trusted without the harness CA")
        report["executed"].append("HTTPS with throwaway CA; untrusted client refuses certificate; no production origin exception")
        clock_file = run / "clock-offset-seconds"
        clock_file.write_text("0")
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(run),
               "HOUSE_E2E_CLOCK_FILE": str(clock_file),
               "HOUSE_E2E_TLS_CERT": tls["cert"], "HOUSE_E2E_TLS_KEY": tls["key"],
               "SSL_CERT_FILE": tls["ca"], "NODE_EXTRA_CA_CERTS": tls["ca"],
               "HOUSE_E2E_DATA": str(run / "data"), "HOUSE_E2E_PORT": str(app_port), "HOUSE_E2E_TUNNEL_PORT": str(tunnel_port),
               "WASHDISHES_DATA_DIR": str(run / "data"), "WASHDISHES_ACCESS_KEY": secrets.token_urlsafe(24),
               "WASHDISHES_TIMEZONE": "America/Los_Angeles", "SUPABASE_URL": auth_url,
               "SUPABASE_SECRET_KEY": auth.admin_key, "SUPABASE_JWT_SECRET": "",
               "SUPABASE_ANON_KEY": auth.anon_key, "HOUSE_SITE_ORIGIN": site_url, "HOUSE_PUBLIC_ORIGIN": tunnel_url}
        # Exclude inherited proxy settings; both harness and app only contact loopback.
        env = {k: v for k, v in env.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        with (run / "app.log").open("w") as log:
            product = subprocess.Popen([str(PYTHON), "-B", str(Path(__file__).with_name("boot.py"))], cwd=REPO, env=env, stdout=log, stderr=log)
        ready(app_url + "/api/health", tls["ca"], product)
        fixture = json.loads((run / "fixture.json").read_text())
        require(len(fixture["residents"]) == 10, "Seed must contain ten residents")
        require(fixture["visits"][0]["points"] == 4, "Multi-dish fixture must score four points")
        report["executed"].extend(["real app started with workers disabled", "10 residents, 3 current visits, 1 closed-week visit and synthetic JPEGs seeded"])
        with httpx.Client(base_url=app_url, trust_env=False, verify=verify) as client:
            login = client.post("/api/login", json={"password": env["WASHDISHES_ACCESS_KEY"]})
            require(login.status_code == 200, "Operator session login failed: " + login.text)
            csrf = login.json()["csrf_token"]
            dashboard = client.get("/api/dashboard")
            require(dashboard.status_code == 200, "Operator dashboard unavailable")
            data = dashboard.json()
            require(len(data["residents"]) == 10, "Dashboard must read back ten seeded residents")
            require(len(data["obligations"]) == 6, "Dashboard must read back six seeded dishes")
            for visit in fixture["visits"]:
                evidence = client.get("/api/evidence/" + visit["evidence_id"])
                require(evidence.status_code == 200 and evidence.headers.get("content-type", "").startswith("image/"),
                        "Seeded arrival JPEG did not round-trip through the real media endpoint")
            clock_file.write_text("120")
            try:
                shifted = client.get("/api/dashboard").json()["server_time"]
                from datetime import datetime
                require((datetime.fromisoformat(shifted) - datetime.fromisoformat(data["server_time"])).total_seconds() >= 120,
                        "Isolated product clock did not advance")
            finally:
                clock_file.write_text("0")
            operator_cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        report["executed"].append("operator cookie login and dashboard read")
        report["executed"].append("synthetic JPEGs served through authenticated media; isolated server wall clock advanced/restored")
        with httpx.Client(base_url=auth_url, trust_env=False, verify=verify) as client:
            admin = {"Authorization": "Bearer " + auth.admin_key, "apikey": auth.admin_key}
            created = client.post("/auth/v1/admin/users", headers=admin,
                                  json={"email": "probe@house-e2e.invalid", "password": "first-password", "email_confirm": True})
            require(created.status_code == 200, "Fake Admin create failed")
            user = created.json()
            changed = client.put("/auth/v1/admin/users/" + user["id"], headers=admin, json={"password": "probe-password"})
            require(changed.status_code == 200, "Fake Admin password update failed")
            jwks = client.get("/auth/v1/.well-known/jwks.json").json()
            key = jwt.PyJWK.from_dict(jwks["keys"][0]).key
            claims = jwt.decode(auth.token(user), key, algorithms=["ES256"], audience="authenticated", issuer=auth_url + "/auth/v1")
            require(claims["sub"] == user["id"], "JWKS signature verification failed")
            for token in [auth.token(user, seconds=-10), auth.token(user, audience="wrong")]:
                require(client.get("/auth/v1/user", headers={"Authorization": "Bearer " + token}).status_code == 401, "Fake Auth accepted invalid claims")
        report["executed"].append("fake Admin create/update; real ES256 JWT verified against served JWKS; expired/wrong-audience refused")
        private_key = run / "auth-private.pem"
        private_key.write_bytes(auth.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ui = json.loads(Path(args.ui_contract).read_text()) if args.ui_contract else {}
        browser_config = {"run": str(run), "playwright": str(playwright), "site": site_url, "auth": auth_url, "api": tunnel_url, "privateApi": app_url,
                          "anon": auth.anon_key, "fixture": fixture, "operatorCookie": operator_cookie, "csrf": csrf,
                          "privateKey": str(private_key), "clockFile": str(clock_file), "kid": auth.kid, "ui": ui, "tlsSpki": tls["spki"]}
        config = run / "browser.json"
        config.write_text(json.dumps(browser_config))

        def browser(mode):
            driver = subprocess.Popen(["node", str(Path(__file__).with_name("browser.mjs")), str(config), mode], cwd=REPO,
                                      env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            try:
                stdout, stderr = driver.communicate(timeout=240)
                (run / (mode + ".log")).write_text(stdout + stderr)
                print(stdout, end="", flush=True)
                require(driver.returncode == 0, f"{mode} browser failed: {stderr.strip()} (see {run / (mode + '.log')})")
                report.setdefault("browser_steps", {})[mode] = json.loads((run / (mode + "-steps.json")).read_text())
            finally:
                # Own the process group so even a driver timeout cannot orphan Chrome.
                try:
                    os.killpg(driver.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    driver.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(driver.pid, signal.SIGKILL)
                    driver.wait()

        browser("foundation")
        report["executed"].append("real supabase-js in 390×844 Chrome over CDP: login, getUser, refresh, wrong-password refusal, logout; screenshots")
        missing = []
        if not (REPO / "house-site/index.html").exists():
            missing.append("Lane C house-site/index.html")
        if not fixture["house_api"]:
            missing.append("Lane B /house/api/week")
        if args.foundation or missing:
            report["pending"] = ["Full UI journey: join Jadon/Andrew/Daniel, camera guess, exact scores/pick, disputes/settlement, closed week, auth/media/tunnel refusals"]
            if missing:
                report["pending"].append("Merge prerequisites: " + ", ".join(missing))
            report["result"] = "foundation-passed; full-pending"
            print("PASS foundation; PENDING full journey: " + (", ".join(missing) or "--foundation selected"), flush=True)
            return 0 if args.foundation else 2
        jwks_before = sum(c["path"] == ".well-known/jwks.json" for c in auth.calls)
        browser("journey")
        report["executed"].append("full merged-product browser journey and security assertions")
        require(sum(c["path"] == ".well-known/jwks.json" for c in auth.calls) > jwks_before, "Product did not request fake Auth JWKS")
        report["result"] = "passed"
        print("PASS full house E2E", flush=True)
        return 0
    except Exception as exc:
        report["error"] = str(exc)
        print("FAIL: " + str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        if product and product.poll() is None:
            product.terminate()
            try:
                product.wait(timeout=10)
            except subprocess.TimeoutExpired:
                product.kill()
                product.wait()
        if static:
            static.shutdown()
            static.server_close()
        if auth_server:
            auth_server.should_exit = True
        for thread in threads:
            thread.join(timeout=5)
        report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        (run / "report.json").write_text(json.dumps(report, indent=2))
        (SCRATCH / "latest-run.txt").write_text(str(run) + "\n")
        print("Evidence: " + str(run), flush=True)


if __name__ == "__main__":
    sys.exit(main())
