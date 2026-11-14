"""POST /api/scan image_path and POST /api/upload validation, over a live server with run_scan faked."""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.server as server  # noqa: E402

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16


class ScanInputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CsWatchHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.shots = root / "shots"          # configured screenshots dir
        self.default_shots = root / "default"  # SCREENSHOT_DIR (data/screenshots)
        self.shots.mkdir()
        self.default_shots.mkdir()
        self.calls: list[tuple[object, bool]] = []

        def fake_run_scan(image_path=None, fetch_steam=True):
            self.calls.append((image_path, fetch_steam))
            return {"players": []}

        patches = [
            mock.patch.object(server, "get_screenshot_dir", return_value=self.shots),
            mock.patch.object(server, "SCREENSHOT_DIR", self.default_shots),
            mock.patch.object(server, "run_scan", side_effect=fake_run_scan),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # ----------------------------------------------------------------- helpers
    def _request(self, method, path, body, content_type):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers={"Content-Type": content_type})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, json.loads(data) if data else None
        finally:
            conn.close()

    def post_scan(self, payload):
        return self._request("POST", "/api/scan", json.dumps(payload).encode("utf-8"), "application/json")

    def post_upload(self, raw, content_type="image/png"):
        return self._request("POST", "/api/upload", raw, content_type)

    # ----------------------------------------------------------------- /api/scan
    def test_image_inside_screenshots_dir_is_accepted(self):
        img = self.shots / "cs2_1.png"
        img.write_bytes(PNG_BYTES)
        status, _ = self.post_scan({"image_path": str(img)})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [(img.resolve(), True)])

    def test_image_inside_default_screenshot_dir_is_accepted(self):
        # scripts/cs_scan.sh sends data/screenshots/cs2_<ts>.png, i.e. SCREENSHOT_DIR
        img = self.default_shots / "cs2_2.png"
        img.write_bytes(PNG_BYTES)
        status, _ = self.post_scan({"image_path": str(img)})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [(img.resolve(), True)])

    def test_jpeg_suffix_is_accepted_case_insensitively(self):
        img = self.shots / "shot.JPG"
        img.write_bytes(JPEG_BYTES)
        status, _ = self.post_scan({"image_path": str(img)})
        self.assertEqual(status, 200)

    def test_system_file_is_rejected(self):
        status, body = self.post_scan({"image_path": "/etc/passwd"})
        self.assertEqual(status, 400)
        self.assertIn("screenshots directory", body["error"])
        self.assertEqual(self.calls, [])

    def test_traversal_out_of_screenshots_dir_is_rejected(self):
        outside = self.shots.parent / "x.png"
        outside.write_bytes(PNG_BYTES)
        status, _ = self.post_scan({"image_path": f"{self.shots}/../x.png"})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_non_image_suffix_in_screenshots_dir_is_rejected(self):
        txt = self.shots / "notes.txt"
        txt.write_text("hi", encoding="utf-8")
        status, _ = self.post_scan({"image_path": str(txt)})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_missing_image_in_screenshots_dir_is_404(self):
        status, body = self.post_scan({"image_path": str(self.shots / "gone.png")})
        self.assertEqual(status, 404)
        self.assertEqual(body, {"error": "Image not found"})
        self.assertEqual(self.calls, [])

    def test_non_string_image_path_is_400(self):
        status, body = self.post_scan({"image_path": 123})
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "image_path must be a string"})
        self.assertEqual(self.calls, [])

    def test_without_image_path_capture_is_used(self):
        status, _ = self.post_scan({"fetch_steam": False})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [(None, False)])

    # ----------------------------------------------------------------- /api/upload
    def test_png_upload_is_saved_and_scanned(self):
        status, _ = self.post_upload(PNG_BYTES, "image/png")
        self.assertEqual(status, 200)
        saved = list(self.shots.glob("upload_*.png"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), PNG_BYTES)
        self.assertEqual(self.calls, [(saved[0], True)])

    def test_jpeg_upload_is_saved_with_jpg_suffix(self):
        status, _ = self.post_upload(JPEG_BYTES, "image/jpeg")
        self.assertEqual(status, 200)
        self.assertEqual(len(list(self.shots.glob("upload_*.jpg"))), 1)

    def test_non_image_upload_is_rejected_and_nothing_is_written(self):
        status, body = self.post_upload(b"hello", "image/png")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "Upload must be a PNG or JPEG image"})
        self.assertEqual(list(self.shots.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_empty_upload_is_rejected(self):
        status, body = self.post_upload(b"", "image/png")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "Empty upload"})


if __name__ == "__main__":
    unittest.main()
