import hashlib
import json
from datetime import UTC, datetime

from greenlight.application.ports import AuditEntry

GENESIS = "0" * 64


def _canonical_time(moment: datetime) -> str:
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.replace(microsecond=0).isoformat()


def fingerprint(entry: AuditEntry) -> str:
    payload = json.dumps(
        {
            "previous": entry.previous_hash,
            "at": _canonical_time(entry.occurred_at),
            "action": entry.action,
            "actor": str(entry.actor_id) if entry.actor_id else None,
            "on_behalf_of": str(entry.on_behalf_of_id) if entry.on_behalf_of_id else None,
            "request": str(entry.request_id) if entry.request_id else None,
            "details": entry.details,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def seal(entry: AuditEntry, previous_hash: str | None) -> AuditEntry:
    entry.occurred_at = entry.occurred_at.replace(microsecond=0)
    entry.details = json.loads(json.dumps(entry.details, default=str))
    entry.previous_hash = previous_hash or GENESIS
    entry.hash = fingerprint(entry)
    return entry


def broken_links(entries: list[AuditEntry]) -> list[AuditEntry]:
    broken = []
    expected_previous = GENESIS
    for entry in entries:
        if entry.previous_hash != expected_previous or entry.hash != fingerprint(entry):
            broken.append(entry)
        expected_previous = entry.hash
    return broken
