"""Append-only JSONL event store with process lock and atomic batch replacement."""
from __future__ import annotations
import json
import os
from pathlib import Path
from src.league.models import LeagueEvent


def read_events(path):
    path = Path(path)
    if not path.exists():
        return []
    events = [LeagueEvent.from_dict(json.loads(line)) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len({e.event_id for e in events}) != len(events) or len({e.sequence for e in events}) != len(events):
        raise ValueError('Corrupt event log: duplicate IDs/sequences')
    return events


def append_events(path, batch, validator=None):
    """Idempotent by event ID; conflicting history is rejected, never overwritten.

    Write the entire byte prefix plus the new batch to a temporary file, fsync,
    then replace. This is logically append-only, without partially appended trades.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(f'Event store locked: {lock}; another writer or interrupted write') from exc
    temp = path.with_suffix(path.suffix + '.tmp')
    try:
        os.close(fd)
        old = read_events(path)
        by_id = {e.event_id: e for e in old}
        additions = []
        maximum = max((e.sequence for e in old), default=0)
        for event in batch:
            if event.event_id in by_id:
                if event.to_dict() != by_id[event.event_id].to_dict():
                    raise ValueError(f'Conflicting existing event: {event.event_id}')
                continue
            if event.sequence <= maximum:
                raise ValueError('Cannot insert or reorder past events; reconcile source history explicitly')
            maximum = event.sequence
            by_id[event.event_id] = event
            additions.append(event)
        if validator:
            validator(old + additions)
        if not additions:
            return 0
        prefix = path.read_bytes() if path.exists() else b''
        if prefix and not prefix.endswith(b'\n'):
            prefix += b'\n'
        encoded = ''.join(json.dumps(e.to_dict(), ensure_ascii=False, sort_keys=True) + '\n' for e in additions).encode('utf-8')
        with temp.open('xb') as output:
            output.write(prefix + encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        return len(additions)
    finally:
        lock.unlink(missing_ok=True)
