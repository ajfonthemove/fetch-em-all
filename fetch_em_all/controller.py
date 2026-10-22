"""The controller page and the local server that hands it one URL at a time.

The page navigates itself to each URL. Navigation started by a page does not bring the browser forward, and
a response saved as a download leaves the page loaded, so the page keeps polling for the next URL.
"""

import http.server
import json
import threading

PAGE = """<!doctype html>
<meta charset="utf-8"><title>Fetch 'Em All</title>
<style>body{font:15px -apple-system,sans-serif;margin:3em;color:#333}</style>
<h1>Fetch 'Em All</h1>
<p>Fetching files in the background. Leave this tab open; it closes when the run ends.</p>
<p id="status"></p>
<script>
async function loop() {
  try {
    const job = await (await fetch("/next", {cache: "no-store"})).json();
    if (job.done) { document.title = "Done"; document.getElementById("status").textContent = "Done."; return; }
    if (job.url) {
      document.getElementById("status").textContent = "Fetching " + job.label;
      location.href = job.url;
    }
  } catch (e) {}
  setTimeout(loop, 1000);
}
loop();
</script>"""


class Controller:
    """One pending URL at a time; `taken` is set once the page has picked it up."""

    def __init__(self):
        self.lock, self.job, self.done = threading.Lock(), None, False
        self.taken = threading.Event()

    def offer(self, url, label):
        with self.lock:
            self.job = {"url": url, "label": label}
            self.taken.clear()

    def finish(self):
        with self.lock:
            self.job, self.done = None, True

    def next_job(self):
        with self.lock:
            if self.done:
                return {"done": True}
            job, self.job = self.job, None
        if job:
            self.taken.set()
        return job or {}


def serve(controller, port):
    """Start the controller server on 127.0.0.1:port (0 = any free port) in a background thread."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/next"):
                body, ctype = json.dumps(controller.next_job()).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
