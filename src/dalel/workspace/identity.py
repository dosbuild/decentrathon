"""Stable workspace fingerprints and fresh event identifiers."""
import uuid
from datetime import datetime, timezone
from dalel.review.store import digest

def stamp():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix=''):
    return prefix + uuid.uuid4().hex


def fnv(text):
    value = 2166136261
    for char in text:
        value = ((value ^ ord(char)) * 16777619) & 0xffffffff
    return f'{value:08x}'



def raw_digest(state):
    return digest([[role, value['raw']] for role, value in state['snapshots'].items() if role != 'references'] +
                  [['references', [[r['locale'], r['raw']] for r in state['snapshots']['references']]]])
