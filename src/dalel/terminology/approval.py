"""Named, snapshot-bound human approval of proposed glossary rows."""
from __future__ import annotations
import copy
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from dalel.terminology.glossary import rows,sidecar
from dalel.review.screen import canonical,sha,render


def prepare(glossary,*,simulation=False):
    glossary=Path(glossary);policy_path=sidecar(glossary)
    policy=json.loads(policy_path.read_text(encoding='utf8'))
    source=policy['source_locale'];target=policy['target_locale'];entries=rows(glossary,include_candidates=True)
    cards=[]
    for entry in entries:
        rule=entry['_rule'];key=sha(canonical([entry[source],entry[target]]).encode('utf8'))
        card={'key':key,'label':'Термин: '+entry[source],'source':entry[source],'target':entry[target],'suggestion':entry[target],
              'findings':[{'issue_type':'предложение термина','severity':'проверка',
              'evidence':entry.get('comment','')+' · '+('Утверждён человеком' if rule.get('status')=='human_approved' else 'Не утверждён человеком')}],
              'entry':entry}
        cards.append(dict(card,card_hash=sha(canonical(card).encode('utf8'))))
    payload={'format':1,'topic':'glossary','mode':'simulation' if simulation else 'human',
        'csv_sha256':sha(glossary.read_bytes()),'rules_sha256':sha(policy_path.read_bytes()),
        'source_locale':source,'target_locale':target,'cards':cards,'report':[]}
    payload['review_id']=sha(canonical(payload).encode('utf8'));return payload


def write_screen(glossary,out,*,simulation=False):
    payload=prepare(glossary,simulation=simulation);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    page=render(payload).replace('Проверка исправлений','Утверждение глоссария')
    page=page.replace('locqa apply-review.','locqa apply-glossary-review.')
    page=page.replace('Предлагаемое исправление','Утверждаемая форма термина')
    page=page.replace('Нет исправлений для проверки.','Нет терминов для проверки.')
    page=page.replace('решения ещё нужно применить к ARB','решения ещё нужно применить к глоссарию')
    page=page.replace('принять показанный текст и находки','утвердить показанный термин')
    page=page.replace('подтверждаю только прочитанные исправления','утверждаю только проверенные термины')
    (out/'glossary_review.html').write_bytes(page.encode('utf8'))
    (out/'glossary_review_manifest.json').write_bytes((canonical(payload)+'\n').encode('utf8'))
    return {'terms':len(payload['cards']),'mode':payload['mode'],'review_id':payload['review_id']}


def apply(payload,decisions,glossary,*,simulation=False):
    glossary=Path(glossary)
    if payload.get('csv_sha256')!=sha(glossary.read_bytes()) or payload.get('rules_sha256')!=sha(sidecar(glossary).read_bytes()):
        raise ValueError('Glossary snapshot changed')
    current=prepare(glossary,simulation=simulation)
    if payload!=current:raise ValueError('Glossary snapshot or review manifest changed')
    if decisions.get('format')!=1 or decisions.get('review_id')!=payload['review_id']:
        raise ValueError('Glossary decisions belong to a different review')
    mode='simulation' if simulation else 'human'
    if decisions.get('mode')!=mode or payload['mode']!=mode:raise ValueError('Simulation cannot approve glossary terms')
    if decisions.get('attest_human') is not (not simulation):raise ValueError('Explicit human attestation is required')
    reviewer=decisions.get('reviewer')
    if not isinstance(reviewer,str) or not reviewer.strip():raise ValueError('Named reviewer is required')
    cards={r['key']:r for r in payload['cards']};chosen={}
    for item in decisions.get('entries',[]):
        key=item.get('key');card=cards.get(key)
        if not card or key in chosen:raise ValueError('Unknown or duplicate glossary decision')
        if item.get('card_hash')!=card['card_hash'] or item.get('reviewer')!=reviewer:raise ValueError('Glossary card or reviewer mismatch')
        stamp=datetime.fromisoformat(item.get('at','').replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError('Glossary decision needs timezone')
        if item.get('action') not in ('accept','reject'):raise ValueError('Unknown glossary action')
        if item['action']=='accept' and (not isinstance(item.get('text'),str) or not item['text'].strip()):
            raise ValueError('Approved term must be nonempty text')
        chosen[key]=item
    entries=copy.deepcopy(payload['cards']);source=payload['source_locale'];target=payload['target_locale'];accepted=0
    for card in entries:
        item=chosen.get(card['key']);entry=card['entry'];rule=entry['_rule']
        if item and not simulation:
            if item['action']=='accept':
                entry[target]=item['text'];rule['target_term']=item['text']
                rule.update(status='human_approved',enforce=True,reviewed_by_human=True,
                    approval={'reviewer':reviewer,'at':item['at'],'attest_human':True,'review_id':payload['review_id']})
                entry['status']='human_approved';accepted+=1
            else:
                rule.update(status='human_rejected',enforce=False,reviewed_by_human=False)
                rule.pop('approval',None);entry['status']='human_rejected'
    values=[c['entry'] for c in entries]
    fields=list(dict.fromkeys([source,target,'en','comment','status'] + [k for entry in values for k in entry if k != '_rule']))
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=fields,lineterminator='\n');writer.writeheader()
    for entry in values:
        entry['status'] = 'human_approved' if entry.get('_rule', {}).get('status') == 'human_approved' else 'ai_proposed'
        writer.writerow({k:entry.get(k,'') for k in fields})
    policy=json.loads(sidecar(glossary).read_text(encoding='utf8'));policy['terms']=[r['_rule'] for r in values]
    policy['human_acceptances']=sum(r.get('status')=='human_approved' for r in policy['terms'])
    return {'csv':buffer.getvalue().encode('utf8'),'rules':policy,
        'summary':{'human_approved_terms':accepted,'human_accepted_strings':0,'mode':mode,'review_id':payload['review_id']}}
