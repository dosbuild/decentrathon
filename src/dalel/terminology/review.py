"""Canon-first review dependencies; term approval never approves messages."""
import copy
import csv
import hashlib
import io
import json
from datetime import datetime
from dalel.terminology.glossary import source_match,Matcher


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()


def attach(cards,source,target,terms):
    proposed={card['key']:card['suggestion'] for card in cards};canons=[]
    for entry in terms:
        src=entry.get(source.locale);tgt=entry.get(target.locale)
        if not src or not tgt:continue
        rule=entry.get('_rule',{})
        siblings=[{'key':key,'source':value,'target':target.strings.get(key),
                   'proposed':proposed.get(key,target.strings.get(key))}
                  for key,value in source.strings.items() if source_match(value,src,rule)]
        if not siblings:continue
        key='@canon:'+digest([source.locale,target.locale,src,rule.get('scope'),rule.get('match')])
        card={'kind':'canon','key':key,'label':'Сначала термин: '+src,'source':src,'target':tgt,
            'suggestion':tgt,'entry':entry,'siblings':siblings,
            'findings':[{'issue_type':'Канон термина','severity':'Проверка',
                'evidence':entry.get('comment','')+' · Решение о термине не принимает переводы.'}]}
        card['card_hash']=digest(card);canons.append(card)
        sibling_keys={r['key'] for r in siblings}
        for message in cards:
            if message['key'] in sibling_keys:
                message.setdefault('canon_dependencies',[]).append(key)
                message.setdefault('term_context',[]).append({'canon_key':key,'source':src,'target':tgt,'siblings':siblings})
    for card in cards:
        card['kind']='message';card.pop('card_hash',None);card['card_hash']=digest(card)
    return sorted(canons,key=lambda c:c['source'].casefold())+cards


def validate(payload,chosen):
    cards={r['key']:r for r in payload['cards']};matcher=Matcher()
    for key,decision in chosen.items():
        card=cards[key]
        if card.get('kind')=='canon':
            if decision['action']=='accept' and not decision['text'].strip():raise ValueError('Accepted canon must be nonempty')
            continue
        if decision['action']!='accept':continue
        dependencies=card.get('canon_dependencies',[])
        bound=decision.get('canon_choices',{})
        if set(bound)!=set(dependencies):raise ValueError('Message decision lacks its canon choices')
        for dependency in dependencies:
            canon=chosen.get(dependency)
            if not canon or canon['action']!='accept':raise ValueError('Approve the canon before its dependent messages')
            if bound[dependency]!={'text':canon['text'],'at':canon['at']}:raise ValueError('Canon changed; dependent message needs re-review')
            if datetime.fromisoformat(decision['at'].replace('Z','+00:00'))<datetime.fromisoformat(canon['at'].replace('Z','+00:00')):
                raise ValueError('Canon decision must precede message acceptance')
            locale=payload.get('target_locale','')
            if not matcher.contains(decision['text'],canon['text'],locale):
                raise ValueError('Accepted message does not use its approved canon')


def apply_terms(payload,chosen,*,simulation=False):
    entries=copy.deepcopy(payload.get('glossary_entries',[]))
    if not entries:return None
    source=payload['source_locale'];target=payload['target_locale'];accepted=0;changed=[]
    for entry in entries:
        entry.setdefault('_rule',{'source_term':entry[source],'target_term':entry[target],
            'enforce':True,'status':'adopter_supplied','source_forms':[],'allowed_variants':[]})
    indexed={(r[source],r[target]):r for r in entries}
    for card in payload['cards']:
        if card.get('kind')!='canon' or card['key'] not in chosen:continue
        decision=chosen[card['key']];entry=indexed[(card['entry'][source],card['entry'][target])]
        if simulation:continue
        rule=entry.setdefault('_rule',{})
        if decision['action']=='accept':
            if entry[target]!=decision['text']:changed.append(card['key'])
            entry[target]=decision['text'];rule.update(source_term=entry[source],target_term=entry[target],
                status='human_approved',enforce=True,reviewed_by_human=True,
                approval={'reviewer':decision['reviewer'],'at':decision['at'],
                    'attest_human':True,'review_id':payload['review_id']})
            accepted+=1
        else:
            rule.update(status='human_rejected',enforce=False,reviewed_by_human=False);rule.pop('approval',None)
    fields=list(dict.fromkeys([source,target,'en','comment','status'] + [k for entry in entries for k in entry if k != '_rule']))
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=fields,lineterminator='\n');writer.writeheader()
    for entry in entries:
        entry['status'] = 'human_approved' if entry.get('_rule', {}).get('status') == 'human_approved' else 'ai_proposed'
        writer.writerow({k:entry.get(k,'') for k in fields})
    return {'csv':buffer.getvalue().encode('utf8'),
        'rules':{'format':1,'source_locale':source,'target_locale':target,'terms':[r.get('_rule',{}) for r in entries],
            'human_acceptances':sum(r.get('_rule',{}).get('status')=='human_approved' for r in entries)},
        'summary':{'human_approved_terms':accepted,'changed_canon_keys':changed,'human_accepted_strings':0}}
