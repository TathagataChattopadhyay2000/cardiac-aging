"""Run the MS Target Finder on the user's computer."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit
import webbrowser

import msi


class State:
    def __init__(self, root, cache):
        self.root = root
        self.cache = cache
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(24)
        self.dataset = None
        self.job = None

    def defaults(self):
        config = self.root / "config.local.json"
        if not config.exists():
            config = self.root / "config.example.json"
        settings = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        data = settings.get("data_dir", "")
        if data and not Path(data).is_absolute():
            data = str(self.root / data)
        legacy = Path.home() / "Documents/Codex/2026-11-03/can-x20/work/full-msi"
        sheet = self.root / settings.get("sample_sheet", "metadata/samples.csv")
        return {"data": data,
                "sheet": str(sheet),
                "legacy": str(legacy) if legacy.is_dir() else "",
                "autoload": bool(data and Path(data).is_dir() and sheet.is_file() and legacy.is_dir()),
                "token": self.token}

    def snapshot(self):
        with self.lock:
            if self.job is None:
                return {"status": "idle"}
            return self.job.copy()

    def start(self, kind, action):
        with self.lock:
            if self.job and self.job["status"] == "running":
                raise ValueError("An operation is already running. Cancel it or wait for it to finish.")
            job = {"status": "running", "kind": kind, "message": "Starting…", "progress": 0}
            self.job = job
            self.cancel = threading.Event()

        def report(message, progress):
            if self.cancel.is_set():
                raise InterruptedError("Cancelled.")
            with self.lock:
                job.update(message=message, progress=progress)

        def run():
            try:
                result = action(report)
                if kind == "study":
                    if self.cancel.is_set():
                        raise InterruptedError("Cancelled.")
                    self.dataset = result
                    result = self.public_study(result)
                with self.lock:
                    job.update(status="complete", progress=1, result=result)
            except (ValueError, OSError, KeyError, InterruptedError, msi.ET.ParseError) as error:
                with self.lock:
                    job.update(status="failed", message=str(error))
            except Exception as error:
                with self.lock:
                    job.update(status="failed", message=f"{type(error).__name__}: {error}")
        threading.Thread(target=run, daemon=True).start()
        return {"status": "running"}

    @staticmethod
    def public_study(dataset):
        return {**{k: v for k, v in dataset.items() if k != "samples"},
                "samples": [sample.public() for sample in dataset["samples"]]}

    def mapped_window(self, sample, lo, hi, report):
        fingerprint = [sample.id, sample.layout.fingerprint(), lo, hi]
        key = hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
        folder = self.cache / "maps"
        cached = folder / f"{key}.json"
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        result = msi.ion_map(sample, lo, hi, report)
        report("Saving map to the local cache", 1)
        folder.mkdir(parents=True, exist_ok=True)
        temporary = cached.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
        temporary.replace(cached)
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "TargetFinder"
    files = {"/": ("index.html", "text/html; charset=utf-8"),
             "/styles.css": ("styles.css", "text/css; charset=utf-8"),
             "/app.js": ("app.js", "text/javascript; charset=utf-8")}

    def reply(self, payload, status=200, mime="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'self'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def valid_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}",
                                             f"localhost:{self.server.server_port}"}

    def do_GET(self):
        if not self.valid_host():
            return self.reply({"error": "Unexpected host."}, 403)
        path = urlsplit(self.path).path
        state = self.server.state
        if path in self.files:
            name, mime = self.files[path]
            return self.reply((Path(__file__).parent / "static" / name).read_bytes(), mime=mime)
        if path == "/api/defaults":
            return self.reply(state.defaults())
        if path == "/api/job":
            return self.reply(state.snapshot())
        return self.reply({"error": "Not found."}, 404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        state = self.server.state
        if (not self.valid_host() or (origin and origin != f"http://{self.headers['Host']}")
                or not secrets.compare_digest(self.headers.get("X-Local-Token", ""), state.token)):
            return self.reply({"error": "Open this app from its local address."}, 403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if size < 1 or size > 100_000:
                raise ValueError("Invalid request size.")
            body = json.loads(self.rfile.read(size))
            path = urlsplit(self.path).path
            self.reply(self.dispatch(path, body))
        except (ValueError, KeyError, OSError, TypeError, msi.ET.ParseError) as error:
            self.reply({"error": str(error)}, 400)

    def dispatch(self, path, body):
        state = self.server.state
        if path == "/api/pick":
            try:
                return {"path": self.pick(body["kind"])}
            except (ImportError, RuntimeError) as error:
                raise ValueError("The file picker is unavailable. Paste the path into the field.") from error
        if path == "/api/cancel":
            with state.lock:
                if state.job and state.job["status"] == "running":
                    state.cancel.set()
            return {"ok": True}
        if path == "/api/load":
            if not body["data"].strip() or not body["sheet"].strip():
                raise ValueError("Select the export folder and sample sheet first.")
            folder, sheet = (Path(body[key]).expanduser().resolve() for key in ("data", "sheet"))
            legacy = Path(body["legacy"]).expanduser().resolve() if body.get("legacy") else None
            return state.start("study", lambda report: msi.study(folder, sheet, state.cache, legacy, report))

        dataset = state.dataset
        if not dataset or dataset["key"] != body.get("study"):
            raise ValueError("Load the study first.")
        if path == "/api/rank":
            return msi.rank(dataset, body["reference"], body["comparison"], float(body["width"]))
        if path == "/api/detail":
            return msi.detail(dataset, body["reference"], body["comparison"],
                              *(float(body[k]) for k in ("lo", "hi", "viewLo", "viewHi")))
        sample = next((s for s in dataset["samples"] if s.id == body.get("sample")), None)
        if sample is None:
            raise ValueError("Choose a valid section.")
        if path == "/api/map":
            lo, hi = float(body["lo"]), float(body["hi"])
            return state.start("map", lambda report: state.mapped_window(sample, lo, hi, report))
        if path == "/api/local":
            return msi.local_spectrum(sample, int(body["x"]), int(body["y"]),
                                      *(float(body[k]) for k in ("lo", "hi", "center", "isolation")))
        raise ValueError("Unknown request.")

    @staticmethod
    def pick(kind):
        import tkinter as tk
        from tkinter import filedialog
        dialog = tk.Tk()
        dialog.withdraw()
        dialog.attributes("-topmost", True)
        try:
            if kind in ("data", "legacy"):
                return filedialog.askdirectory(parent=dialog)
            if kind == "sheet":
                return filedialog.askopenfilename(parent=dialog, filetypes=[("CSV sample sheet", "*.csv")])
            raise ValueError("Unknown file type.")
        finally:
            dialog.destroy()

    def log_message(self, format, *args):
        if args and len(args) > 1 and str(args[1]).startswith(("4", "5")):
            super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as error:
        parser.exit(1, f"Could not start the local app on port {args.port}: {error}\n")
    server.state = State(root, args.cache or root / "results/msms_target_finder/cache")
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"MS Target Finder · {url}\nKeep this window open while using the app.", flush=True)
    if not args.no_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
