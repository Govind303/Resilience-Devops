"""Downstream dependency for the ResilienceOps app: a tiny quote API on port 9090.

The network-failure chaos experiment cuts the app off from this service. For the local demo,
creating the file given by --blackhole-file makes the service stall, which looks the same to the
app as a dropped network path.
"""
import argparse
import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

QUOTES = [
    "Everything fails, all the time.",
    "Hope is not a strategy.",
    "The best way to avoid failure is to fail constantly.",
    "Embrace failure, design for recovery.",
]


def make_handler(blackhole_file):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if blackhole_file and os.path.exists(blackhole_file):
                time.sleep(5)  # longer than the app's timeout, so requests are effectively dropped
            if self.path.rstrip("/") != "/quote":
                self.send_error(404)
                return
            body = json.dumps({"quote": random.choice(QUOTES)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--blackhole-file", default=None)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args.blackhole_file))
    print(f"quote service on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
