import csv
import json
from types import SimpleNamespace
from typer.testing import CliRunner
from dalel.cli import app
from dalel.terminology.glossary import rows,policy_map
from dalel.engine.pipeline import analyze
from dalel.engine.rules import check
from test_production import document,ModelFixture,inputs


def test_scoped_duplicate_terms_and_explicit_variants_are_not_flattened(tmp_path):
    path=tmp_path/'terms.csv'
    fields=['en','de','notes','de_variants','scope']
    entries=[dict(zip(fields,['crane','Kran','Lifting machine','Hebekran|Turmkran','Dock panel'])),
             dict(zip(fields,['crane','Kranich','A bird','','Wildlife panel']))]
    with path.open('w',encoding='utf8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');writer.writeheader();writer.writerows(entries)
    assert rows(path)==entries and policy_map(rows(path),'en','de')=={}
    source=document('en',lifting='crane',bird='crane',unknown='crane',
        **{'@lifting':{'description':'Dock panel'},'@bird':{'description':'Wildlife panel'}})
    target=document('de',lifting='Turmkran',bird='Kranich',unknown='Unbestimmt')
    assert not check(source,target,glossary=path)[0]
    wrong=document('de',lifting='Kranich',bird='Kranich',unknown='Unbestimmt')
    assert [(r.key,r.issue_type) for r in check(source,wrong,glossary=path)[0]]==[('lifting','glossary_violation')]


def test_detection_and_blind_verification_receive_full_context_and_scopes(tmp_path):
    source,target=inputs();source=document('en',**source.strings,**{'@entry':{'description':'Workshop dialog'}})
    terms=[{'en':'open','de':'oeffnen','scope':'Workshop dialog','notes':'A physical latch','de_variants':'aufmachen'},
           {'en':'open','de':'eroeffnen','scope':'Business dialog','notes':'An enterprise','de_variants':''}]
    provider=ModelFixture();context={'mechanic':'The highlighted latch opens away from the player.'}
    result=analyze(source,target,provider,glossary=terms,game_context=context,cache_dir=tmp_path)
    assert result['coverage']['status']=='complete'
    for request,_ in provider.requests:
        assert request['approved_glossary']==terms and request['game_context']==context
        assert request['assigned'][0]['metadata']['description']=='Workshop dialog'
    assert result['dependencies']['glossary']==terms and result['dependencies']['game_context']==context


def test_scoped_glossary_replay_preserves_rows_and_rejects_scope_changes(tmp_path):
    import pytest
    from dalel.delivery.replay import package,load,ReplayMismatch
    source,target=inputs();path=tmp_path/'terms.csv'
    path.write_bytes(b'en,de,scope,notes,de_variants\nHelp,Hilfe,Workshop,Physical latch,Beistand\nHelp,Assistenz,Office,Service,\n')
    entries=rows(path)
    result=analyze(source,target,ModelFixture(),glossary=entries,
        game_context={'location':'Workshop'},cache_dir=tmp_path/'cache')
    manifest=package(source,target,result,tmp_path/'bundle',glossary=path)
    restored=load(manifest,source,target,glossary=path)['language']
    assert restored['dependencies']['glossary']==entries
    assert restored['dependencies']['game_context']=={'location':'Workshop'} and restored['api_attempts']==0
    path.write_bytes(path.read_bytes().replace(b'Workshop',b'Warehouse'))
    with pytest.raises(ReplayMismatch,match='glossary differs'):
        load(manifest,source,target,glossary=path)


def test_cli_protocol_uses_actual_decisions_and_exports_verified_changes(tmp_path,monkeypatch):
    source,target=inputs();a=tmp_path/'source.arb';b=tmp_path/'target.arb'
    a.write_bytes(source.raw);b.write_bytes(target.raw)
    provider=ModelFixture()
    monkeypatch.setattr('dalel.inference.client.Config.from_env',lambda:SimpleNamespace(base_url='fixture',model='fixture',api_key='fixture',budget=1))
    monkeypatch.setattr('dalel.inference.client.Provider',lambda *a,**k:provider)
    out=tmp_path/'run';protocol=out/'protocol.json';fixed=out/'fixed.arb'
    result=CliRunner().invoke(app,['check','--source',str(a),'--target',str(b),'--out',str(out/'report.json'),
        '--profile','full','--protocol-out',str(protocol),'--corrected',str(fixed),'--student-cache',str(out/'cache'),'--use-escaping'])
    assert result.exit_code==0,result.output
    payload=json.loads(protocol.read_text(encoding='utf8'))
    assert set(payload['processed_keys'])=={'entry','help'} and not payload['abstentions']
    assert len(payload['findings'][0])==9
    assert json.loads(fixed.read_text(encoding='utf8'))['entry']=='Öffne {item}'
    assert not json.loads(fixed.with_suffix('.changes.json').read_text(encoding='utf8'))[0]['reviewed_by_human']
    provider.fail=True
    partial=out/'partial'
    result=CliRunner().invoke(app,['check','--source',str(a),'--target',str(b),'--out',str(partial/'report.json'),
        '--profile','full','--protocol-out',str(partial/'protocol.json'),'--student-cache',str(partial/'cache')])
    assert result.exit_code==3
    assert json.loads((partial/'protocol.json').read_text(encoding='utf8'))['processed_keys']==[]


def test_uzbek_never_reports_kazakh_harmony(tmp_path):
    source=document('en',route='Go to {place}')
    target=document('uz',route='{place}ga boring')
    findings,_=check(source,target)
    assert not any(r.issue_type=='suffix_harmony' for r in findings)
    assert any(r.issue_type=='orthography' for r in findings)


def test_formal_processing_trace_excludes_metadata_and_records_empty_messages():
    source=document('en',empty='',present='Present',**{'@present':{'description':'A label'}})
    target=document('de',empty='',present='Vorhanden',extra='Zusatz')
    processed=[];check(source,target,processed_keys=processed)
    assert set(processed)=={'empty','present','extra'} and len(processed)==3


def test_changed_canon_invalidates_old_lexical_divergence_claim():
    from dalel.engine.context import CorpusPolicy
    terms=[{'ru':'Судно','kk':'кеме','_rule':{'canon_verification':{'verdict':'yes'},'match':'term'}}]
    policy=CorpusPolicy([],canon_terms=terms)
    issue={'key':'demo','source':'Судно','target':'Кемелер','suggestion':None,'issue_type':'inconsistent_term','evidence':''}
    assert policy.route(issue,semantic=True)['type']=='current_canon_already_matched'
    assert policy.route(dict(issue,target='Қайық'),semantic=True) is None
    unrelated=dict(issue,source='Судно и причал',target='Кеме және аялдама',evidence='Для причала использовано «аялдама» вместо корпуса.')
    assert policy.route(unrelated,semantic=True) is None
    related=dict(unrelated,evidence='Термин «Судно» передан как «Кеме», а старый канон был другим.')
    assert policy.route(related,semantic=True)['type']=='current_canon_already_matched'


def test_uzbek_glossary_case_possession_and_marks_preserve_lexical_identity():
    from dalel.terminology.glossary import Matcher
    matcher=Matcher()
    for actual,canonical in [('kitoblarimizdan','kitob'),('tilagimni','tilak'),('oyog‘ingiz','oyoq'),
                             ('ko‘chamizga',"ko'cha"),('xatingizni','xat')]:
        assert matcher.contains(actual,canonical,'uz')
    for actual,canonical in [('kitobxon','kitob'),('tilaksiz','tilak'),('xatolik','xat'),('kitobxona','kitob')]:
        assert not matcher.contains(actual,canonical,'uz')
