import copy
import json
import pytest
from dalel.review.screen import prepare,render,apply,validate_import
from dalel.formats.arb import ArbDocument
from test_production import document

def fixture():
    source=document('en',entry='Open {item}',second='Open {item}')
    target=document('de',entry='Zu {item}',second='Zu {item}')
    rows=[{'key':key,'source':'Open {item}','target':'Zu {item}','suggestion':'Auf {item}',
        'issue_type':'machine_translation_suspect','severity':'high','confidence':.9,
        'evidence':'В «Zu {item}» закрытие вместо открытия из исходника.','reviewed_by_human':False}
        for key in ('entry','second')]
    payload=prepare(source,target,rows,simulation=True)
    decisions={'format':1,'review_id':payload['review_id'],'mode':'simulation','attest_human':False,
        'reviewer':'Operator dry run','entries':[{'key':'entry','card_hash':payload['cards'][0]['card_hash'],
         'action':'accept','text':'Öffne {item}','reviewer':'Operator dry run','at':'2026-01-01T00:00:00Z'},
        {'key':'second','card_hash':payload['cards'][1]['card_hash'],'action':'reject','text':None,
         'reviewer':'Operator dry run','at':'2026-01-01T00:00:01Z'}]}
    return source,target,payload,decisions

def test_offline_simulated_decisions_apply_only_as_simulation_and_preserve_snapshot():
    source,target,payload,decisions=fixture()
    result=apply(payload,decisions,source,target,simulation=True)
    fixed=ArbDocument(result['fixed'])
    assert fixed.strings=={'entry':'Öffne {item}','second':'Zu {item}'}
    assert result['summary']['human_accepted_strings']==0
    assert not any(r['reviewed_by_human'] for r in result['changes']+result['report'])
    with pytest.raises(ValueError,match='Simulation'):
        apply(payload,decisions,source,target)
    with pytest.raises(ValueError,match='inputs changed'):
        apply(payload,decisions,source,document('de',entry='Changed'),simulation=True)
    bad=copy.deepcopy(decisions);bad['entries'][0]['text']='Öffne'
    with pytest.raises(ValueError,match='Unsafe'):
        apply(payload,bad,source,target,simulation=True)
    bad=copy.deepcopy(decisions);bad['entries'][0]['card_hash']='stale'
    with pytest.raises(ValueError,match='card changed'):
        apply(payload,bad,source,target,simulation=True)
    bad=copy.deepcopy(decisions);bad['entries'].append(bad['entries'][0])
    with pytest.raises(ValueError,match='duplicate'):
        apply(payload,bad,source,target,simulation=True)

def test_offline_payload_cannot_break_out_of_script_and_does_not_claim_human_review():
    source,target,payload,decisions=fixture();payload['cards'][0]['target']='</script><script>bad()</script>'
    page=render(payload)
    assert '</script><script>bad()' not in page
    assert '\\u003c/script\\u003e' in page
    assert 'localStorage' in page and 'connect-src' in page and 'application/json' in page
    with pytest.raises(ValueError,match='manifest hash'):
        validate_import(payload,decisions,source,target,simulation=True)
