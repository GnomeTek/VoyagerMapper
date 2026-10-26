"""Serve XYZ tiles from an MBTiles file on 127.0.0.1 (screenshot support only)."""
import sqlite3, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
MB = sys.argv[1]; PORT = int(sys.argv[2])
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        try:
            z, x, y = [int(v) for v in self.path.strip("/").split(".")[0].split("/")[-3:]]
        except Exception:
            self.send_error(404); return
        row = sqlite3.connect(MB).execute("select tile_data from tiles where zoom_level=? and tile_column=? and tile_row=?", (z, x, (1 << z) - 1 - y)).fetchone()
        if not row: self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(row[0]))); self.end_headers(); self.wfile.write(row[0])
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
