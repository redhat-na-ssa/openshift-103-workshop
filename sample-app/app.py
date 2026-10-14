#!/usr/bin/env python3
"""Sample application for the OpenShift Cloud Workshop.

Lists the objects in an S3 bucket (ROSA) or a Blob container (ARO),
using whatever cloud credentials the pod is given.

  sample-app          serve HTTP on $PORT (default 8080)
  sample-app list     print the object listing and exit
  sample-app whoami   print the cloud identity the pod runs as and exit

HTTP paths: / (object listing), /healthz, /metrics (Prometheus).

Configuration, all through environment variables:
  BUCKET           S3 bucket name (AWS)
  AWS_REGION       region of the bucket (AWS)
  STORAGE_ACCOUNT  storage account name (Azure)
  CONTAINER        Blob container name (Azure)
  STATE_DIR        writable directory for the listing cache
"""

import base64
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

PORT = int(os.environ.get("PORT", "8080"))
STATE_DIR = os.environ.get("STATE_DIR", "/var/lib/sample-app")
PATHS = ("/", "/healthz", "/metrics")

REQUESTS = Counter(
    "sample_app_requests_total",
    "HTTP requests served, by path and status code.",
    ["path", "code"],
)
DURATION = Histogram(
    "sample_app_request_duration_seconds",
    "HTTP request duration, by path.",
    ["path"],
)
logging.getLogger("azure").setLevel(logging.ERROR)

OBJECT_ERRORS = Counter(
    "sample_app_object_store_errors_total",
    "Failed calls to S3 or Blob storage.",
)


def log(level, msg, **fields):
    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "level": level,
        "msg": msg,
        **fields,
    }
    print(json.dumps(record), flush=True)


def cloud():
    if os.environ.get("BUCKET"):
        return "aws"
    if os.environ.get("STORAGE_ACCOUNT"):
        return "azure"
    return "none"


def azure_credential():
    from azure.identity import DefaultAzureCredential

    # Use only credentials given to the pod, never the node's identity.
    return DefaultAzureCredential(exclude_managed_identity_credential=True)


def list_objects():
    provider = cloud()
    if provider == "aws":
        import boto3

        bucket = os.environ["BUCKET"]
        resp = boto3.client("s3").list_objects_v2(Bucket=bucket, MaxKeys=100)
        objects = [
            {"name": o["Key"], "size": o["Size"]}
            for o in resp.get("Contents", [])
        ]
        return {"cloud": "aws", "bucket": bucket, "objects": objects}
    if provider == "azure":
        from azure.storage.blob import BlobServiceClient

        account = os.environ["STORAGE_ACCOUNT"]
        container = os.environ.get("CONTAINER", "sample-app")
        credential = azure_credential()
        # The Blob client retries a failed token request for over a minute,
        # so check the credential first and fail fast when the pod has none.
        credential.get_token("https://storage.azure.com/.default")
        service = BlobServiceClient(
            f"https://{account}.blob.core.windows.net",
            credential=credential,
        )
        blobs = service.get_container_client(container).list_blobs()
        objects = [{"name": b.name, "size": b.size} for b in blobs]
        return {
            "cloud": "azure",
            "storageAccount": account,
            "container": container,
            "objects": objects,
        }
    raise RuntimeError("set BUCKET (AWS) or STORAGE_ACCOUNT (Azure)")


def whoami():
    provider = cloud()
    if provider == "aws":
        import boto3

        arn = boto3.client("sts").get_caller_identity()["Arn"]
        return {"cloud": "aws", "arn": arn}
    if provider == "azure":
        token = azure_credential().get_token("https://storage.azure.com/.default")
        payload = token.token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return {
            "cloud": "azure",
            "clientId": claims.get("appid") or claims.get("azp"),
            "objectId": claims.get("oid"),
            "tenantId": claims.get("tid"),
        }
    raise RuntimeError("set BUCKET (AWS) or STORAGE_ACCOUNT (Azure)")


def error_text(exc):
    return f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"


def cached_listing():
    """Fetch the listing and keep a copy in STATE_DIR."""
    listing = list_objects()
    with open(os.path.join(STATE_DIR, "last-listing.json"), "w") as f:
        json.dump(listing, f)
    return listing


class Handler(BaseHTTPRequestHandler):
    server_version = "sample-app/1.0"

    def do_GET(self):
        start = time.monotonic()
        path = self.path.split("?")[0]
        if path == "/healthz":
            code, body, ctype = 200, {"status": "ok"}, "application/json"
        elif path == "/metrics":
            code, body, ctype = 200, generate_latest(), CONTENT_TYPE_LATEST
        elif path == "/":
            try:
                code, body = 200, cached_listing()
            except Exception as exc:
                OBJECT_ERRORS.inc()
                code, body = 500, {"cloud": cloud(), "error": error_text(exc)}
            ctype = "application/json"
        else:
            code, body, ctype = 404, {"error": "not found"}, "application/json"

        data = body if isinstance(body, bytes) else (json.dumps(body) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

        elapsed = time.monotonic() - start
        label = path if path in PATHS else "other"
        REQUESTS.labels(label, str(code)).inc()
        DURATION.labels(label).observe(elapsed)
        if path != "/healthz":
            log(
                "info",
                "request",
                method="GET",
                path=path,
                status=code,
                durationMs=round(elapsed * 1000, 1),
                client=self.headers.get("X-Forwarded-For", self.client_address[0]),
            )

    def log_message(self, format, *args):
        pass  # requests are logged as JSON in do_GET


def check_state_dir():
    """Fail fast, like many upstream images, if the state directory is read-only."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "started"), "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError as exc:
        log(
            "error",
            "cannot write state directory",
            path=STATE_DIR,
            uid=os.getuid(),
            gid=os.getgid(),
            error=str(exc),
        )
        sys.exit(1)


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command in ("list", "whoami"):
        try:
            result = list_objects() if command == "list" else whoami()
        except Exception as exc:
            print(json.dumps({"cloud": cloud(), "error": error_text(exc)}, indent=2))
            sys.exit(1)
        print(json.dumps(result, indent=2))
        return
    if command != "serve":
        sys.exit(f"unknown command {command!r}; use serve, list or whoami")

    check_state_dir()
    log("info", "starting", port=PORT, cloud=cloud(), uid=os.getuid(), gid=os.getgid())
    # As PID 1 the process ignores SIGTERM unless it handles it, so pods
    # would wait out the full grace period on every rollout.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
