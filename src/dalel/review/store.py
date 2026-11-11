"""Explicit review decisions with immutable snapshots and an append-only audit."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import re
from datetime import datetime,timezone
from pathlib import Path
from dalel.formats.arb import ArbDocument
from dalel.delivery.comparison import detector_digest
from dalel.formats.messages import validate_suggestion
from dalel.contracts import Finding
from dalel.engine.hygiene import LINE_REFLOW

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def digest(value):
    return hashlib.sha256(canonical(value).encode('utf8')).hexdigest()

class _ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

class ReviewStore:
    def __init__(self,path,*,actor='synthetic-test',human=False):
        if not isinstance(actor,str) or not actor.strip():
            raise ValueError('A named reviewer is required')
        if not isinstance(human,bool):
            raise ValueError('Reviewer provenance must be explicit')
        self.path=Path(path);self.actor=actor.strip();self.human=human;self._contexts={}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,source BLOB NOT NULL,target BLOB NOT NULL,dependencies TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY,snapshot TEXT NOT NULL REFERENCES snapshots(id),payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,snapshot TEXT NOT NULL REFERENCES snapshots(id),entry_key TEXT NOT NULL,
                    payload TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_entry ON events(snapshot,entry_key,seq);
            ''')
            for table in ('snapshots','findings','events'):
                for action in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable review history'); END")
        self.verify()

    def connect(self):
        db=sqlite3.connect(self.path,timeout=30,factory=_ClosingConnection);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON');return db

    def verify(self):
        previous='0'*64
        with self.connect() as db:
            for row in db.execute('SELECT * FROM events ORDER BY seq'):
                event=json.loads(row['payload'])
                if (row['previous_hash']!=previous or digest([previous,event])!=row['event_hash']
                        or event['seq']!=row['seq'] or event['snapshot']!=row['snapshot'] or event['key']!=row['entry_key']):
                    raise ValueError('Review event hash chain is invalid')
                previous=row['event_hash']
        return previous

    def register(self,source,target,findings,*,dependencies=None):
        dependencies={**(dependencies or {}),'rules':detector_digest()}
        source_values,target_values=source.strings,target.strings
        ident=digest([hashlib.sha256(source.raw).hexdigest(),hashlib.sha256(target.raw).hexdigest(),dependencies])
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)',(ident,source.raw,target.raw,canonical(dependencies)))
            for finding in findings:
                finding=Finding.model_validate(finding).model_dump();key=finding['key']
                if key not in source_values and key not in target_values or finding['source']!=source_values.get(key) or finding['target']!=target_values.get(key):
                    raise ValueError('Finding does not belong to the review snapshot')
                fid=digest([ident,finding])
                db.execute('INSERT OR IGNORE INTO findings VALUES(?,?,?)',(fid,ident,canonical(finding)))
        self._contexts.pop(ident,None)
        return ident

    def documents(self,snapshot):
        with self.connect() as db:
            row=db.execute('SELECT * FROM snapshots WHERE id=?',(snapshot,)).fetchone()
        if row is None:
            raise ValueError('Unknown review snapshot')
        return ArbDocument(row['source']),ArbDocument(row['target']),json.loads(row['dependencies'])

    def findings(self,snapshot):
        with self.connect() as db:
            return [dict(id=r['id'],**json.loads(r['payload'])) for r in db.execute('SELECT * FROM findings WHERE snapshot=? ORDER BY rowid',(snapshot,))]

    def state(self,snapshot,key,db=None,*,include_simulated=False):
        owned=db is None;db=db or self.connect()
        try:
            state={'draft':None,'approval':None,'revision':0,'findings':{}}
            for row in db.execute('SELECT payload FROM events WHERE snapshot=? AND entry_key=? ORDER BY seq',(snapshot,key)):
                event=json.loads(row['payload']);action=event['action'];state['revision']=event['seq']
                if not event['human'] and not include_simulated:
                    continue
                if action=='draft':
                    state['draft']=event['text']
                if action=='approve':
                    state['approval']=event;state['draft']=event['text']
                if action=='revoke':
                    state['approval']=None
                if event.get('finding_id'):
                    status={'approve':'confirmed','confirm':'confirmed','link':'confirmed','reject':'rejected','defer':'deferred'}.get(action)
                    if status:
                        state['findings'][event['finding_id']]={'status':status,'event':event,
                            'approval_seq':event['seq'] if action=='approve' else event.get('approval_seq')}
            return state
        finally:
            if owned:
                db.close()

    def decide(self,snapshot,key,action,*,revision,text=None,finding_id=None,reason=''):
        if action not in ('draft','approve','revoke','confirm','reject','defer','link'):
            raise ValueError('Unknown review action')
        source,target,dependencies=self.documents(snapshot)
        source_values,target_values=source.strings,target.strings
        if key not in source_values and key not in target_values:
            raise ValueError('Unknown review entry')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self.state(snapshot,key,db,include_simulated=not self.human)
            if state['revision']!=revision:
                raise ValueError('Stale review card; reload before deciding')
            finding=None
            if finding_id:
                row=db.execute('SELECT * FROM findings WHERE id=? AND snapshot=?',(finding_id,snapshot)).fetchone()
                finding=json.loads(row['payload']) if row else None
                if finding is None or finding['key']!=key:
                    raise ValueError('Finding is not part of this entry')
            if action in ('confirm','reject','defer','link') and finding is None:
                raise ValueError('Choose exactly one finding')
            if action=='reject' and not reason.strip():
                raise ValueError('Rejection needs a reason')
            if action in ('draft','approve') and not isinstance(text,str):
                raise ValueError('Draft or approval needs exact text')
            if action=='approve':
                if text==target_values.get(key):
                    raise ValueError('Unchanged text is not an improved revision')
                errors=validate_suggestion(source_values.get(key) or '',text,
                    escaping=bool(dependencies.get('escaping')),original_target=target_values.get(key),
                    allow_line_reflow=bool(finding and LINE_REFLOW.search(finding['evidence'])))
                if target.locale.replace('-','_').split('_')[0]=='kk':
                    if snapshot not in self._contexts:
                        from dalel.engine.context import CorpusPolicy
                        rows=[{'key':k,'source':source_values.get(k),'target':target_values.get(k)}
                              for k in dict.fromkeys([*source_values,*target_values])]
                        self._contexts[snapshot]=CorpusPolicy(rows,self.findings(snapshot),dependencies.get('glossary'))
                    errors.extend(e['rule'] for e in self._contexts[snapshot].fix_errors(source_values.get(key),target_values.get(key),text))
                if errors:
                    raise ValueError('Unsafe approved text: '+'; '.join(errors))
            if action=='link' and state['approval'] is None:
                raise ValueError('No active approval to link')
            last=db.execute('SELECT seq,event_hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            seq=last['seq']+1 if last else 1;previous=last['event_hash'] if last else '0'*64
            event={'seq':seq,'snapshot':snapshot,'key':key,'action':action,'finding_id':finding_id,
                'text':text,'reason':reason,'actor':self.actor,'human':self.human,
                'at':datetime.now(timezone.utc).isoformat(),'source_hash':digest(source_values.get(key)),
                'original_target_hash':digest(target_values.get(key)),
                'text_hash':digest(text),'approval_seq':state['approval']['seq'] if action=='link' else None}
            event['allow_line_reflow']=bool(action=='approve' and finding and LINE_REFLOW.search(finding['evidence']))
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(seq,snapshot,key,canonical(event),previous,digest([previous,event])))
        return event

    def export(self,snapshot,*,simulation=False):
        self.verify();source,target,dependencies=self.documents(snapshot)
        source_values,target_values=source.strings,target.strings
        findings=self.findings(snapshot)
        with self.connect() as db:
            states={key:self.state(snapshot,key,db,include_simulated=simulation)
                for key in dict.fromkeys([*source_values,*target_values])}
        changes=[];edits={};additions={}
        for key,state in states.items():
            event=state['approval']
            if event is None or not (event['human'] or simulation):
                continue
            before=target_values.get(key);after=event['text']
            errors=validate_suggestion(source_values.get(key) or '',after,
                escaping=bool(dependencies.get('escaping')),original_target=before,
                allow_line_reflow=event.get('allow_line_reflow',False))
            if errors:
                raise ValueError('An active approval no longer passes structural validation')
            if key in target_values:
                edits[key]=after
            else:
                additions[key]=after
            human=bool(event['human'] and not simulation)
            changes.append({'key':key,'before':before,'after':after,'source_hash':event['source_hash'],
                'before_hash':event['original_target_hash'],'after_hash':event['text_hash'],
                'author':event['actor'],'at':event['at'],'reviewed_by_human':human,'revision':event['seq']})
        # Effective occurrences only. Missing strings follow their source
        # predecessor; unrelated duplicate occurrences and bytes survive.
        fixed=ArbDocument(target.render(edits))
        fixed=ArbDocument(fixed.insert_in_source_order(additions,list(source_values)))
        for change in changes:
            key='@'+change['key'];previous=fixed.effective.get(key)
            if previous and not isinstance(previous.value,dict):
                raise ValueError('Existing message metadata is not an object; manual resolution required')
            metadata=dict(previous.value) if previous else {}
            metadata['reviewed_by_human']=change['reviewed_by_human']
            old_provenance=metadata.get('x-locqa',{})
            if not isinstance(old_provenance,dict):
                raise ValueError('Existing review metadata is not an object; manual resolution required')
            metadata['x-locqa']={**old_provenance,**{k:change[k] for k in ('source_hash','before_hash','after_hash','author','at','revision')}}
            fixed=ArbDocument(fixed.render({key:metadata}) if previous else fixed.insert(key,metadata,after=change['key']))
        report=[]
        for finding in findings:
            fid=finding.pop('id');state=states[finding['key']];decision=state['findings'].get(fid)
            if decision and decision['status'] in ('rejected','deferred'):
                continue
            human=bool(decision and decision['status']=='confirmed' and decision['event']['human'] and not simulation)
            finding['reviewed_by_human']=human
            approval=state['approval']
            if decision and approval and decision['approval_seq']==approval['seq'] and (approval['human'] or simulation):
                finding['suggestion']=approval['text']
            values=[v for v in (finding['source'],finding['target'],finding['suggestion']) if isinstance(v,str)]
            for quote in re.findall('«([^«»]*)»',finding['evidence']):
                if not quote or not any(quote in value for value in values):
                    finding['evidence']=finding['evidence'].replace('«'+quote+'»',quote)
            if not re.findall('«([^«»]+)»',finding['evidence']):
                fragment=next((v[:60] for v in (finding['target'],finding['source']) if v),'')
                suffix=' Исходный фрагмент: «'+fragment+'».'
                finding['evidence']=finding['evidence'][:300-len(suffix)].rstrip()+suffix
            Finding.model_validate(dict(finding,reviewed_by_human=False))
            report.append(finding)
        with self.connect() as db:
            log=[json.loads(r['payload'])|{'event_hash':r['event_hash'],'previous_hash':r['previous_hash']}
                 for r in db.execute('SELECT * FROM events ORDER BY seq')]
        return {'fixed':fixed.raw,'report':report,'changes':changes,'review_log':log,
            'summary':{'snapshot':snapshot,'mode':'simulation' if simulation else 'human_review',
                'target_locale':target.locale,
                'changed_strings':len(changes),'human_accepted_strings':sum(c['reviewed_by_human'] for c in changes),
                'human_confirmed_findings':sum(r['reviewed_by_human'] for r in report),'event_chain_head':self.verify()}}
