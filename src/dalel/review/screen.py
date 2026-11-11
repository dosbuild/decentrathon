"""Self-contained keyboard review and snapshot-bound decision import."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from dalel.resources import PACKAGE_ROOT
from dalel.formats.arb import ArbDocument
from dalel.engine.corrections import apply_ai_changes
from dalel.contracts import Finding

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def sha(raw):return hashlib.sha256(raw).hexdigest()

def prepare(source,target,findings,*,simulation=False,gate=None,glossary=()):
    records=[Finding.model_validate(row).model_dump() for row in findings];grouped={}
    for row in records:
        key=row['key'];text=row['suggestion']
        if row['source']!=source.strings.get(key) or row['target']!=target.strings.get(key):
            raise ValueError('Review finding snapshot mismatch')
        if text is None or text.startswith('requires_code_change'):
            continue
        card=grouped.setdefault(key,{'key':key,'source':row['source'],'target':row['target'],
                                    'suggestion':text,'findings':[]})
        if card['suggestion']!=text:raise ValueError('Conflicting suggestions for review card')
        card['findings'].append({'issue_type':row['issue_type'],'severity':row['severity'],'evidence':row['evidence']})
    cards=[]
    for card in grouped.values():
        if glossary:
            from dalel.terminology.glossary import source_match
            card['glossary']=[{'source':r[source.locale],'target':r[target.locale]} for r in glossary
                if r.get(source.locale) and r.get(target.locale) and source_match(card['source'] or '',r[source.locale],r.get('_rule'))]
        cards.append(dict(card,card_hash=sha(canonical(card).encode('utf8'))))
    cards.sort(key=lambda c:(-max({'high':2,'medium':1,'low':0}[f['severity']] for f in c['findings']),c['key']))
    if gate is not None:
        excluded=set(gate.get('excluded_keys',[]));cards=[card for card in cards if card['key'] not in excluded]
    if gate is not None and gate.get('passed') is not True:cards=[]
    if glossary and (gate is None or gate.get('passed') is True):
        from dalel.terminology.review import attach
        cards=attach(cards,source,target,glossary)
    payload={'format':1,'mode':'simulation' if simulation else 'human',
        'source_sha256':sha(source.raw),'target_sha256':sha(target.raw),'report':records,'cards':cards,
        'source_locale':source.locale,'target_locale':target.locale}
    if glossary:
        from dalel.terminology.review import digest
        payload.update(glossary_entries=list(glossary),glossary_digest=digest(list(glossary)),
            source_locale=source.locale,target_locale=target.locale)
    if gate is not None:payload['acceptance_gate']=gate
    payload['review_id']=sha(canonical(payload).encode('utf8'))
    return payload

def render(payload):
    encoded=canonical(payload).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    from dalel.presentation.report import TYPE_NAMES,SEVERITY_NAMES
    return HTML.replace('__PAYLOAD__',encoded).replace('__TYPE_NAMES__',canonical(TYPE_NAMES)).replace('__SEVERITY_NAMES__',canonical(SEVERITY_NAMES))

def write_screen(source,target,findings,out,*,simulation=False,gate=None,glossary=()):
    payload=prepare(source,target,findings,simulation=simulation,gate=gate,glossary=glossary);out=Path(out)
    out.mkdir(parents=True,exist_ok=True)
    (out/'review.html').write_bytes(render(payload).encode('utf8'))
    (out/'review_manifest.json').write_bytes((canonical(payload)+'\n').encode('utf8'))
    return {'cards':len(payload['cards']),'message_cards':sum(c.get('kind')!='canon' for c in payload['cards']),
        'canon_cards':sum(c.get('kind')=='canon' for c in payload['cards']),
        'mode':payload['mode'],'review_id':payload['review_id']}

def validate_import(payload,decisions,source,target,*,simulation=False):
    if payload.get('acceptance_gate',{}).get('passed') is False:
        raise ValueError('Fixed-file quality gate has not passed; human acceptance is unavailable')
    original=dict(payload);review_id=original.pop('review_id')
    if sha(canonical(original).encode('utf8'))!=review_id:raise ValueError('Review manifest hash mismatch')
    if payload['source_sha256']!=sha(source.raw) or payload['target_sha256']!=sha(target.raw):
        raise ValueError('Review inputs changed; old decisions cannot apply')
    if decisions.get('review_id')!=review_id or decisions.get('format')!=1:
        raise ValueError('Decisions belong to a different review')
    mode='simulation' if simulation else 'human'
    if payload['mode']!=mode or decisions.get('mode')!=mode:
        raise ValueError('Simulation decisions cannot become human approval')
    if not simulation and decisions.get('attest_human') is not True:
        raise ValueError('Explicit human-review attestation is missing')
    if simulation and decisions.get('attest_human') is not False:
        raise ValueError('Simulation must not attest human review')
    reviewer=decisions.get('reviewer')
    if not isinstance(reviewer,str) or not reviewer.strip():raise ValueError('Named reviewer is required')
    cards={r['key']:r for r in payload['cards']};seen=set();result={}
    for decision in decisions.get('entries',[]):
        key=decision.get('key')
        if key in seen or key not in cards:raise ValueError('Unknown or duplicate review entry')
        seen.add(key);card=cards[key]
        if decision.get('card_hash')!=card['card_hash']:raise ValueError('Review card changed')
        if decision.get('reviewer')!=reviewer:raise ValueError('Reviewer identity mismatch')
        stamp=datetime.fromisoformat(decision.get('at','').replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('Decision timestamp requires timezone')
        if decision.get('action') not in ('accept','reject'):raise ValueError('Unknown review action')
        if decision['action']=='accept' and not isinstance(decision.get('text'),str):
            raise ValueError('Accepted decision needs exact text')
        result[key]=decision
    from dalel.terminology.review import validate
    validate(payload,result)
    return result

def apply(payload,decisions,source,target,*,simulation=False):
    chosen=validate_import(payload,decisions,source,target,simulation=simulation)
    records=copy.deepcopy(payload['report'])
    for row in records:
        decision=chosen.get(row['key'])
        if decision:
            row['suggestion']=decision['text'] if decision['action']=='accept' else None
        values=[v for v in (row['source'],row['target'],row['suggestion']) if isinstance(v,str)]
        for quote in re.findall('«([^«»]*)»',row['evidence']):
            if not quote or not any(quote in value for value in values):
                row['evidence']=row['evidence'].replace('«'+quote+'»',quote)
        if not re.findall('«([^«»]+)»',row['evidence']):
            fragment=next((v[:60] for v in (row['target'],row['source']) if v),'')
            suffix=' Исходный фрагмент: «'+fragment+'».'
            row['evidence']=row['evidence'][:300-len(suffix)].rstrip()+suffix
        Finding.model_validate(row)
    provenance={'review_id':payload['review_id'],'decisions_sha256':sha(canonical(decisions).encode('utf8'))}
    fixed,changes=apply_ai_changes(source,target,records,provenance=provenance,
        glossary=payload.get('glossary_entries', []),
        accepted_keys={key for key, decision in chosen.items() if decision['action'] == 'accept'})
    doc=ArbDocument(fixed);accepted=0
    for change in changes:
        decision=chosen.get(change['key']);human=bool(not simulation and decision and decision['action']=='accept')
        verdict={'status':'accepted' if human else 'unreviewed',
                 'name':decision['reviewer'] if human else None,'at':decision['at'] if human else None}
        change.update(reviewed_by_human=human,human_verdict=verdict,
            finding_types=[r['issue_type'] for r in records if r['key']==change['key']],
            verification_verdict='human_edited_or_accepted' if human else 'independent_ai_verification')
        if human:
            accepted+=1;name='@'+change['key'];metadata=dict(doc.effective[name].value)
            metadata.update(reviewed_by_human=True)
            metadata['x-locqa']={**metadata['x-locqa'],'reviewer':decision['reviewer'],
                                 'reviewed_at':decision['at'],'review_stage':'human_accepted'}
            doc=ArbDocument(doc.render({name:metadata}))
    for row in records:
        decision=chosen.get(row['key'])
        row['reviewed_by_human']=bool(not simulation and decision and decision['action']=='accept')
    from dalel.terminology.review import apply_terms
    glossary=apply_terms(payload,chosen,simulation=simulation)
    message_chosen={k:v for k,v in chosen.items() if not k.startswith('@canon:')}
    return {'fixed':doc.raw,'changes':changes,'report':records,'decisions':decisions,'glossary':glossary,
        'summary':{'mode':'simulation' if simulation else 'human','changed_strings':len(changes),
            'human_accepted_strings':accepted,'accepted_decisions':sum(d['action']=='accept' for d in message_chosen.values()),
            'rejected_decisions':sum(d['action']=='reject' for d in message_chosen.values()),
            'human_approved_terms':glossary['summary']['human_approved_terms'] if glossary else 0,
            'review_id':payload['review_id'],'decisions_sha256':provenance['decisions_sha256']}}

HTML = (PACKAGE_ROOT / 'assets/views/review.html').read_text(encoding='utf-8')
