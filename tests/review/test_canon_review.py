import copy
import pytest
from dalel.review.screen import prepare,apply
from test_offline_review import fixture


def queue():
    source,target,payload,decisions=fixture()
    terms=[{'en':'Open','de':'Auf','comment':'Команда открытия'}]
    payload=prepare(source,target,payload['report'],simulation=True,glossary=terms)
    decisions.update(review_id=payload['review_id'],entries=[])
    return source,target,payload,decisions


def decision(card,text,at,**extra):
    return dict(key=card['key'],card_hash=card['card_hash'],action='accept',text=text,
        reviewer='Operator dry run',at=at,**extra)


def test_canon_first_scope_includes_unchanged_siblings_and_requires_acceptance():
    source,target,payload,decisions=queue()
    term,message=payload['cards'][:2]
    assert term['kind']=='canon'
    assert {s['key'] for s in term['siblings']}=={'entry','second'}
    assert message['canon_dependencies']==[term['key']]
    assert message['term_context'][0]['siblings']==term['siblings']
    decisions['entries']=[decision(message,'Auf {item}','2026-01-01T00:00:02Z')]
    with pytest.raises(ValueError,match='canon choices'):apply(payload,decisions,source,target,simulation=True)
    chosen=decision(term,'Auf','2026-01-01T00:00:00Z')
    decisions['entries']=[chosen,decision(message,'Auf {item}','2026-01-01T00:00:02Z',
        canon_choices={term['key']:{'text':chosen['text'],'at':chosen['at']}})]
    result=apply(payload,decisions,source,target,simulation=True)
    assert result['summary']['human_approved_terms']==0
    assert result['summary']['human_accepted_strings']==0
    assert result['summary']['accepted_decisions']==1
    assert result['glossary']['csv'].splitlines()[0]==b'en,de,comment,status'


def test_changed_canon_invalidates_old_message_acceptance_and_checks_edited_text():
    source,target,payload,decisions=queue();term,message=payload['cards'][:2]
    chosen=decision(term,'Öffne','2026-01-01T00:00:01Z')
    old=decision(message,'Auf {item}','2026-01-01T00:00:02Z',
        canon_choices={term['key']:{'text':'Auf','at':'2026-01-01T00:00:00Z'}})
    decisions['entries']=[chosen,old]
    with pytest.raises(ValueError,match='needs re-review'):apply(payload,decisions,source,target,simulation=True)
    old['canon_choices'][term['key']]={'text':chosen['text'],'at':chosen['at']}
    with pytest.raises(ValueError,match='approved canon'):apply(payload,decisions,source,target,simulation=True)
    old['text']='Öffne {item}'
    assert apply(payload,decisions,source,target,simulation=True)['summary']['accepted_decisions']==1
    old['at']='2025-12-31T23:59:59Z'
    with pytest.raises(ValueError,match='must precede'):apply(payload,decisions,source,target,simulation=True)
