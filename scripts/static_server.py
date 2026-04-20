"""Tiny gzip-capable static server for the dashboard.

Python's stdlib http.server doesn't compress. contacts_data.json is ~5MB
uncompressed, so remote loads feel "forever." This wrapper gzips text-like
responses on the fly when the client sends Accept-Encoding: gzip.
"""
from __future__ import annotations

import gzip
import io
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

COMPRESSIBLE = (
    ".html", ".css", ".js", ".json", ".svg", ".txt", ".map",
)


class GzipHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        path = self.translate_path(self.path)
        self._served_path = path
        accept = self.headers.get("Accept-Encoding", "")
        will_gzip = (
            "gzip" in accept
            and path.lower().endswith(COMPRESSIBLE)
            and os.path.isfile(path)
        )
        if not will_gzip:
            return super().send_head()

        try:
            f = open(path, "rb")
        except OSError:
            self.send_error(404)
            return None
        try:
            fs = os.fstat(f.fileno())
            ctype = self.guess_type(path)
            raw = f.read()
            buf = io.BytesIO()
            with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
                gz.write(raw)
            gzipped = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(gzipped)))
            self.send_header("Last-Modified", self.date_time_string(int(fs.st_mtime)))
            self.end_headers()
            return io.BytesIO(gzipped)
        except Exception:
            f.close()
            raise


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    directory = sys.argv[2] if len(sys.argv) > 2 else "export"
    os.chdir(directory)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), GzipHandler)
    print(f"gzip static server on :{port} serving {directory}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
