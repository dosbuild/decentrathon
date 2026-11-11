import copy
import json
import pytest
from dalel.terminology.approval import prepare,apply,write_screen


def test_glossary_simulation_cannot_create_authority_and_inputs_are_bound(tmp_path):
    path=tmp_path/'glossary.csv';path.write_bytes(b'en,de,status\nBook,Buch,ai_proposed\n')
    rule={'source_term':'Book','target_term':'Buch','status':'ai_proposed','enforce':False,'reviewed_by_human':False}
    (tmp_path/'glossary_rules.json').write_bytes(json.dumps({'format':1,'source_locale':'en','target_locale':'de','terms':[rule]}).encode())
    payload=prepare(path,simulation=True);card=payload['cards'][0]
    decisions={'format':1,'review_id':payload['review_id'],'mode':'simulation','attest_human':False,'reviewer':'Synthetic dry run',
        'entries':[{'key':card['key'],'card_hash':card['card_hash'],'reviewer':'Synthetic dry run',
                    'action':'accept','text':'Werk','at':'2026-01-01T00:00:00Z'}]}
    result=apply(payload,decisions,path,simulation=True)
    assert result['summary']['human_approved_terms']==0 and result['summary']['human_accepted_strings']==0
    assert result['rules']['terms'][0]==rule and b'Buch' in result['csv']
    with pytest.raises(ValueError):apply(payload,decisions,path)
    bad=copy.deepcopy(decisions);bad['entries'][0]['card_hash']='wrong'
    with pytest.raises(ValueError,match='card'):apply(payload,bad,path,simulation=True)
    write_screen(path,tmp_path/'screen',simulation=True)
    assert 'locqa apply-glossary-review' in (tmp_path/'screen/glossary_review.html').read_text(encoding='utf8')
    path.write_bytes(b'en,de,status\nBook,Werk,ai_proposed\n')
    with pytest.raises(ValueError,match='snapshot'):apply(payload,decisions,path,simulation=True)


def test_ai_preference_alone_cannot_justify_a_fix():
    from dalel.engine.hygiene import FixValidator
    validator=FixValidator()
    issues=[{'issue_type':'glossary_violation','severity':'medium','evidence':'Не соответствует термину.'}]
    assert validator.original_issues('Награда','Сыйлық',issues)==[]
    real=[{'issue_type':'machine_translation_suspect','severity':'high','evidence':'Потерян смысл.'}]
    assert validator.original_issues('Книга','Есік',real)==real
