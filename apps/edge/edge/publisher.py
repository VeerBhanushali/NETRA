"""Publishes voted sightings to the API.

Buffers locally and retries, so a brief API outage costs latency rather
than data. During a hackathon the API gets restarted constantly; the
worker must survive that without losing the vehicles it saw.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections import deque

API_URL = os.getenv("NETRA_API_URL", "http://127.0.0.1:8000")
INGEST_TOKEN = os.getenv("NETRA_INGEST_TOKEN", "dev-ingest-token")


class Publisher:
    def __init__(self, batch_size: int = 8, max_buffer: int = 5000) -> None:
        self.batch_size = batch_size
        # Bounded: if the API is down for an hour we drop the oldest
        # sightings rather than exhausting memory on the edge box.
        self.buffer: deque[dict] = deque(maxlen=max_buffer)
        self.sent = 0
        self.failed = 0

    def add(self, sighting: dict) -> None:
        self.buffer.append(sighting)
        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> bool:
        if not self.buffer:
            return True
        batch = [self.buffer.popleft() for _ in range(min(self.batch_size, len(self.buffer)))]
        body = json.dumps({"sightings": batch}).encode()
        req = urllib.request.Request(
            f"{API_URL}/api/ingest/sightings",
            data=body,
            headers={"content-type": "application/json",
                     "x-ingest-token": INGEST_TOKEN},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            self.sent += result.get("accepted", 0)
            if result.get("alerts"):
                for a in result["alerts"]:
                    print(f"  !! ALERT [{a['severity']}] {a['title']}")
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Put them back at the front so ordering is preserved.
            self.failed += len(batch)
            for s in reversed(batch):
                self.buffer.appendleft(s)
            print(f"  .. publish failed ({exc}); {len(self.buffer)} buffered")
            return False

    def drain(self, attempts: int = 5) -> None:
        """Flush everything before exit, with backoff."""
        for i in range(attempts):
            while self.buffer:
                if not self.flush():
                    break
            if not self.buffer:
                return
            time.sleep(2 ** i)
