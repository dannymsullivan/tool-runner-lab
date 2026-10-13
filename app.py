"""Private, single-user learning server. Start with: python app.py."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import anthropic
import lab
import notebook_lab
import session_state

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
EXERCISES = {
    "inventory": ("inventory_tool.py", "Complete the typed inventory function. Normalize product names, preserve zero stock, and distinguish an unknown product."),
    "runner": ("runner_exercise.py", "Return the real SDK Tool Runner with the supplied model, tools, and user message. Use max_tokens=512 and max_iterations=4."),
}

TUTORIAL_URL = re.compile(r"/tutorials/[A-Za-z0-9][A-Za-z0-9._-]*\.ipynb\Z")


def tutorial_manifest():
    # Load the series only when requested, so its availability cannot break the
    # existing support bot and executable notebook endpoints.
    import tutorial_series
    return tutorial_series.manifest()


def tutorial_catalog():
    """Map explicitly published URLs to confined notebook files."""
    manifest = tutorial_manifest()
    directory = ROOT.resolve() / "tutorials"
    files = {}
    for module in manifest["modules"]:
        for field in ("notebook", "reference"):
            url = module[field]
            if not isinstance(url, str) or not TUTORIAL_URL.fullmatch(url):
                raise ValueError("Invalid tutorial URL.")
            declared = directory / url.rsplit("/", 1)[1]
            target = declared.resolve()
            # An exact URL whitelist also excludes symlinks outside tutorials.
            if target != declared or url in files:
                raise ValueError("Invalid tutorial file.")
            files[url] = target
    return manifest, files


class Handler(BaseHTTPRequestHandler):
    server_version = "SupportAgentLab/1.0"

    def log_message(self, format, *args):
        # Never log request bodies, credentials, or provider exception details.
        logging.info("%s %s", self.command, urlsplit(self.path).path)

    def session(self):
        if not hasattr(self, "_session"):
            self._token, self._session, self._new_session = session_state.get_session(self.headers.get("Cookie", ""))
        return self._session

    def headers_for(self, code, content_type, length=None, filename=None):
        self.session()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        if self._new_session:
            # Same-origin only. Codespaces supplies TLS at its private proxy.
            secure = "; Secure" if os.environ.get("CODESPACE_NAME") else ""
            self.send_header("Set-Cookie", f"lab_session={self._token}; Path=/; HttpOnly; SameSite=Strict{secure}")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def json_response(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.headers_for(code, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def config(self):
        config = lab.get_config()
        session = self.session()
        with session.lock:
            connected = bool(session.credentials)
            config.update(connected=connected, models=list(session.models),
                          connection_source="session" if connected else ("environment" if config["live_available"] else None))
            if connected:
                config.update(live_available=True, model_access_verified=True,
                              default_model=session.credentials["model"])
        config.update(session_state.source_config())
        config["live_notice"] = "Optional live runs use your Anthropic API billing. The full course works without a key."
        return config

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/api/config":
            return self.json_response(200, self.config())
        if path == "/api/health":
            return self.json_response(200, {"status": "ok"})
        if path == "/api/curriculum":
            return self.json_response(200, lab.get_curriculum())
        if path == "/api/notebook":
            return self.json_response(200, notebook_lab.curriculum())
        if path == "/api/tutorials" or TUTORIAL_URL.fullmatch(path):
            try:
                manifest, files = tutorial_catalog()
                target = files.get(path)
                body = target.read_bytes() if target is not None else None
            except Exception:
                logging.error("Tutorial content unavailable; internal details omitted.")
                return self.json_response(500, {"error": "This tutorial is unavailable. Try again later."})
            if path == "/api/tutorials":
                return self.json_response(200, manifest)
            if target is None:
                return self.json_response(404, {"error": "Not found"})
            self.headers_for(200, "application/x-ipynb+json; charset=utf-8", len(body), target.name)
            self.wfile.write(body)
            return
        if path == "/tool-runner-lab.ipynb":
            body = (ROOT / "tool-runner-lab.ipynb").read_bytes()
            self.headers_for(200, "application/x-ipynb+json; charset=utf-8", len(body), "tool-runner-lab.ipynb")
            self.wfile.write(body)
            return
        if path == "/api/report":
            return self.json_response(200, self.session().report())
        if path == "/api/exercise":
            exercise = parse_qs(parsed.query).get("exercise", [""])[0]
            if exercise not in EXERCISES:
                return self.json_response(400, {"error": "Choose the inventory or runner exercise."})
            filename, instruction = EXERCISES[exercise]
            return self.json_response(200, {
                "exercise": exercise, "file": "exercises/" + filename, "instructions": instruction,
                "starter": (ROOT / "exercises" / filename).read_text(),
                "solution": (ROOT / "exercises" / "solutions" / filename).read_text(),
            })
        asset = ASSETS.get(path)
        if asset is None:
            return self.json_response(404, {"error": "Not found"})
        name, content_type = asset
        body = (WEB / name).read_bytes()
        self.headers_for(200, content_type, len(body))
        self.wfile.write(body)

    def do_POST(self):
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if (origin and urlsplit(origin).netloc != host) or self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.json_response(403, {"error": "Use the lab's own browser tab."})
        if self.headers.get_content_type() != "application/json":
            return self.json_response(415, {"error": "Expected a JSON request."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("Request must be between 1 and 16384 bytes.")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("Expected an object.")
        except (ValueError, UnicodeDecodeError):
            return self.json_response(400, {"error": "Send a JSON object smaller than 16 KB."})
        path, session = urlsplit(self.path).path, self.session()
        if path == "/api/notebook-cell":
            # This endpoint accepts only a known cell ID, never executable text
            # or credentials. Every request gets fresh, isolated prerequisites.
            deep_ids = [cell["id"] for cell in notebook_lab.DEEP_CELLS]
            expected = {"cell_id", "variant"} if payload.get("cell_id") in deep_ids else {"cell_id"}
            if set(payload) != expected:
                return self.json_response(400, {"error": "Send only a known cell ID and, for experiments, a variant ID."})
            try:
                result = notebook_lab.run_cell(payload["cell_id"], payload.get("variant"))
                return self.json_response(200, result)
            except ValueError:
                return self.json_response(400, {"error": "Choose a known notebook cell and valid variant."})
            except Exception:
                logging.error("Notebook cell failed; internal details omitted.")
                return self.json_response(500, {"error": "This cell could not finish. Try it again."})
        if path == "/api/connect":
            return self.connect(payload)
        if path == "/api/disconnect":
            with session.lock:
                session.credentials, session.models = None, []
            return self.json_response(200, {"connected": False})
        if path == "/api/model":
            model = payload.get("model")
            with session.lock:
                if not session.credentials or model not in [item["id"] for item in session.models]:
                    return self.json_response(400, {"error": "Connect your key and choose an available model."})
                session.credentials["model"] = model
            return self.json_response(200, {"connected": True, "model": model})
        if path == "/api/checks":
            return self.json_response(200, {"checks": lab.run_checks()})
        if path == "/api/exercise":
            return self.check_exercise(payload)
        if path == "/api/checkpoint":
            try:
                with session.lock:
                    result = lab.evaluate_checkpoint(payload.get("step_id"), {
                        "answers": payload.get("answers", {}), "runs": list(session.runs),
                        "exercises": dict(session.exercises),
                    })
                    session.checkpoints[result["step_id"]] = result
                return self.json_response(200, result)
            except (ValueError, TypeError):
                return self.json_response(400, {"error": "Choose a valid course checkpoint."})
        if path != "/api/run":
            return self.json_response(404, {"error": "Not found"})
        if not session.run_lock.acquire(blocking=False):
            return self.json_response(409, {"error": "Wait for this session's current run to finish."})
        self.headers_for(200, "application/x-ndjson; charset=utf-8")

        def send(value):
            self.wfile.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

        try:
            with session.lock:
                credentials = dict(session.credentials) if session.credentials else None
            result = lab.run_lab(payload, lambda event: send({"kind": "event", "event": event}), credentials=credentials)
            with session.lock:
                session.runs.append(result)
                session.runs[:] = session.runs[-80:]
            send({"kind": "result", "result": result})
        except (BrokenPipeError, ConnectionResetError):
            return
        except ValueError as exc:
            send({"kind": "error", "error": str(exc)})
        except Exception:
            logging.error("Lab run failed; provider details omitted to protect credentials.")
            send({"kind": "error", "error": "This run could not finish. Check the connection and selected model, then try again."})
        finally:
            session.run_lock.release()

    def connect(self, payload):
        key = payload.get("api_key", "")
        if not isinstance(key, str) or not 8 <= len(key.strip()) <= 1024 or any(c.isspace() for c in key.strip()):
            return self.json_response(400, {"error": "Enter your Anthropic API key without spaces."})
        try:
            with anthropic.Anthropic(api_key=key.strip(), max_retries=0, timeout=15) as client:
                models = [{"id": item.id, "display_name": item.display_name}
                          for item in client.models.list(limit=100, lifecycle=["active"]).data]
            if not models:
                return self.json_response(400, {"error": "This key did not return any available models."})
            available = [item["id"] for item in models]
            if payload.get("model") and payload["model"] not in available:
                return self.json_response(400, {"error": "That model is not available for this key. Connect without a model selection to see available choices."})
            preferred = payload.get("model") or lab.get_config()["default_model"]
            model = preferred if preferred in available else available[0]
            session = self.session()
            with session.lock:
                session.credentials = {"api_key": key.strip(), "model": model}
                session.models = models
            return self.json_response(200, {"connected": True, "model": model, "models": models})
        except anthropic.AuthenticationError:
            return self.json_response(401, {"error": "That key was not accepted. Check it in the Anthropic console."})
        except Exception:
            return self.json_response(502, {"error": "Could not verify model access. Check your connection and API key, then retry."})

    def check_exercise(self, payload):
        exercise = payload.get("exercise")
        if not isinstance(exercise, str) or exercise not in EXERCISES:
            return self.json_response(400, {"error": "Choose the inventory or runner exercise."})
        # Executes only fixed, learner-owned workspace files, with no API key in
        # its environment. Local Python execution is not an untrusted-code sandbox.
        env = {name: value for name, value in os.environ.items()
               if name in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
        try:
            completed = subprocess.run(
                [sys.executable, str(ROOT / "exercises" / "check.py"), "--exercise", exercise, "--json"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=12,
            )
            result = json.loads(completed.stdout)
            if completed.returncode or not isinstance(result, dict) or not isinstance(result.get("passed"), bool):
                raise ValueError("Invalid checker result")
        except (subprocess.TimeoutExpired, ValueError, OSError):
            result = {"passed": False, "checks": [], "feedback": "The exercise could not finish. Check your saved Python for syntax errors or a loop that does not stop, then retry."}
        session = self.session()
        with session.lock:
            session.exercises[exercise] = result
            # A newly failing edit should not retain an old completion badge.
            step_id = "tool" if exercise == "inventory" else "runner"
            if not result["passed"]:
                session.checkpoints.pop(step_id, None)
        return self.json_response(200, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Support Agent Lab is running at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
