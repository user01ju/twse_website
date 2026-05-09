"""Local HTTP server — serves output/ at http://localhost:8080"""
import http.server
import os
from config import OUTPUT_DIR

os.chdir(OUTPUT_DIR)
handler = http.server.SimpleHTTPRequestHandler

print(f"Serving {OUTPUT_DIR} at http://localhost:8080")
print("Press Ctrl+C to stop.")
with http.server.HTTPServer(("", 8080), handler) as httpd:
    httpd.serve_forever()
