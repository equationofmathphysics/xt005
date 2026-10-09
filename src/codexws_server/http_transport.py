import gzip

from flask import request


MINIMUM_GZIP_BYTES = 1024


def configure_http_transport(app):
    @app.after_request
    def prepare_response(response):
        if response.mimetype != "application/json":
            return response

        response.headers["Cache-Control"] = "no-store"
        accepted_encodings = request.headers.get("Accept-Encoding", "").lower()
        if (
            "gzip" not in accepted_encodings
            or response.status_code < 200
            or response.status_code >= 300
            or response.direct_passthrough
            or response.headers.get("Content-Encoding")
        ):
            return response

        payload = response.get_data()
        if len(payload) < MINIMUM_GZIP_BYTES:
            return response
        compressed = gzip.compress(payload, compresslevel=5)
        if len(compressed) >= len(payload):
            return response
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        response.headers.add("Vary", "Accept-Encoding")
        return response

    return app
