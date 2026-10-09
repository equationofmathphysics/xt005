import gzip
import unittest

from flask import Flask, jsonify

from codexws_server.http_transport import configure_http_transport


class HttpTransportTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        configure_http_transport(app)

        @app.get("/api/payload")
        def payload():
            return jsonify({"data": ["compressible response"] * 200})

        self.client = app.test_client()

    def test_large_json_api_response_is_not_cached_and_supports_gzip(self):
        plain = self.client.get("/api/payload")
        compressed = self.client.get(
            "/api/payload", headers={"Accept-Encoding": "br, gzip, deflate"}
        )

        self.assertEqual(plain.headers["Cache-Control"], "no-store")
        self.assertIsNone(plain.headers.get("Content-Encoding"))
        self.assertEqual(compressed.headers["Cache-Control"], "no-store")
        self.assertEqual(compressed.headers["Content-Encoding"], "gzip")
        self.assertIn("Accept-Encoding", compressed.headers["Vary"])
        self.assertEqual(gzip.decompress(compressed.data), plain.data)
        self.assertLess(len(compressed.data), len(plain.data) // 2)


if __name__ == "__main__":
    unittest.main()
