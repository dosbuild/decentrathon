import pytest
from dalel.engine.hygiene import FixValidator
from dalel.engine.regeneration import validate_regeneration,regeneration_request

def test_regeneration_can_decline_but_cannot_publish_unsafe_or_unattested_text():
    row={'key':'new_case','source':'{count} книг','target':'{count} кітап'}
    validator=FixValidator(['кітап'])
    record={'key':'new_case','suggestion':None,'rationale':'Исправление не требуется.','requires_code_change':False}
    assert not validate_regeneration(record,row,validator)
    assert 'earlier proposal is untrusted' in regeneration_request([row])
    record['suggestion']='{count} кітаптар'
    assert any(e['rule']=='plural_after_count' for e in validate_regeneration(record,row,validator))
    record['suggestion']='кітап'
    assert any(e['rule']=='protected_structure' for e in validate_regeneration(record,row,validator))
    record['suggestion']='{count} библиотекочкасы'
    assert any(e['rule']=='unattested_new_token' for e in validate_regeneration(record,row,validator))
    record['requires_code_change']='yes'
    with pytest.raises(ValueError):
        validate_regeneration(record,row,validator)
