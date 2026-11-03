"""Static site with runtime-only local config and a real supabase-js probe page."""
from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROBE = b'''<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>House E2E Auth</title><style>body{font:20px system-ui;background:#182630;color:white;padding:24px}button,input{display:block;font:inherit;margin:16px 0;max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}</style>
<h1>Synthetic auth check</h1><p>Real supabase-js, local Auth only.</p>
<input aria-label="Email" id="email"><input aria-label="Password" id="password" type="password">
<button id="login">Sign in</button><button id="user">Get user</button><button id="refresh">Refresh</button>
<button id="logout">Sign out</button><pre id="result">Ready</pre>
<script type="module" src="/__e2e__/probe.js"></script></html>'''


def server(site: Path, sdk: Path, auth: str, api: str, anon: str, config_path: str, config_js: str | None, port: int):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/__e2e__/auth.html":
                return self.send(PROBE, "text/html")
            if path == "/__e2e__/supabase.js":
                return self.send(sdk.read_bytes(), "text/javascript")
            if path == "/__e2e__/probe.js":
                script = ('import {createClient} from "/__e2e__/supabase.js";\n'
                          f"const client = createClient({json.dumps(auth)}, {json.dumps(anon)});\n")
                script += """
window.authClient = client;
const show = value => document.querySelector('#result').textContent = JSON.stringify(value);
document.querySelector('#login').onclick = async () => show(await client.auth.signInWithPassword({email:document.querySelector('#email').value,password:document.querySelector('#password').value}));
document.querySelector('#user').onclick = async () => show(await client.auth.getUser());
document.querySelector('#refresh').onclick = async () => show(await client.auth.refreshSession());
document.querySelector('#logout').onclick = async () => {show(await client.auth.signOut());};
"""
                return self.send(script.encode(), "text/javascript")
            if path == config_path:
                script = config_js or (f"export const SUPABASE_URL = {json.dumps(auth)};\n"
                                       f"export const SUPABASE_PUBLISHABLE_KEY = {json.dumps(anon)};\n"
                                       f"export const HOUSE_API_ORIGIN = {json.dumps(api)};\n")
                return self.send(script.encode(), "text/javascript")
            # /join is a static SPA route, never a request to real hosting.
            if path in ("/join", "/name", "/mine", "/past"):
                self.path = "/index.html"
            return super().do_GET()

        def send(self, body, mime):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(site)))
