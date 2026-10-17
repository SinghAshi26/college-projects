"""Structured metadata only: never serialize arbitrary exceptions, headers or payloads."""
import json
import logging
import re

logger = logging.getLogger("appnix")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False
ALLOWED = {"provider", "operation", "outcome", "http_status", "attempt", "search_id", "request_id", "error_category"}


def safe_event(event, **metadata):
    record = {"event": re.sub(r"[^a-z_]", "", event)[:60]}
    for key, value in metadata.items():
        if key not in ALLOWED:
            continue
        if isinstance(value, int) or value is None:
            record[key] = value
        elif isinstance(value, str) and re.fullmatch(r"[a-z0-9_-]{1,64}", value):
            record[key] = value
    logger.info(json.dumps(record, sort_keys=True))
