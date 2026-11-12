#!/usr/bin/env python3
"""Development stand-in for the Slack incoming webhooks.

Answers with http code 200 to every request and records it as one JSON line,
so that notifications sent by the services under test never reach the
real Slack channels, and can be checked afterwards. GET returns everything
recorded so far, as plain text.

Configured through the environment:
    ALERT_SINK_PORT  TCP port to listen on
    ALERT_SINK_LOG   JSON lines file to append to
"""

import datetime
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("ALERT_SINK_LOG", "/var/lib/alert-sink/requests.jsonl")
PORT = int(os.environ.get("ALERT_SINK_PORT", "8181"))


class Handler(BaseHTTPRequestHandler):
    def _answer(self, status, content):
        body = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace")
        try:
            decoded = json.loads(body)
        except ValueError:
            decoded = None
        entry = {
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "method": self.command, # POST, PUT or PATCH
            "path": self.path, # /comite-informatique vs /comite-informatique-bot
            "headers": dict(self.headers),
            "body": body, # raw request body
            "json": decoded, # + decoded request body as json
            # keep both raw and decoded for troubleshooting sender issues
        }
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # What Slack answers to a webhook call
        self._answer(200, "ok")

    do_POST = _record
    do_PUT = _record
    do_PATCH = _record

    def do_GET(self):
        try:
            # answer GET with every messsage on disk
            with open(LOG, encoding="utf-8") as log:
                self._answer(200, log.read())
        except FileNotFoundError:
            self._answer(200, "")

    # override lib defaults, we just remove the baked in timestamp since systemd will add its own.
    def log_message(self, format, *args):
        # One line per request on the journal
        sys.stderr.write("%s %s\n" % (self.address_string(), format % args))


def main():
    server = ThreadingHTTPServer(("", PORT), Handler)
    sys.stderr.write("alert-sink listening on port %d, recording to %s\n" % (PORT, LOG))
    server.serve_forever()


if __name__ == "__main__":
    main()
