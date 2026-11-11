import copy
import json
import pytest
from dalel.engine.prompts import TYPES
from dalel.engine.verification import blind_rows,verification_request,validate_verification,approved_fix,apply_verified_fixes,normalize_verification_quotes

def test_source_caps_formats_only_icu_text_nodes_and_preserves_exact_verdict():
    from dalel.engine.verification import source_case_format
    text='<g>Кітап</g> {bookTitle}: {n, plural, one{бір кітап} other{{n} кітап}} https://example.test/Read %s'
    result=source_case_format('КНИГА',text)
    assert result=='<g>КІТАП</g> {bookTitle}: {n, plural, one{БІР КІТАП} other{{n} КІТАП}} https://example.test/Read %s'
    assert source_case_format('Книга',text)==text
    assert source_case_format('I','Ich')=='Ich'
    assert source_case_format('КНИГА',result)==result

def sample():
    candidates=[{'key':'syntheticLabel','source':'Спокойной ночи','target':'Қайырлы тан',
                 'proposals':[{'text':'Қайырлы түн','support':[]}]}]
    rows,mapping=blind_rows(candidates)
    original=mapping['syntheticLabel']['original_label']
    fixed=next(k for k in rows[0]['variants'] if k!=original)
    issue={'issue_type':'machine_translation_suspect','severity':'high',
           'evidence':'Вместо ночи указано утро: «Спокойной ночи» не соответствует переводу.'}
    variants=[{'label':label,'issues':[issue] if label==original else [],
               'negative_types':[t for t in TYPES if label!=original or t!='machine_translation_suspect'],
               'rationale':'Смысл и форма проверены.','quote':value}
              for label,value in rows[0]['variants'].items()]
    record={'key':'syntheticLabel','variants':variants,'preferred':fixed,
            'strict_improvement':True,'improvement_verdict':'yes','comparison':'Исправлено время суток.','requires_code_change':False}
    return rows,mapping,record

def test_blind_request_has_no_previous_verdict_or_original_identity():
    rows,mapping,record=sample()
    request=json.loads(verification_request(rows))
    assert request['assigned']==rows
    assert 'original_label' not in verification_request(rows)
    assert 'support' not in verification_request(rows)
    assert blind_rows([{'key':'x','source':'S','target':'T','proposals':[]}])[0][0]['variants']=={'A':'T'}
    validate_verification({'rows':[record]},rows)
    fix=approved_fix(record,mapping[record['key']],rows[0]['source'])
    assert fix and fix['text']=='Қайырлы түн' and not fix['reviewed_by_human']


def test_verification_rules_follow_the_requested_target_locale():
    kk=json.loads(verification_request([],target_locale='kk'))['rules']
    uz=json.loads(verification_request([],target_locale='uz'))['rules']
    assert any('Kazakh count nouns' in rule for rule in kk)
    assert not any('Kazakh' in rule or 'ablative' in rule for rule in uz)
    assert any('actual target grammar' in rule for rule in uz)

def test_ties_preference_for_original_and_unresolved_defects_never_become_fixes():
    rows,mapping,record=sample();assignment=mapping[record['key']]
    for change in ({'strict_improvement':False},{'preferred':None,'strict_improvement':False},
                   {'preferred':assignment['original_label']}):
        candidate=record|change
        if not candidate['strict_improvement']:
            candidate['improvement_verdict']='no'
        validate_verification({'rows':[candidate]},rows)
        assert approved_fix(candidate,assignment,rows[0]['source']) is None
    bad=copy.deepcopy(record)
    by_label={v['label']:v for v in bad['variants']}
    by_label[bad['preferred']]['issues']=by_label[assignment['original_label']]['issues']
    assert approved_fix(bad,assignment,rows[0]['source']) is None
    style=copy.deepcopy(record)
    for variant in style['variants']:
        for issue in variant['issues']:issue['severity']='low'
    assert approved_fix(style,assignment,rows[0]['source']) is None

def test_incomplete_type_or_variant_coverage_and_fabricated_quotes_are_rejected():
    rows,mapping,record=sample()
    for mutate in (lambda r:r['variants'].pop(),lambda r:r['variants'][0]['negative_types'].clear(),
                   lambda r:r['variants'][0].update(quote='Несуществующий фрагмент')):
        candidate=copy.deepcopy(record);mutate(candidate)
        with pytest.raises(ValueError):
            validate_verification({'rows':[candidate]},rows)


def test_explicit_flat_issue_is_losslessly_recovered_but_missing_decisions_fail():
    rows,mapping,record=sample()
    variant=next(v for v in record['variants'] if v['issues'])
    issue=variant.pop('issues')[0];variant.update(issue)
    normalized,audit=normalize_verification_quotes({'rows':[record]},rows)
    assert any(a['action']=='explicit_flat_issue_to_list' for a in audit)
    validate_verification(normalized,rows)
    recovered=next(v for v in normalized['rows'][0]['variants'] if v['issues'])
    assert recovered['issues']==[issue]
    del variant['severity']
    incomplete,_=normalize_verification_quotes({'rows':[record]},rows)
    with pytest.raises(ValueError,match='Missing explicit type'):
        validate_verification(incomplete,rows)

def test_a_structurally_unsafe_preference_is_recorded_but_never_accepted():
    rows,mapping,record=sample();assignment=mapping[record['key']]
    assert approved_fix(record,assignment,'Спокойной ночи, {name}') is None
    class RejectVocabulary:
        def check(self,source,before,after,**kwargs):
            return [{'rule':'unattested_new_token'}]
    assert approved_fix(record,assignment,rows[0]['source'],RejectVocabulary()) is None
    uncertain=record|{'improvement_verdict':'unsure','strict_improvement':False}
    validate_verification({'rows':[uncertain]},rows)
    assert approved_fix(uncertain,assignment,rows[0]['source']) is None


def test_publication_drops_no_unsure_pending_fixes_without_dropping_findings():
    rows,mapping,record=sample();key=record['key']
    candidates=[{'key':key,'source':rows[0]['source'],'target':mapping[key]['variants'][mapping[key]['original_label']],
                 'proposals':[{'text':'Қайырлы түн'}]}]
    findings=[{'key':key,'source':candidates[0]['source'],'target':candidates[0]['target'],
        'issue_type':'machine_translation_suspect','severity':'high','evidence':'Нужна ночь: «Қайырлы түн».',
        'suggestion':'Қайырлы түн','confidence':.9,'reviewed_by_human':False}]
    for answer in ('no','unsure','pending'):
        verdict=record|{'improvement_verdict':answer,'strict_improvement':False,'preferred':None}
        accepted={} if answer=='pending' else {key:{'verdict':verdict,'assignment':mapping[key]}}
        report,audit,_=apply_verified_fixes(findings,accepted,candidates)
        assert len(report)==1 and report[0]['suggestion'] is None and not report[0]['reviewed_by_human']
        assert audit[0]['finding_retained'] and audit[0]['original_evidence']==findings[0]['evidence']
    accepted={key:{'verdict':record,'assignment':mapping[key]}}
    report,_,_=apply_verified_fixes(findings,accepted,candidates)
    assert report[0]['suggestion']=='Қайырлы түн'
    misclassified=dict(findings[0],issue_type='length_overflow',severity='low')
    corrected,audit,_=apply_verified_fixes([misclassified,*findings],accepted,candidates)
    assert len(corrected)==1 and corrected[0]['issue_type']=='machine_translation_suspect' and corrected[0]['severity']=='high'
    assert len(audit)==2
    changed=copy.deepcopy(candidates);changed[0]['proposals'][0]['text']='Өзге мәтін'
    with pytest.raises(ValueError,match='assignment'):
        apply_verified_fixes(findings,accepted,changed)
    changed=copy.deepcopy(candidates);changed[0]['source']='Желаем доброго вечера'
    with pytest.raises(ValueError,match='assignment'):
        apply_verified_fixes(findings,accepted,changed)
    stale=copy.deepcopy(findings);stale[0]['target']='Жаңа түпнұсқа'
    with pytest.raises(ValueError,match='snapshots'):
        apply_verified_fixes(stale,accepted,candidates)


@pytest.mark.parametrize('previous_type',['machine_translation_suspect','orthography'])
def test_current_verified_fix_refreshes_evidence_and_disproved_type(previous_type):
    rows,mapping,verdict=sample();key=verdict['key'];assignment=mapping[key]
    candidates=[{'key':key,'source':rows[0]['source'],'target':assignment['variants'][assignment['original_label']],
                 'proposals':[{'text':'Қайырлы түн'}]}]
    old={'key':key,'source':candidates[0]['source'],'target':candidates[0]['target'],
         'issue_type':previous_type,'severity':'medium','evidence':'Устаревшее объяснение: «Қайырлы тан».',
         'suggestion':None,'confidence':.85,'reviewed_by_human':False}
    result,audit,_=apply_verified_fixes([old],{key:{'verdict':verdict,'assignment':assignment}},candidates)
    current=next(v for v in verdict['variants'] if v['label']==assignment['original_label'])['issues'][0]
    assert result[0]['evidence']==current['evidence'] and result[0]['issue_type']==current['issue_type']
    assert result[0]['suggestion']=='Қайырлы түн'
    assert audit[0]['original_evidence']==old['evidence'] and audit[0]['original_issue_type']==previous_type


def test_original_defect_support_is_separate_from_whether_a_fix_wins():
    from dalel.engine.verification import original_issues
    from dalel.engine.hygiene import FixValidator
    rows,mapping,record=sample();assignment=mapping[record['key']]
    original=next(v for v in record['variants'] if v['label']==assignment['original_label'])
    assert original_issues(record,assignment,rows[0]['source'])
    record.update(preferred=None,strict_improvement=False,improvement_verdict='no')
    assert original_issues(record,assignment,rows[0]['source'])
    original['issues']=[];original['negative_types']=list(TYPES)
    assert not original_issues(record,assignment,rows[0]['source'])
    original['issues']=[{'issue_type':'glossary_violation','severity':'medium',
        'evidence':'Вариант отличается от предпочитаемого термина.'}]
    assert not original_issues(record,assignment,rows[0]['source'],FixValidator(approved_glossary=[]))


def test_verification_gloss_normalization_preserves_judgments_and_requires_literal_evidence():
    rows,mapping,record=sample();original=mapping[record['key']]['original_label']
    variant=next(v for v in record['variants'] if v['label']==original)
    variant['issues'][0]['evidence']+=' Пояснение: «семантическая инверсия».'
    with pytest.raises(ValueError):
        validate_verification({'rows':[record]},rows)
    normalized,audit=normalize_verification_quotes({'rows':[record]},rows)
    validate_verification(normalized,rows)
    assert audit and normalized['rows'][0]['improvement_verdict']=='yes'
    fixed=next(v for v in normalized['rows'][0]['variants'] if v['label']==original)
    assert '«семантическая инверсия»' not in fixed['issues'][0]['evidence']
    assert 'семантическая инверсия' in fixed['issues'][0]['evidence']
    bad=copy.deepcopy(record)
    for variant in bad['variants']:
        variant['quote']='Фрагмент отсутствует во входе'
    normalized,_=normalize_verification_quotes({'rows':[bad]},rows)
    with pytest.raises(ValueError):
        validate_verification(normalized,rows)

def test_absent_variant_uses_source_quote_without_inventing_a_translation_or_judgment():
    rows,mapping,record=sample();original=mapping[record['key']]['original_label']
    rows[0]['variants'][original]=None
    variant=next(v for v in record['variants'] if v['label']==original)
    variant.update(quote='',issues=[{'issue_type':'untranslated','severity':'high',
                   'evidence':'Перевод исходника отсутствует.'}],
                   negative_types=[t for t in TYPES if t!='untranslated'])
    with pytest.raises(ValueError):
        validate_verification({'rows':[record]},rows)
    normalized,audit=normalize_verification_quotes({'rows':[record]},rows)
    validate_verification(normalized,rows)
    converted=next(v for v in normalized['rows'][0]['variants'] if v['label']==original)
    assert converted['quote']==rows[0]['source'] and rows[0]['variants'][original] is None
    assert converted['issues'][0]['issue_type']=='untranslated'
    assert converted['negative_types']==variant['negative_types']
    assert normalized['rows'][0]['improvement_verdict']==record['improvement_verdict']
    assert any(item.get('action')=='absent_variant_source_quote' for item in audit)
