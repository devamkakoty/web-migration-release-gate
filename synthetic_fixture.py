"""Original synthetic routes. Binds only to an OS-assigned loopback port."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from threading import Thread

HOME = b"""<!doctype html><html><head><title>Willow Studio | Home</title></head>
<body><h1>Willow Studio</h1><a id="book" class="cta primary" href="/contact">Book a call</a></body></html>"""
CONTACT = b"""<!doctype html><html><head><title>Contact | Willow Studio</title></head>
<body><h1>Contact</h1><form id="contact-form"><input name="email" type="email">
<button type="submit">Send</button></form></body></html>"""
ROUTES = {
    "/old-home": (301, "/home", b""),
    "/old-contact": (302, "/contact-step", b""),
    "/contact-step": (308, "/contact", b""),
    "/home": (200, None, HOME),
    "/contact": (200, None, CONTACT),
    "/gone": (410, None, b"<html><title>Retired</title></html>"),
    "/wrong-status": (503, None, b"<html><title>Unavailable</title></html>"),
    "/missing-title": (200, None, b"<html><title>Default template</title></html>"),
    "/missing-cta": (200, None, b"<html><title>Contact</title><p>Form missing</p></html>"),
    "/drift": (301, "/home", b""),
    "/external": (302, "https://not-authorized.invalid/collect", b""),
    "/loop-a": (301, "/loop-b", b""),
    "/loop-b": (302, "/loop-a", b""),
    "/no-location": (301, None, b""),
    "/unsafe-location": (302, "/home#fragment", b""),
    "/large": (200, None, b"x" * (128 * 1024 + 1)),
    "/not-html": (200, None, b'{"message":"Contact"}'),
}


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.requests: list[str] = []

    def server_bind(self):
        # HTTPServer's default bind performs a hostname lookup; this fixture needs none.
        TCPServer.server_bind(self)
        self.server_name = "synthetic-loopback"
        self.server_port = self.server_address[1]


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append(self.path)
        if self.path == "/drop":
            self.close_connection = True
            return
        status, location, body = ROUTES.get(
            self.path, (404, None, b"<html><title>Not found</title></html>")
        )
        self.send_response(status)
        if location is not None:
            self.send_header("Location", location)
        self.send_header("Content-Type", "application/json" if self.path == "/not-html"
                         else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


@contextmanager
def fixture():
    server = FixtureServer()
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01},
                    name="synthetic-http-fixture")
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("Synthetic fixture did not shut down.")
