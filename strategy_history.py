"""Persistent successful strategies, scoped to build path, strategy contents and targets."""
from connection_health import CRITERION, network_key
import hashlib
import json
import math
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

def success_timestamp(record):
    """Return a comparable timestamp, or zero for an untrusted record."""
    value = record.get('last_success') if isinstance(record, dict) else None
    if type(value) not in (int, float):
        return 0.0
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


class StrategyHistory:
    def __init__(self, filename):
        self.filename = Path(filename)
        try:
            data = json.loads(self.filename.read_text(encoding='utf-8'))
            self.records = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.records = {}

    def key(self, path, targets):
        path = Path(path)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
        engine = path.parent/'bin/winws.exe'
        engine_digest = hashlib.sha256(engine.read_bytes()).hexdigest() if engine.is_file() else ''
        payload = [CRITERION, network_key(), engine_digest, str(path.resolve()).casefold(), digest, sorted(targets.items())]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def order(self, paths, targets):
        def score(path):
            stamp = success_timestamp(self.records.get(self.key(path, targets)))
            return stamp if 0 <= time.time()-stamp <= 86400 else 0
        return sorted(paths, key=score, reverse=True)

    def preferred(self, paths, targets):
        ordered = self.order(paths, targets)
        if ordered and 0 <= time.time()-success_timestamp(self.records.get(self.key(ordered[0], targets))) <= 86400:
            return ordered[0]
        return None

    def record_success(self, path, targets):
        key = self.key(path, targets)
        if key is None:
            return
        self.records[key] = {'last_success': time.time(), 'strategy': Path(path).stem}
        self.records = dict(sorted(((key, record) for key, record in self.records.items() if success_timestamp(record)),
            key=lambda item: success_timestamp(item[1]),
            reverse=True)[:256])
        self.filename.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.filename.parent,
                                    prefix='strategy-history-', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self.records, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.filename)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)