import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from dalel.formats.arb import ArbDocument
from dalel.cli import app
from dalel.terminology.glossary import Matcher,rows
from dalel.engine.rules import check,LOANS
from dalel.delivery.replay import package_report,load,ReplayMismatch
from test_production import document


def test_evidence_fragment_keeps_complete_words():
    from dalel.engine.rules import fragment
    assert fragment('Longer firstword and another',12)=='Longer'
    assert fragment('Unbreakable',4)=='Unbreakable'


def test_uzbek_compound_possession_keeps_term_identity_without_derivation():
    matcher=Matcher()
    for text,term in [('pochta qutingizga','pochta qutisi'),
                      ('yuk mashinangizda','yuk mashinasi'),
                      ('bolalar bog‘changizdan','bolalar bog‘chasi')]:
        assert matcher.contains(text,term,'uz')
    for text,term in [('pochta qutichasiga','pochta qutisi'),
                      ('yuk mashinasozlik','yuk mashinasi'),
                      ('taks','taksi'),('ona va yuk mashinasi','ona mashinasi')]:
        assert not matcher.contains(text,term,'uz')

def write(path,value):path.write_bytes(json.dumps(value,ensure_ascii=False).encode('utf8'))

def test_glossary_inside_sentence_accepts_inflection_but_not_accidental_substrings(tmp_path):
    glossary=tmp_path/'glossary.csv';glossary.write_bytes('ru,kk\nкнигу,кітап\n'.encode('utf8'))
    source=document('ru',read='Открой книгу в меню')
    correct=document('kk',read='Мәзірден кітапты аш')
    wrong=document('kk',read='Мәзірден журналды аш')
    assert not any(f.issue_type=='glossary_violation' for f in check(source,correct,glossary=glossary)[0])
    findings,diagnostics=check(source,wrong,glossary=glossary)
    found=next(f for f in findings if f.issue_type=='glossary_violation')
    assert found.suggestion is None and '«книгу»' in found.evidence
    assert any(d.get('reason')=='contextual_term_rephrase' for d in diagnostics)
    matcher=Matcher(LOANS)
    assert matcher.contains('үш кітап бар','кітаптар','kk')
    assert matcher.contains('кітабын оқы','кітап','kk')
    assert matcher.contains('темір есікті аш','темір есік','kk')
    assert matcher.contains('бас тартыңыз','бас тарту','kk')
    assert matcher.contains('жоба кітаптары','жоба кітабы','kk')
    assert matcher.contains('жоба кітабына','жоба кітаптары','kk')
    assert matcher.contains('боулингтегі ойын','боулинг','kk')
    assert matcher.contains('химияның тарихы','химия','kk')
    assert not matcher.contains('боулингхана','боулинг','kk')
    assert not matcher.contains('кітапхананы аш','кітап','kk')
    assert not matcher.contains('есік пен темір','темір есік','kk')


def test_participle_glossary_identity_accepts_inflection_without_swapping_verbs(tmp_path):
    matcher=Matcher(LOANS)
    assert matcher.contains('ашылмаған','ашылған','kk')
    assert matcher.contains('ашылғанына','ашылмаған','kk')
    assert matcher.contains('көрінбеген','көрінген','kk')
    assert not matcher.contains('жабылған','ашылған','kk')
    assert not matcher.contains('ашылген','ашылған','kk')
    assert not matcher.contains('ашылмаған','ашылған','de')
    glossary=tmp_path/'glossary.csv';glossary.write_bytes('ru,kk\nОткрытый,ашылған\n'.encode('utf8'))
    source=document('ru',state='Открытый');target=document('kk',state='ашылмаған')
    # The semantic reversal remains a meaning finding; grammar alone cannot
    # establish a second glossary defect for the same verb concept.
    meaning={'key':'state','source':'Открытый','target':'ашылмаған','issue_type':'machine_translation_suspect',
        'severity':'high','evidence':'Вместо «Открытый» перевод «ашылмаған» отрицает открытие.',
        'suggestion':None,'confidence':.9,'reviewed_by_human':False}
    manifest=package_report(source,target,[meaning],tmp_path/'replay',glossary=glossary,
        coverage={'status':'complete','source_strings_evaluated':1},provenance={'producer':'external'})
    assert load(manifest,source,target,glossary=glossary)['language']['report']==[meaning]


def test_wrong_meaning_is_not_recast_as_a_terminology_preference_after_merge(tmp_path):
    glossary=tmp_path/'glossary.csv';glossary.write_bytes(b'en,de\nDoor,Tuer\n')
    source=document('en',label='Door',caps='DOOR',sentence='Open the Door')
    target=document('de',label='Fenster',caps='FENSTER',sentence='Schliesse das Fenster')
    def meaning(key,original,translated,fix):
        return {'key':key,'source':original,'target':translated,'issue_type':'machine_translation_suspect',
            'severity':'high','evidence':f'Смысл «{original}» искажён в «{translated}».',
            'suggestion':fix,'confidence':.9,'reviewed_by_human':False}
    records=[meaning('label','Door','Fenster','Tuer'),meaning('caps','DOOR','FENSTER','TUER'),
             meaning('sentence','Open the Door','Schliesse das Fenster',None)]
    manifest=package_report(source,target,records,tmp_path/'replay',glossary=glossary,
        coverage={'status':'complete','source_strings_evaluated':3},provenance={'producer':'external'})
    result=load(manifest,source,target,glossary=glossary)['language']
    assert [r['issue_type'] for r in result['report'] if r['key']=='label']==['machine_translation_suspect']
    assert [r['issue_type'] for r in result['report'] if r['key']=='caps']==['machine_translation_suspect']
    # A longer sentence can contain two separate defects: action and term.
    assert {r['issue_type'] for r in result['report'] if r['key']=='sentence'}=={'machine_translation_suspect','glossary_violation'}
    assert any(d['type']=='merged_same_defect' for d in result['diagnostics'])

def test_explicit_candidate_policy_cannot_become_enforced_by_csv_alone(tmp_path):
    glossary=tmp_path/'glossary.csv';glossary.write_bytes(b'en,de\nBook,Buch\n')
    policy={'format':1,'source_locale':'en','target_locale':'de','terms':[
        {'source_term':'Book','target_term':'Buch','enforce':False,'status':'ai_verified_candidate'}]}
    write(tmp_path/'glossary_rules.json',policy)
    assert rows(glossary)==[] and len(rows(glossary,include_candidates=True))==1
    policy['terms'][0]['enforce']=True;write(tmp_path/'glossary_rules.json',policy)
    assert rows(glossary)==[]
    policy['terms'][0]['status']='human_approved'
    policy['terms'][0]['approval']={'reviewer':'Test reviewer','at':'2026-01-01T00:00:00Z','attest_human':True}
    write(tmp_path/'glossary_rules.json',policy)
    assert len(rows(glossary))==1
    glossary.write_bytes(b'en,de\nBook,Buch\nDesk,Tisch\n')
    with pytest.raises(ValueError,match='no matching policy'):rows(glossary)


def test_ai_glossary_guides_fixes_but_cannot_publish_synonym_findings(tmp_path):
    from dalel.terminology.glossary import gate_findings,policy_map
    glossary=tmp_path/'glossary.csv';glossary.write_bytes(b'en,de,status\nBook,Buch,ai_proposed\n')
    rule={'source_term':'Book','target_term':'Buch','enforce':True,'status':'ai_proposed'}
    write(tmp_path/'glossary_rules.json',{'format':1,'source_locale':'en','target_locale':'de','terms':[rule]})
    entries=rows(glossary,include_candidates=True)
    assert policy_map(entries,'en','de')=={'Book':'Buch'} and rows(glossary)==[]
    source=document('en',label='Book');target=document('de',label='Werk')
    assert not any(f.issue_type=='glossary_violation' for f in check(source,target,glossary=glossary)[0])
    record={'key':'label','source':'Book','target':'Werk','issue_type':'glossary_violation'}
    kept,diagnostics=gate_findings([record],entries,'en','de')
    assert kept==[] and diagnostics[0]['type']=='unapproved_glossary_preference'
    # A contradictory stale replay cannot bypass the same authority check.
    full=dict(record,severity='medium',evidence='Термин «Werk» отличается от глоссария.',suggestion=None,confidence=.8,reviewed_by_human=False)
    manifest=package_report(source,target,[full],tmp_path/'replay',glossary=glossary,
        coverage={'status':'complete','source_strings_evaluated':1},provenance={'producer':'external'})
    assert load(manifest,source,target,glossary=glossary)['language']['report']==[]
    rule.update(status='human_approved',approval={'reviewer':'Reviewer','at':'2026-01-01T00:00:00Z','attest_human':True},allowed_variants=['Werk'])
    write(tmp_path/'glossary_rules.json',{'format':1,'source_locale':'en','target_locale':'de','terms':[rule]})
    assert gate_findings([record],rows(glossary),'en','de')[0]==[]


def test_binding_ui_preference_accepts_a_valid_cancel_synonym(tmp_path):
    glossary=tmp_path/'glossary.csv';glossary.write_bytes('ru,kk,status\nОтмена,Бас тарту,ai_proposed\n'.encode())
    write(tmp_path/'glossary_rules.json',{'format':1,'source_locale':'ru','target_locale':'kk','terms':[
        {'source_term':'Отмена','target_term':'Бас тарту','status':'ai_proposed','enforce':False}]})
    source=document('ru',label='Отмена');target=document('kk',label='Болдырмау')
    assert not any(f.issue_type=='glossary_violation' for f in check(source,target,glossary=glossary)[0])

def test_whole_message_scope_honors_aliases_without_leaking_into_embedded_senses(tmp_path):
    glossary=tmp_path/'glossary.csv';glossary.write_bytes(b'en,de\nBook,Buch\n')
    write(tmp_path/'glossary_rules.json',{'format':1,'source_locale':'en','target_locale':'de','terms':[
        {'source_term':'Book','source_forms':['Books'],'target_term':'Buch','enforce':True,'match':'whole_message_casefold'}]})
    source=document('en',label='BOOKS',action='Book a seat')
    target=document('de',label='Heft',action='Reserviere einen Platz')
    found=[f for f in check(source,target,glossary=glossary)[0] if f.issue_type=='glossary_violation']
    assert len(found)==1 and found[0].key=='label' and found[0].suggestion=='BUCH'

def test_replay_preserves_inactive_glossary_and_binds_policy_bytes(tmp_path):
    glossary=tmp_path/'glossary.csv';glossary.write_bytes(b'en,de\nBook,Buch\n')
    policy={'format':1,'source_locale':'en','target_locale':'de','terms':[
        {'source_term':'Book','target_term':'Buch','enforce':False}]}
    rules=tmp_path/'glossary_rules.json';write(rules,policy)
    source=document('en',label='Book');target=document('de',label='Werk')
    manifest=package_report(source,target,[],tmp_path/'bundle',glossary=glossary,
        coverage={'status':'complete','source_strings_evaluated':1},provenance={'producer':'external'})
    loaded=load(manifest,source,target,glossary=glossary)
    assert rows(loaded['glossary'])==[] and not loaded['language']['report']
    policy['terms'][0]['enforce']=True;write(rules,policy)
    with pytest.raises(ReplayMismatch,match='policy'):load(manifest,source,target,glossary=glossary)

def test_bare_cli_discovers_real_locale_siblings_and_ignores_fixed_or_same_language_files(tmp_path,monkeypatch):
    docs={'ru':document('ru',help='Пожалуйста откройте окно с настройками'),
          'kk':document('kk',help='Please open the window with settings'),
          'en':document('en',help='Please open the window with settings'),
          'kk_Latn':document('kk_Latn',help='Some unrelated Latin output')}
    for locale,doc in docs.items():(tmp_path/f'intl_{locale}.arb').write_bytes(doc.raw)
    (tmp_path/'intl_kk_fixed.arb').write_bytes(document('kk',help='Fixed').raw)
    monkeypatch.chdir(tmp_path)
    result=CliRunner().invoke(app,['check','--source','intl_ru.arb','--target','intl_kk.arb','--out','report.json'])
    assert result.exit_code==0,result.output
    findings=json.loads((tmp_path/'report.json').read_text(encoding='utf8'))
    assert len(findings)==1 and findings[0]['issue_type']=='wrong_language'
    metadata=json.loads((tmp_path/'report.run.json').read_text(encoding='utf8'))
    assert metadata['compatibility']['reference_locales']==['en']


def test_term_identity_keeps_instrumental_and_soft_sign_loan_inflections():
    from dalel.terminology.glossary import Matcher
    matcher=Matcher()
    assert matcher.contains('Қаламдарымен жазады','қалам','kk')
    assert matcher.contains('Сөздігіммен жұмыс','сөздік','kk')
    assert matcher.contains('Лагерімде қалды','лагерь','kk')
    assert not matcher.contains('Лагерімен','қалам','kk')
    assert not matcher.contains('Қаламгермен','қалам','kk')
    assert matcher.contains('Иелердің тізімі','иесі','kk')
    assert matcher.contains('сатып алынған зат','сатып алынды','kk')
    assert not matcher.contains('сатылған зат','сатып алынды','kk')


def test_explicit_source_sense_exclusion_does_not_disable_other_uses():
    from dalel.terminology.glossary import source_match
    rule={'source_forms':['Books'],'source_exclusions':['Book a seat']}
    assert source_match('Book a seat','Book',rule) is None
    assert source_match('BOOK A SEAT','Book',rule) is None
    assert source_match('Read the book','Book',rule)=='book'
    assert source_match('Books available','Book',rule)=='Books'
