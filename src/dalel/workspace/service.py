"""Durable local workspaces using the production ARB, analysis and review contracts.

The client owns presentation and draft editing. This service owns immutable inputs,
optimistic concurrency, exact-text review attestations, analysis and release bytes.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading

from dalel import __version__
from dalel.contracts import Finding
from dalel.formats.arb import ArbDocument
from dalel.formats.messages import validate_suggestion
from dalel.review.store import canonical, digest
from dalel.resources import PACKAGE_ROOT
from dalel.workspace.identity import stamp, uid, fnv, raw_digest
from dalel.workspace.glossary import glossary_text, normalize_terms


class Conflict(ValueError):
    """The caller is editing an older workspace revision."""



def document(raw, locale, name):
    if not isinstance(raw, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,34}', locale):
        raise ValueError('ARB text and a valid locale are required')
    doc = ArbDocument(raw.encode('utf8'), path=Path(f'intl_{locale}.arb'))
    if doc.locale.lower().replace('-', '_') != locale.lower().replace('-', '_'):
        raise ValueError(f'{name}: declared locale does not match its selected role')
    for member in doc.effective.values():
        if not member.key.startswith('@') and not isinstance(member.value, str):
            raise ValueError(f'{name}: message {member.key!r} must be a string')
        if member.key.startswith('@') and not member.key.startswith('@@') and member.value is not None and not isinstance(member.value, dict):
            raise ValueError(f'{name}: message metadata must be an object or null')
    return doc


def snapshot(raw, locale, name):
    doc = document(raw, locale, name)
    return {'name': Path(name).name, 'raw': raw, 'data': {k: m.value for k, m in doc.effective.items()},
            'messages': doc.strings, 'locale': locale, 'hash': fnv(raw)}




class WorkspaceService:
    def __init__(self, directory, *, simulation=False):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'workspaces.sqlite3'
        self.simulation = simulation
        self.analysis_lock = threading.Lock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, state TEXT NOT NULL, version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, workspace TEXT NOT NULL, payload TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS audit_workspace ON audit(workspace, seq);
                CREATE TRIGGER IF NOT EXISTS immutable_audit_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT,'immutable workspace audit'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_audit_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT,'immutable workspace audit'); END;
            ''')
        self.verify()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def verify(self, ident=None):
        previous = {}
        with self.connect() as db:
            rows = db.execute('SELECT * FROM audit' + (' WHERE workspace=?' if ident else '') + ' ORDER BY seq', (ident,) if ident else ())
            for row in rows:
                prior = previous.get(row['workspace'], '0' * 64)
                if row['previous_hash'] != prior or digest([prior, json.loads(row['payload'])]) != row['event_hash']:
                    raise ValueError('Workspace audit chain is invalid')
                previous[row['workspace']] = row['event_hash']
        return previous

    def _read(self, db, ident, expected=None):
        row = db.execute('SELECT * FROM workspaces WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('Unknown workspace')
        if expected is not None and (type(expected) is not int or row['version'] != expected):
            raise Conflict('This workspace changed in another request or tab. Download your local backup before reloading.')
        state = json.loads(row['state'])
        last = db.execute('SELECT payload FROM audit WHERE workspace=? ORDER BY seq DESC LIMIT 1', (ident,)).fetchone()
        if not last or json.loads(last['payload'])['state_sha256'] != digest(state):
            raise ValueError('Saved workspace checksum does not match its audit record')
        return state

    def _write(self, db, state, kind, detail, *, initial=False):
        state['backend']['version'] += 1
        event = {'workspace': state['id'], 'version': state['backend']['version'], 'kind': kind,
                 'at': stamp(), 'detail': detail, 'state_sha256': digest(state)}
        last = db.execute('SELECT event_hash FROM audit WHERE workspace=? ORDER BY seq DESC LIMIT 1', (state['id'],)).fetchone()
        previous = last['event_hash'] if last else '0' * 64
        db.execute('INSERT INTO audit(workspace,payload,previous_hash,event_hash) VALUES(?,?,?,?)',
                   (state['id'], canonical(event), previous, digest([previous, event])))
        if initial:
            db.execute('INSERT INTO workspaces VALUES(?,?,?)', (state['id'], canonical(state), state['backend']['version']))
        else:
            db.execute('UPDATE workspaces SET state=?,version=? WHERE id=?', (canonical(state), state['backend']['version'], state['id']))
        return state

    def current(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='active'").fetchone()
            return self._read(db, row['value']) if row and row['value'] else None

    def get(self, ident):
        with self.connect() as db:
            return self._read(db, ident)

    def activate(self, ident):
        with self.connect() as db:
            state = self._read(db, ident)
            db.execute("INSERT OR REPLACE INTO settings VALUES('active',?)", (ident,))
        return state

    def clear(self):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('active',NULL)")

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,state,version FROM workspaces ORDER BY rowid DESC').fetchall()
        return [{'id': r['id'], 'name': (s := json.loads(r['state']))['name'], 'entries': len(s['order']),
                 'createdAt': s['createdAt'], 'version': r['version']} for r in rows]

    def _documents(self, state, *, working=False):
        src = document(state['snapshots']['source']['raw'], state['sourceLocale'], 'source')
        target = document(state['snapshots']['target']['raw'], state['targetLocale'], 'target')
        if working:
            edits = {k: e['workingText'] for k, e in state['entries'].items() if e['workingText'] is not None and k in target.strings}
            additions = {k: e['workingText'] for k, e in state['entries'].items() if e['workingText'] is not None and k not in target.strings}
            target = ArbDocument(target.render(edits), path=target.path)
            target = ArbDocument(target.insert_in_source_order(additions, list(src.strings)), path=target.path)
        return src, target

    def validation(self, state, key, text):
        entry = state['entries'][key]
        if entry['source'] is None:
            return []
        if text is None:
            return [{'key': key, 'kind': 'missing', 'message': 'No target translation for this source key.'}]
        original = entry['originalTarget']
        inherited_lines = (isinstance(original, str) and
            original.count('\n') != entry['source'].count('\n') and text.count('\n') == original.count('\n'))
        errors = validate_suggestion(entry['source'], text, escaping=state['backend']['escaping'],
                                     original_target=original, allow_line_reflow=inherited_lines)
        return [{'key': key, 'kind': 'backend', 'message': reason} for reason in errors]

    @staticmethod
    def guidance(state, key, text):
        entry = state['entries'][key]
        original = entry['originalTarget']
        if isinstance(original, str) and isinstance(text, str) and isinstance(entry['source'], str):
            if original.count('\n') != entry['source'].count('\n') and text.count('\n') == original.count('\n'):
                return [dict(key=key, kind='inherited_layout', message='The imported translation uses different line breaks from the source. Inspect its layout in context.')]
        return []

    def _validate_entry(self, state, key):
        e = state['entries'][key]
        e['backendValidation'] = {'text': e['workingText'], 'revisionId': e['currentRevisionId'],
                                  'errors': self.validation(state, key, e['workingText']),
                                  'warnings': self.guidance(state, key, e['workingText'])}

    def create(self, candidate):
        if not isinstance(candidate, dict) or not isinstance(candidate.get('snapshots'), dict):
            raise ValueError('Source and target ARB snapshots are required')
        source_locale, target_locale = candidate.get('sourceLocale', 'ru'), candidate.get('targetLocale', 'kk')
        if source_locale.lower().replace('-', '_') == target_locale.lower().replace('-', '_'):
            raise ValueError('Source and target locales must differ')
        supplied = candidate['snapshots']
        source = snapshot(supplied['source']['raw'], source_locale, supplied['source'].get('name', 'source.arb'))
        target = snapshot(supplied['target']['raw'], target_locale, supplied['target'].get('name', 'target.arb'))
        refs = []
        seen = {source_locale.lower().replace('-', '_'), target_locale.lower().replace('-', '_')}
        for ref in supplied.get('references', []):
            locale = ref.get('selectedLocale') or ref.get('locale')
            if not isinstance(locale, str) or locale.lower().replace('-', '_') in seen:
                raise ValueError('Each reference needs a distinct locale')
            seen.add(locale.lower().replace('-', '_'))
            refs.append(dict(snapshot(ref['raw'], locale, ref.get('name', 'reference.arb')), selectedLocale=locale))
        name = candidate.get('name', 'Localization review')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Workspace name is required')
        state = {'schema': 1, 'id': uid(), 'name': name.strip(), 'createdAt': stamp(), 'demo': False,
                 'sourceLocale': source_locale, 'targetLocale': target_locale,
                 'snapshots': {'source': source, 'target': target, 'references': refs}, 'historicImports': [],
                 'order': list(dict.fromkeys([*source['messages'], *target['messages']])), 'entries': {},
                 'glossary': normalize_terms(candidate.get('glossary', [])), 'glossaryVersion': 1,
                 'checks': {'status': 'not_run', 'checkedAt': None, 'scope': 'none'}, 'releases': [],
                 'ui': {'view': 'review', 'filter': 'all', 'query': '', 'selectedKey': None, 'sort': 'source',
                        'inspector': 'issues', 'releaseMode': 'validated', 'glossaryQuery': ''},
                 'session': {'id': uid('local-'), 'label': '', 'nativeSelfDeclared': False},
                 'processing': {'externalCalls': 0, 'externalCost': 0, 'provider': 'dalel offline'},
                 'backend': {'version': 0, 'engine': __version__, 'escaping': candidate.get('useEscaping') is True,
                             'simulation': self.simulation, 'analysis': {'mode': 'not_run'}, 'entryVersions': {}},
                 'createdFrom': 'dalel backend import'}
        if candidate.get('backend') or any(e.get('decisions') or len(e.get('revisions', [])) > 1 for e in candidate.get('entries', {}).values()):
            state['recoveredHistory'] = {'trusted_human_review': False, 'reason': 'Imported backup claims are retained as history; current drafts require fresh review', 'workspace': copy.deepcopy(candidate)}
        demo_dir = PACKAGE_ROOT / 'assets/workspace/demo'
        if candidate.get('demo') and demo_dir.exists():
            state['demo'] = all(json.loads((demo_dir / f'intl_{locale}.arb').read_text()) == snap['data']
                                for locale, snap in [('ru', source), ('kk', target)]) if (source_locale, target_locale) == ('ru', 'kk') else False
        for key in state['order']:
            original = target['messages'].get(key)
            rid = uid('import-')
            src = source['messages'].get(key)
            meta = source['data'].get('@' + key) or {}
            e = {'key': key, 'targetLocale': target_locale, 'source': src, 'sourceHash': fnv(key + '\0' + (src or '')),
                 'originalTarget': original, 'workingText': original, 'glossaryFingerprint': digest(state['glossary']), 'sourceMeta': source['data'].get('@' + key),
                 'targetMeta': target['data'].get('@' + key), 'description': meta.get('description') or (target['data'].get('@' + key) or {}).get('description', ''),
                 'contextOrigin': 'provided' if meta.get('description') else 'inferred',
                 'refs': {r['locale']: r['messages'].get(key) for r in refs},
                 'revisions': [{'id': rid, 'text': original, 'createdAt': stamp(), 'reason': 'Imported target', 'author': 'Imported file'}],
                 'currentRevisionId': rid, 'decisions': [], 'reviewHistory': [], 'undoStack': [], 'redoStack': [],
                 'manualFindings': [], 'importedFindings': [], 'localFindings': [], 'checkedRevisionId': None,
                 'checkedGlossaryVersion': 0, 'terminologyStale': False}
            prior = candidate.get('entries', {}).get(key, {})
            proposed = prior.get('workingText', original)
            if proposed is not None and not isinstance(proposed, str):
                raise ValueError('Working translations must be strings or missing')
            if proposed is None and original is not None:
                raise ValueError('Deleting an imported message is not supported')
            if proposed != original:
                e['workingText'] = proposed
                e['currentRevisionId'] = uid('rev-')
                e['revisions'].append({'id': e['currentRevisionId'], 'text': proposed, 'createdAt': stamp(),
                                       'reason': 'Recovered unreviewed draft', 'author': 'Workspace import'})
            state['entries'][key] = e
            state['backend']['entryVersions'][key] = 1
            for finding in prior.get('importedFindings', []):
                self._import_finding(state, key, finding)
            self._validate_entry(state, key)
        state['ui']['selectedKey'] = state['order'][0] if state['order'] else None
        from dalel.formats.context import sections
        section_by_key = sections(document(source['raw'], source_locale, 'source'))
        for index, key in enumerate(state['order']):
            state['entries'][key]['section'] = section_by_key.get(key, '')
            state['entries'][key]['neighbors'] = [k for k in state['order'][max(0, index-2):index+3]
                if k != key and section_by_key.get(k, '') == section_by_key.get(key, '')]
        # Analysis and validation finish before replacing the active workspace.
        self._analyze(state)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._write(db, state, 'import', {'inputs_sha256': raw_digest(state)}, initial=True)
            db.execute("INSERT OR REPLACE INTO settings VALUES('active',?)", (state['id'],))
        return state

    def _import_finding(self, state, key, value):
        payload = {k: value.get(k) for k in Finding.model_fields}
        payload.update(key=key, reviewed_by_human=False)
        e = state['entries'][key]
        if payload['source'] != e['source'] or payload['target'] != e['originalTarget']:
            raise ValueError('Imported finding belongs to a different ARB version')
        parsed = Finding.model_validate(payload).model_dump()
        e['importedFindings'].append(dict(parsed, id=uid('imported-'), origin='imported',
            basisRevisionId=e['revisions'][0]['id'], glossaryVersion=state['glossaryVersion'], sourceHash=e['sourceHash'], judgments=[]))

    def open_bundle(self, bundle):
        """Open a locally verified replay without trusting backup approval claims."""
        from dalel.delivery.replay import checked, load
        from dalel.terminology.glossary import rows
        bundle = Path(bundle).resolve()
        manifest = json.loads((bundle/'locqa-replay.json').read_text(encoding='utf8'))
        documents = [(r, ArbDocument.read(checked(bundle, r))) for r in manifest['inputs']]
        source = next(d for r, d in documents if r['role'] == 'source')
        target = next(d for r, d in documents if r['role'] == 'target')
        refs = [d for r, d in documents if r['role'] == 'reference']
        glossary = checked(bundle, manifest['glossary']) if manifest.get('glossary') else None
        loaded = load(bundle/'locqa-replay.json', source, target, references=refs,
                      glossary=glossary, escaping=manifest['escaping'])
        language = loaded['language']
        if language.get('origin') == 'reviewed_report':
            raise ValueError('Open the original machine-analysis bundle; import exact human decisions separately')
        terms = rows(glossary, include_candidates=True)
        for term in terms:
            term['variants'] = term.get('_rule', {}).get('allowed_variants', [])
            term['status'] = 'proposed'
        entries = {}
        for row in language['report']:
            entries.setdefault(row['key'], {'importedFindings': []})['importedFindings'].append(row)
        def snap(doc):
            return dict(raw=doc.raw.decode('utf8'), name=f'intl_{doc.locale}.arb', locale=doc.locale)
        state = self.create(dict(name='Localization review', sourceLocale=source.locale, targetLocale=target.locale,
            snapshots=dict(source=snap(source), target=snap(target), references=[snap(r) for r in refs]),
            glossary=terms, entries=entries, useEscaping=manifest['escaping']))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._read(db, state['id'], state['backend']['version'])
            state['backend']['analysis'].update(mode='replay', language_complete=True,
                language=language['coverage'], producer=language.get('provenance', {}).get('producer', 'student'))
            state['backend']['modelProvenance'] = language.get('provenance', {})
            state['backend']['modelDiagnostics'] = language['diagnostics']
            state['backend']['modelAnalysisIdentity'] = language['identity']
            for diagnostic in language['diagnostics']:
                key = diagnostic.get('key')
                if diagnostic.get('type') == 'requires_code_change' and key in state['entries']:
                    state['entries'][key].setdefault('codePlans', []).append(diagnostic)
            return self._write(db, state, 'open-analysis', {'analysis_identity': language['identity'], 'api_calls': 0})

    def _analyze(self, state):
        source, target = self._documents(state, working=True)
        folder = self.directory / state['id'] / 'analysis' / uid()
        folder.mkdir(parents=True, exist_ok=True)
        src_path = folder / f"intl_{state['sourceLocale']}.arb"
        tgt_path = folder / f"intl_{state['targetLocale']}.arb"
        src_path.write_bytes(source.raw); tgt_path.write_bytes(target.raw)
        glossary_path = folder / 'glossary.csv'
        glossary_path.write_text(glossary_text(state['glossary'], analysis=True, target_locale=state['targetLocale']), encoding='utf8', newline='')
        args = [sys.executable, '-m', 'dalel', 'check', '--source', str(src_path), '--target', str(tgt_path),
                '--profile', 'offline', '--out', str(folder / 'report.json'), '--glossary', str(glossary_path)]
        if state['backend']['escaping']:
            args.append('--use-escaping')
        for i, ref in enumerate(state['snapshots']['references']):
            ref_path = folder / f'ref_{i}_{ref["locale"]}.arb'
            ref_path.write_text(ref['raw'], encoding='utf8')
            args.extend(['--ref', str(ref_path)])
        env = dict(os.environ, LOCQA_BUDGET_USD='0', PYTHONPATH=str(PACKAGE_ROOT.parent))
        # Explicit offline profile cannot create a paid model request, even when
        # the parent shell contains a provider key or a positive budget.
        with self.analysis_lock:
            result = subprocess.run(args, capture_output=True, text=True, encoding='utf8', env=env, timeout=300)
        if result.returncode not in (0, 3) or not (folder / 'coverage.json').is_file():
            raise ValueError('dalel analysis failed: ' + (result.stderr or result.stdout)[-1500:])
        records = json.loads((folder / 'report.json').read_text())
        coverage = json.loads((folder / 'coverage.json').read_text())
        language = json.loads((folder / 'language.json').read_text())
        if coverage.get('api_calls', 0):
            raise ValueError('Offline workspace analysis unexpectedly reported new model calls')
        for e in state['entries'].values():
            e['localFindings'] = []
            e['checkedRevisionId'] = e['currentRevisionId']
            e['checkedGlossaryVersion'] = state['glossaryVersion']
            e['terminologyStale'] = False
        for record in records:
            record = Finding.model_validate(record).model_dump()
            e = state['entries'][record['key']]
            if any(f['issue_type'] == record['issue_type'] and f['target'] == record['target']
                   and f['basisRevisionId'] == e['currentRevisionId'] and f.get('glossaryVersion', 1) == state['glossaryVersion'] for f in e['importedFindings']):
                continue
            fid = 'backend:' + digest([record, e['currentRevisionId']])
            history = [j for j in state['backend'].get('findingJudgments', {}).get(fid, [])
                       if j.get('glossary_sha256') == digest(state['glossary'])]
            e['localFindings'].append(dict(record, id=fid, origin='backend', revisionId=e['currentRevisionId'],
                                           glossaryVersion=state['glossaryVersion'], sourceHash=e['sourceHash'], judgments=history))
        state['checks'] = {'status': 'complete', 'checkedAt': stamp(), 'scope': 'all', 'methods': ['dalel ' + __version__],
                           'localFindingCount': len(records), 'glossaryVersion': state['glossaryVersion']}
        state['backend']['analysis'] = coverage
        state['backend']['analysis']['checkedMessagesSha256'] = digest(target.strings)
        state['backend']['analysis']['checkedGlossarySha256'] = digest(state['glossary'])
        state['backend']['analysis']['checkedTargetSha256'] = hashlib.sha256(target.raw).hexdigest()
        state['backend']['language'] = {k: language.get(k) for k in ('identity', 'coverage', 'replay', 'provenance') if k in language}
        state['processing'] = {'externalCalls': 0, 'externalCost': 0, 'provider': 'dalel offline/replay'}
        if records:
            state['ui']['selectedKey'] = state['ui'].get('selectedKey') or records[0]['key']

    def analyze(self, ident, expected):
        with self.connect() as db:
            state = self._read(db, ident, expected)
        self._analyze(state)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); self._read(db, ident, expected)
            return self._write(db, state, 'analysis', {'coverage': state['backend']['analysis']})

    def save(self, ident, expected, candidate):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._read(db, ident, expected)
            if raw_digest(candidate) != raw_digest(state) or candidate.get('sourceLocale') != state['sourceLocale'] or candidate.get('targetLocale') != state['targetLocale']:
                raise ValueError('Imported ARB snapshots are immutable. Open a new workspace for different inputs.')
            changes = []
            for key, e in state['entries'].items():
                proposed = candidate.get('entries', {}).get(key)
                if not isinstance(proposed, dict):
                    raise ValueError('Workspace save omitted an entry')
                text = proposed.get('workingText')
                rid = proposed.get('currentRevisionId')
                if text is not None and not isinstance(text, str) or text is None and e['originalTarget'] is not None:
                    raise ValueError('Invalid working translation')
                if not isinstance(rid, str) or not rid:
                    raise ValueError('Every working text needs a revision identifier')
                if text != e['workingText'] or rid != e['currentRevisionId']:
                    old = e['currentRevisionId']
                    # Approved/superseded revisions cannot be rewritten or revived
                    # by changing a client ID, replaying a backup, or reverting text.
                    sealed = e['decisions'] or e['checkedRevisionId'] == old or any((f.get('revisionId') or f.get('basisRevisionId')) == old for bucket in ('localFindings', 'manualFindings', 'importedFindings') for f in e[bucket])
                    if (rid == old and sealed) or any(r['id'] == rid and r['id'] != old for r in e['revisions']):
                        rid = uid('rev-')
                    e['reviewHistory'].extend(e['decisions']); e['decisions'] = []
                    e['workingText'] = text; e['currentRevisionId'] = rid
                    if e['revisions'][-1]['id'] == rid:
                        e['revisions'][-1]['text'] = text
                    else:
                        e['revisions'].append({'id': rid, 'text': text, 'createdAt': stamp(), 'reason': 'Edited translation', 'author': candidate.get('session', {}).get('label') or 'Local draft'})
                    state['backend']['entryVersions'][key] += 1
                    e['checkedRevisionId'] = None
                    for name in ('undoStack', 'redoStack'):
                        values = proposed.get(name, [])
                        if not isinstance(values, list) or any(v is not None and not isinstance(v, str) for v in values):
                            raise ValueError('Invalid undo/redo history')
                        e[name] = values
                    self._validate_entry(state, key)
                    changes.append({'key': key, 'text': text, 'revision': rid})
            terms = normalize_terms(candidate.get('glossary', []))
            if terms != state['glossary']:
                state['glossaryHistory'] = state.get('glossaryHistory', []) + [{'before': state['glossary'], 'at': stamp()}]
                state['glossary'] = terms; state['glossaryVersion'] += 1
                for e in state['entries'].values():
                    e['terminologyStale'] = True
                    e['reviewHistory'].extend(e['decisions'])
                    e['decisions'] = []
                    for bucket in ('localFindings', 'importedFindings', 'manualFindings'):
                        for finding in e[bucket]:
                            finding.setdefault('judgmentHistory', []).extend(finding.get('judgments', []))
                            finding['judgments'] = []
            for e in state['entries'].values():
                e['glossaryFingerprint'] = digest(terms)
            name = candidate.get('name', state['name'])
            session = candidate.get('session', {})
            if not isinstance(name, str) or not name.strip() or not isinstance(session.get('label', ''), str):
                raise ValueError('Invalid workspace or reviewer name')
            state['name'] = name.strip()
            state['session'].update(label=session.get('label', '').strip(), nativeSelfDeclared=session.get('nativeSelfDeclared') is True)
            state['ui'] = copy.deepcopy(candidate.get('ui', {}))
            if not isinstance(state['ui'], dict):
                raise ValueError('Invalid workspace view state')
            # Client review flags, decisions, checks, releases and processing cost
            # are intentionally never accepted by this save endpoint.
            return self._write(db, state, 'draft-save', {'changes': changes, 'glossary_sha256': digest(terms)})

    def decide(self, ident, expected, payload):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); state = self._read(db, ident, expected)
            key = payload.get('key'); e = state['entries'].get(key)
            if e is None or payload.get('revisionId') != e['currentRevisionId'] or payload.get('text') != e['workingText']:
                raise Conflict('The displayed text revision changed. Reload it before recording a decision.')
            action = payload.get('action')
            if action not in ('approve', 'withdrawn', 'defer', 'request_changes', 'code_dependency', 'confirmed', 'dismissed'):
                raise ValueError('Unknown review action')
            actor = payload.get('reviewer')
            if not isinstance(actor, str) or not actor.strip():
                raise ValueError('Reviewer attribution is required (a local session label is sufficient)')
            human = payload.get('attestHuman') is True and not self.simulation
            if action in ('approve', 'confirmed', 'dismissed') and payload.get('attestHuman') is not True:
                raise ValueError('An explicit personal-review declaration is required')
            binding = {'entry_version': state['backend']['entryVersions'][key], 'source_sha256': digest(e['source']),
                       'original_target_sha256': digest(e['originalTarget']), 'text_sha256': digest(e['workingText']),
                       'glossary_sha256': digest(state['glossary'])}
            event = {'id': uid('decision-'), 'action': action, 'revisionId': e['currentRevisionId'], 'sourceHash': e['sourceHash'],
                     'at': stamp(), 'reviewer': actor.strip(), 'sessionId': state['session']['id'],
                     'nativeSelfDeclared': state['session']['nativeSelfDeclared'], 'reviewed_by_human': human, **binding}
            if action == 'approve':
                errors = self.validation(state, key, e['workingText'])
                if errors:
                    raise ValueError('Resolve the dalel validation errors before approval: ' + '; '.join(x['message'] for x in errors))
                # Simulation records are auditable but never activate human approval.
                if not human:
                    e['reviewHistory'].append(dict(event, action='simulated_approval'))
                else:
                    e['decisions'].append(event)
            elif action in ('confirmed', 'dismissed'):
                fid = payload.get('findingId')
                finding = next((f for bucket in ('localFindings', 'importedFindings', 'manualFindings') for f in e[bucket] if f['id'] == fid), None)
                if finding is None or (finding.get('revisionId') or finding.get('basisRevisionId')) != e['currentRevisionId'] or finding.get('glossaryVersion', 1) != state['glossaryVersion']:
                    raise Conflict('This finding belongs to an earlier text revision')
                if human:
                    judgment = dict(event, value=action)
                    finding['judgments'].append(judgment)
                    state['backend'].setdefault('findingJudgments', {}).setdefault(fid, []).append(judgment)
            else:
                e['decisions'].append(event)
            return self._write(db, state, 'review', {'key': key, 'event': event, 'finding_id': payload.get('findingId')})

    def manual(self, ident, expected, payload):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); state = self._read(db, ident, expected)
            e = state['entries'].get(payload.get('key'))
            if e is None or payload.get('revisionId') != e['currentRevisionId']:
                raise Conflict('The selected revision changed')
            note = payload.get('evidence')
            if not isinstance(note, str) or not note.strip():
                raise ValueError('Describe the concrete evidence')
            from dalel.engine.rules import fragment
            quote = fragment(e['workingText'] or e['source'] or '—', 65)
            evidence = 'Ручное замечание: ' + note.replace('«', '').replace('»', '')[:160] + '. Фрагмент: «' + quote + '».'
            record = Finding(key=e['key'], issue_type=payload.get('issue_type'), severity=payload.get('severity'),
                             source=e['source'], target=e['workingText'], evidence=evidence,
                             suggestion=payload.get('suggestion') or None, confidence=0.5).model_dump()
            e['manualFindings'].append(dict(record, id=uid('manual-'), manual_note=note, origin='manual',
                                           basisRevisionId=e['currentRevisionId'], glossaryVersion=state['glossaryVersion'], sourceHash=e['sourceHash'], judgments=[]))
            return self._write(db, state, 'manual-finding', {'key': e['key'], 'finding': record})

    def import_review(self, ident, expected, packet):
        from dalel.workspace.decisions import validate
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._read(db, ident, expected)
            entries = validate(state, packet)
            for decision in entries:
                key = decision['key']
                e = state['entries'][key]
                if decision['action'] == 'approve' and self.validation(state, key, e['workingText']):
                    raise ValueError('Reviewed translation fails structural validation')
                event = dict(id=uid('decision-'), action=decision['action'], reviewer=decision['reviewer'], at=decision['at'],
                    revisionId=e['currentRevisionId'], sourceHash=e['sourceHash'], sessionId=state['session']['id'],
                    reviewed_by_human=not self.simulation, nativeSelfDeclared=decision.get('nativeSelfDeclared') is True,
                    entry_version=state['backend']['entryVersions'][key], source_sha256=digest(e['source']),
                    original_target_sha256=digest(e['originalTarget']), text_sha256=digest(e['workingText']),
                    glossary_sha256=digest(state['glossary']))
                if self.simulation:
                    e['reviewHistory'].append(dict(event, action='simulated_' + event['action']))
                else:
                    e['decisions'].append(event)
            return self._write(db, state, 'import-review', {'packet': packet})

    @staticmethod
    def approval(state, key):
        e = state['entries'][key]
        event = next((d for d in reversed(e['decisions']) if d.get('revisionId') == e['currentRevisionId']), None)
        if event and event['action'] == 'approve' and event.get('reviewed_by_human') is True and event.get('entry_version') == state['backend']['entryVersions'][key] and event.get('source_sha256') == digest(e['source']) and event.get('text_sha256') == digest(e['workingText']) and event.get('glossary_sha256') == digest(state['glossary']):
            return event
        return None

    def release(self, ident, expected, mode):
        if mode not in ('validated', 'draft'):
            raise ValueError('Unknown release selection')
        self.verify(ident)
        with self.connect() as db:
            state = self._read(db, ident, expected)
        from dalel.workspace.output import select_output
        return select_output(state, expected, mode, documents=self._documents(state),
            get_approval=self.approval, validate=self.validation, fingerprint=self.fingerprint, guidance=self.guidance)

    def flutter(self, ident, expected, mode):
        """Compile selected messages through the explicit Flutter name adapter."""
        from dalel.delivery.flutter import find_flutter, FlutterRunner, key_mapping, normalize_document, VALID_NAME
        release = self.release(ident, expected, mode)
        state = self.get(ident)
        output = release['files'][release['filename']].encode('utf8')
        folder = self.directory / ident / 'flutter' / uid()
        executable = find_flutter()
        result = {'status': 'not_run', 'reason': 'Flutter SDK not found. Set LOCQA_FLUTTER or add Flutter to PATH.'}
        if executable:
            runner = FlutterRunner(executable, folder)
            version = runner.version()
            result = {'status': version['status'], 'sdk': version}
            if version['status'] == 'passed':
                srcname = 'intl_' + state['sourceLocale'] + '.arb'
                targetname = 'intl_' + state['targetLocale'] + '.arb'
                source = document(state['snapshots']['source']['raw'], state['sourceLocale'], 'source')
                selected = ArbDocument(output)
                mapping, actions = key_mapping([source, selected])
                inputs = {srcname: normalize_document(source, mapping, actions),
                          targetname: normalize_document(selected, mapping, actions, source)}
                generated = runner.generate('selected', inputs, template=srcname, escaping=state['backend']['escaping'])
                log = Path(generated['log']).read_text(encoding='utf8')
                excluded = [k for k in source.strings if not VALID_NAME.fullmatch(k)]
                invalid = [k for k in selected.strings if not VALID_NAME.fullmatch(k)]
                template = source.raw
                literal = dict(status='failed', invalid_target_ids=invalid, invalid_source_ids=excluded, excluded_source_ids=[],
                               target_sha256=hashlib.sha256(output).hexdigest(), source_template_sha256=hashlib.sha256(template).hexdigest())
                exact = runner.generate('literal', {srcname: template, targetname: output}, template=srcname,
                                            escaping=state['backend']['escaping'])
                literal.update(status=exact['generation'], exit_code=exact['exit_code'])
                literal_log = Path(exact['log']).read_text(encoding='utf8')
                result = {'status': literal['status'], 'literal_target': literal,
                          'adapted_bundle': {'status': generated['generation'], 'input_sha256': generated.get('input_sha256', {})},
                          'sdk': version, 'exit_code': literal.get('exit_code'),
                          'details': 'Literal target:\n' + literal_log[-3000:] + '\nAdapted bundle:\n' + log[-3000:],
                          'adapter': {'key_map': {k: v for k, v in mapping.items() if k != v},
                          'actions': actions, 'compiled_input_sha256': {k: hashlib.sha256(v).hexdigest() for k, v in inputs.items()}}}
                if generated['generation'] == 'passed':
                    artifacts = {'flutter/arb/' + k: v.decode('utf8') for k, v in inputs.items()}
                    artifacts['flutter/key_map.json'] = json.dumps(result['adapter'], ensure_ascii=False, indent=2) + '\n'
                    for name in ('pubspec.yaml', 'l10n.yaml'):
                        configuration = folder/'selected'/name
                        if configuration.is_file():
                            artifacts['flutter/' + name] = configuration.read_text(encoding='utf8')
                    result['artifacts'] = artifacts
        result.update(output_sha256=hashlib.sha256(output).hexdigest(), mode=mode, checked_at=stamp())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); state = self._read(db, ident, expected)
            state['backend']['flutter'] = result
            return self._write(db, state, 'flutter-validation', result)

    def prepare(self, ident, expected, mode):
        release = self.release(ident, expected, mode)
        if release['blocked']:
            raise ValueError('Reviewed release requires valid selected messages, resolved code dependencies and successful literal Flutter compilation')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE'); state = self._read(db, ident, expected)
            release['contentFingerprint'] = self.fingerprint(state)
            state['releases'] = (state['releases'] + [release])[-5:]
            return self._write(db, state, 'prepare-release', {'release': release['id'], 'selection': mode})

    @staticmethod
    def fingerprint(state):
        return digest({'inputs': raw_digest(state), 'entries': [[k, e['workingText'], e['currentRevisionId'], e['decisions'], e['manualFindings'], e['importedFindings'], e['localFindings']] for k, e in state['entries'].items()],
                       'glossary': state['glossary'], 'analysis': state['backend']['analysis'], 'flutter': state['backend'].get('flutter'), 'session': state['session']})
