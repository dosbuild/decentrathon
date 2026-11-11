import copy

import pytest

from dalel.engine.prompts import TYPES,validate_decisions,decode_json,normalize_evidence_quotes


def example():
    rows = [{'key':'syntheticArrival','source':'Гость прибыл.','target':'Қонақ келді.'}]
    payload = {'rows':[{'key':'syntheticArrival','negative_types':list(TYPES),
                        'negative_rationale':'Смысл исходника передан; формальные элементы сохранены.',
                        'negative_quote':'Қонақ','issues':[]}]}
    return rows,payload


def test_dense_negative_contract():
    rows,payload = example()
    assert len(validate_decisions(payload,rows))==1
    for mutation in ('missing_key','missing_type','duplicate_key','invented_quote'):
        bad = copy.deepcopy(payload)
        if mutation=='missing_key':
            bad['rows']=[]
        elif mutation=='missing_type':
            bad['rows'][0]['negative_types'].pop()
        elif mutation=='duplicate_key':
            bad['rows']*=2
        else:
            bad['rows'][0]['negative_quote']='Бұл мәтінде жоқ.'
        with pytest.raises(ValueError):
            validate_decisions(bad,rows)


def test_response_truncation_is_never_a_negative():
    with pytest.raises(ValueError,match='Truncated'):
        decode_json({'choices':[{'finish_reason':'length','message':{'content':'{"rows":[]}'}}]})


def test_metalinguistic_gloss_is_not_published_as_a_literal_quote():
    rows,payload = example()
    payload['rows'][0]['issues']=[{'issue_type':'machine_translation_suspect',
        'evidence':'Фрагмент «Қонақ» означает «посетитель», по правилу нужно проверить контекст.',
        'suggestion':None}]
    normalized,audit = normalize_evidence_quotes(payload,rows)
    assert '«посетитель»' not in normalized['rows'][0]['issues'][0]['evidence']
    assert '«Қонақ»' in normalized['rows'][0]['issues'][0]['evidence']
    assert audit[0]['unquoted_gloss']=='посетитель'
    assert '«посетитель»' in payload['rows'][0]['issues'][0]['evidence']
def test_complete_rows_recovery_never_invents_missing_fields():
    from dalel.engine.prompts import complete_response_rows,expand_compact,TYPES
    response={'choices':[{'message':{'content':'{"rows":[{"key":"one","negative_codes":"0123456789a"},{"key":"two","unfinished":'}}]}
    rows=complete_response_rows(response)
    assert len(rows)==1 and rows[0]['key']=='one'
    assert expand_compact(rows[0])['negative_types']==list(TYPES)
    import pytest
    with pytest.raises(ValueError):
        expand_compact({'negative_codes':'00'})

