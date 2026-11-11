"""Portable review receipts tied to the workspace and exact text revisions."""
from datetime import datetime

from dalel.review.store import digest


def manifest(state):
    content = dict(workspace=state['id'], glossary_sha256=digest(state['glossary']),
        cards=[dict(key=k, revision=e['currentRevisionId'], source_sha256=digest(e['source']),
                    text_sha256=digest(e['workingText'])) for k, e in state['entries'].items()])
    return dict(content, review_id=digest(content))


def export(state):
    entries = []
    for key, entry in state['entries'].items():
        for decision in entry['decisions'][-1:]:
            if decision.get('revisionId') == entry['currentRevisionId'] and decision.get('reviewed_by_human'):
                entries.append(dict(key=key, text=entry['workingText'], **decision))
    return dict(format='dalel-workspace-review/1', mode='human', attest_human=bool(entries),
                manifest=manifest(state), entries=entries)


def validate(state, packet):
    if packet.get('format') != 'dalel-workspace-review/1' or packet.get('mode') != 'human' or packet.get('attest_human') is not True:
        raise ValueError('An explicit human review receipt is required')
    if packet.get('manifest') != manifest(state):
        raise ValueError('Review belongs to different text revisions or glossary')
    entries = packet.get('entries')
    if not isinstance(entries, list):
        raise ValueError('Decision entries are required')
    seen = set()
    for entry in entries:
        key = entry.get('key')
        if key not in state['entries'] or key in seen:
            raise ValueError('Unknown or duplicate decision key')
        seen.add(key)
        current = state['entries'][key]
        if entry.get('revisionId') != current['currentRevisionId'] or entry.get('text') != current['workingText']:
            raise ValueError('Reviewed text changed')
        if entry.get('action') not in ('approve', 'request_changes', 'withdrawn', 'defer', 'code_dependency'):
            raise ValueError('Unknown translation decision')
        if not isinstance(entry.get('reviewer'), str) or not entry['reviewer'].strip():
            raise ValueError('Reviewer attribution is required')
        if datetime.fromisoformat(entry.get('at', '').replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('Decision timestamp requires a timezone')
    return entries
