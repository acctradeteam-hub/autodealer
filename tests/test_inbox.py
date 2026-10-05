import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lot_analyzer import inbox


class FakeGitHub(BaseHTTPRequestHandler):
    private = True
    files: dict = {}
    puts: list = []

    def log_message(self, *a):
        pass

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/repos/me/inbox":
            return self._json(200, {"private": FakeGitHub.private})
        if self.path in FakeGitHub.files:
            return self._json(200, {"sha": FakeGitHub.files[self.path]})
        return self._json(404, {"message": "Not Found"})

    def do_PUT(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeGitHub.puts.append((self.path, data.get("sha")))
        FakeGitHub.files[self.path] = f"sha{len(FakeGitHub.puts)}"
        return self._json(201, {})


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.old_api = inbox.API
        inbox.API = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        inbox.SETTINGS_PATH, inbox.SENT_PATH = self.dir / "inbox.json", self.dir / "sent.json"
        FakeGitHub.private, FakeGitHub.files, FakeGitHub.puts = True, {}, []
        self.file = self.dir / "ACV_ACV_Auctions_2026-10-05_1000.html"
        self.file.write_text("<html>acv</html>", encoding="utf-8")

    def tearDown(self):
        inbox.API = self.old_api
        self.server.shutdown()
        self.tmp.cleanup()

    def test_off_by_default(self):
        self.assertEqual(inbox.send_new([self.file]), 0)
        self.assertEqual(FakeGitHub.puts, [])

    def test_never_sends_to_public_repo(self):
        FakeGitHub.private = False
        inbox.save_settings("me/inbox", "secret-token-123", True)
        self.assertEqual(inbox.send_new([self.file]), 0)
        self.assertEqual(FakeGitHub.puts, [])
        self.assertIn("ОТКРЫТЫЙ", inbox.state()["error"])

    def test_sends_once_and_again_when_changed(self):
        inbox.save_settings("me/inbox", "secret-token-123", True)
        self.assertEqual(inbox.send_new([self.file]), 1)
        self.assertTrue(FakeGitHub.puts[0][0].startswith("/repos/me/inbox/contents/inbox/"))
        self.assertTrue(FakeGitHub.puts[0][0].endswith("/ACV_ACV_Auctions_2026-10-05_1000.html"))
        self.assertEqual(inbox.send_new([self.file]), 0)                      # тот же файл — второй раз не отправляется
        time.sleep(1.1)
        self.file.write_text("<html>acv, больше машин</html>", encoding="utf-8")
        self.assertEqual(inbox.send_new([self.file]), 1)                      # изменился — отправлен заново, поверх
        self.assertEqual(FakeGitHub.puts[1][1], "sha1")
        self.assertNotIn("secret-token-123", json.dumps(inbox.state()))                     # токен наружу не отдаётся


if __name__ == "__main__":
    unittest.main()
