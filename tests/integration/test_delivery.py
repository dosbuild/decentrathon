import hashlib
import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from dalel.cli import app
from dalel.delivery.package import build,public_export
from test_production import document


def save(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False)+'\n').encode('utf8'))


def fixture(tmp_path):
    source=document('ru',read='Книга');target=document('kk',read='Китап')
    (tmp_path/'source.arb').write_bytes(source.raw);(tmp_path/'target.arb').write_bytes(target.raw)
    row={'key':'read','source':'Книга','target':'Китап','issue_type':'orthography','severity':'medium',
        'evidence':'Казахское слово «Китап» требует буквы і: правильно «Кітап».','suggestion':'Кітап',
        'confidence':.9,'reviewed_by_human':False}
    save(tmp_path/'report.json',[row])
    (tmp_path/'glossary.csv').write_bytes(b'ru,kk,en,comment\n')
    save(tmp_path/'glossary_rules.json',{'format':1,'source_locale':'ru','target_locale':'kk','terms':[]})
    save(tmp_path/'coverage.json',{'status':'complete','source_strings_evaluated':1})
    sha=lambda data:hashlib.sha256(data).hexdigest()
    save(tmp_path/'verification.json',{'source_sha256':sha(source.raw),'target_sha256':sha(target.raw),
        'fixes':[{'key':'read','text':'Кітап','verdict':'strict_win'}]})
    manifest={'format':1,**{k:k+('.arb' if k in ('source','target') else '.csv' if k=='glossary' else '.json')
        for k in ('source','target','report','glossary','coverage','verification')}}
    save(tmp_path/'delivery.json',manifest)
    return manifest


def test_package_reproduces_offline_report_and_blocks_unqualified_review(tmp_path,monkeypatch):
    config=fixture(tmp_path);out=tmp_path/'submission'
    status=build(tmp_path/'delivery.json',out,verify_flutter=False)
    assert 'Детерминированные правила' in (out/'report.html').read_text(encoding='utf8')
    assert status['changed_strings']==1 and status['human_acceptances']==0 and status['review_cards']==0
    assert 'full_fixed_file_self_check' in status['unmet_conditions']
    original=(out/'intl_kk_fixed.arb').read_bytes()
    assert build(tmp_path/'delivery.json',out,verify_flutter=False)['fixed_sha256']==status['fixed_sha256']
    assert (out/'intl_kk_fixed.arb').read_bytes()==original
    monkeypatch.chdir(out)
    result=CliRunner().invoke(app,['check','--source','intl_ru.arb','--target','intl_kk.arb','--glossary','glossary.csv','--out','report.json'])
    assert result.exit_code==0,result.output
    assert '"api_calls": 0' in result.output
    assert json.loads((out/'report.json').read_text(encoding='utf8'))[0]['suggestion']=='Кітап'
    assert 'Human-accepted changed strings: 0' in (out/'methodology.md').read_text()
    config['self_check']='self_check.json';save(tmp_path/'self_check.json',{'status':'passed','fixed_sha256':status['fixed_sha256'],
        'findings_detail':[],'coverage':{'source_strings_evaluated':1},
        'language_complete':True,'missing_keys':0,'placeholder_findings':0,'wrong_language_findings':0,
        'same_as_source_findings':0,'remaining_high_without_code_change':0})
    save(tmp_path/'delivery.json',config)
    checked_status=build(tmp_path/'delivery.json',out,verify_flutter=False)
    assert checked_status['review_cards']==1
    assert checked_status['fixed_file_check']['new_findings']==0
    assert checked_status['fixed_file_check']['remaining_high_without_code_change']==0
    checked=json.loads((tmp_path/'self_check.json').read_text())
    checked.update(status='requires_remediation',remaining_high_without_code_change=1)
    save(tmp_path/'self_check.json',checked)
    assert build(tmp_path/'delivery.json',out,verify_flutter=False)['review_cards']==1


def test_package_rejects_unverified_fixes_stale_input_and_overwrite_of_human_history(tmp_path):
    fixture(tmp_path);path=tmp_path/'verification.json';verification=json.loads(path.read_text(encoding='utf8'))
    verification['fixes']=[];save(path,verification)
    with pytest.raises(ValueError,match='strict verification'):build(tmp_path/'delivery.json',tmp_path/'out',verify_flutter=False)
    fixture(tmp_path);out=tmp_path/'out';build(tmp_path/'delivery.json',out,verify_flutter=False)
    (out/'review_history').mkdir()
    with pytest.raises(ValueError,match='human-reviewed'):build(tmp_path/'delivery.json',out,verify_flutter=False)


def test_raw_candidates_remain_distinct_from_the_published_fixes(tmp_path):
    config=fixture(tmp_path)
    raw=json.loads((tmp_path/'report.json').read_text(encoding='utf8'))
    raw[0]['suggestion']=None
    raw[0]['evidence']='Казахское «Китап» требует исправления буквы по правилу.'
    save(tmp_path/'automatic.json',raw);config['report_auto']='automatic.json';save(tmp_path/'delivery.json',config)
    out=tmp_path/'out';build(tmp_path/'delivery.json',out,verify_flutter=False)
    assert json.loads((out/'report_auto.json').read_text(encoding='utf8'))==raw
    assert json.loads((out/'report.json').read_text(encoding='utf8'))[0]['suggestion']=='Кітап'
    assert json.loads((out/'changes.json').read_text(encoding='utf8'))[0]['after']=='Кітап'
    raw[0]['source']='Different input';save(tmp_path/'automatic.json',raw)
    with pytest.raises(ValueError,match='snapshot'):build(tmp_path/'delivery.json',out,verify_flutter=False)


def test_public_export_uses_allowlist_not_repository_history_or_private_artifacts(tmp_path):
    project=tmp_path/'project';project.mkdir()
    for name in ('README.md','LICENSE','pyproject.toml'): (project/name).write_text('public',encoding='utf8')
    screenshots = [f'docs/images/readme/{name}.jpg' for name in ('workspace', 'validation', 'glossary', 'review', 'release')]
    for name in ('docs/guide.md', *screenshots, 'src/dalel/app.py','src/dalel/assets/workspace/src/demo.js','src/dalel/assets/workspace/demo/input.arb','data/secret.arb','judge/eval/gold.json','codex/report.md','.env','tools/probe.py'):
        path=project/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('content',encoding='utf8')
    out=tmp_path/'public';result=public_export(project,out)
    assert result['files']==10 and (out/'src/dalel/app.py').exists()
    assert not any((out/p).exists() for p in ('judge','data','codex','.env','tools'))
    assert not (out/'src/dalel/assets/workspace/src/demo.js').exists()
    assert not (out/'src/dalel/assets/workspace/demo').exists()
    assert public_export(project,out)['files']==10
    assert (out/'docs/guide.md').is_file()
    assert all((out/name).is_file() for name in screenshots)
    (out/'README.md').write_bytes(b'consumer edit')
    with pytest.raises(ValueError,match='modified'):public_export(project,out)


@pytest.mark.parametrize('include_uz',[False,True])
def test_flutter_evidence_reuse_rejects_a_changed_fixed_byte(tmp_path,include_uz):
    from dalel.delivery.package import cached_flutter,capture_flutter_evidence,restore_flutter_evidence
    from dalel.delivery.flutter import key_mapping,normalize_document,VERSION,REVISION
    source=document('ru',hello='Привет');fixed=document('kk',hello='Сәлем')
    for name,doc in [('intl_ru.arb',source),('intl_kk_fixed.arb',fixed)]: (tmp_path/name).write_bytes(doc.raw)
    bundle=tmp_path/'flutter_bundle';bundle.mkdir()
    (bundle/'generation.log').write_bytes(b'fixture generation evidence')
    (bundle/'messages.dart').write_bytes(b'// synthetic proof fixture')
    mapping,actions=key_mapping([source,fixed])
    ru=normalize_document(source,mapping,actions,source);kk=normalize_document(fixed,mapping,actions,source)
    inputs={'A_source':{'intl_ru.arb':source.raw},'A':{'intl_ru.arb':source.raw,'intl_kk.arb':fixed.raw},
        'B1':{'intl_ru.arb':ru},'B2':{'intl_ru.arb':ru,'intl_kk.arb':kk}}
    if include_uz:
        uz=document('uz',hello='Salom');(tmp_path/'uz').mkdir()
        (tmp_path/'uz/intl_uz_fixed.arb').write_bytes(uz.raw)
        inputs['A']['intl_uz.arb']=uz.raw
        inputs['B3']=dict(inputs['B2'],**{'intl_uz.arb':normalize_document(uz,mapping,actions,source)})
    checks={k:{'generation':'passed','input_sha256':{n:hashlib.sha256(v).hexdigest() for n,v in values.items()},
        'log':str(bundle/'generation.log'),'generated_file':str(bundle/'messages.dart')} for k,values in inputs.items()}
    save(bundle/'validation.json',{'status':'passed','sdk':{'version':VERSION,'revision':REVISION},'checks':checks,
        'api_signatures':'passed','check_a_api_signatures':'passed','runtime_smoke':'passed','oracle':{'status':'passed'}})
    assert cached_flutter(tmp_path)['status']=='passed'
    evidence=tmp_path.parent/(tmp_path.name+'-evidence')
    capture_flutter_evidence(tmp_path,evidence)
    assert capture_flutter_evidence(tmp_path,evidence)['files']==3
    (evidence/'unowned.txt').write_bytes(b'keep me')
    with pytest.raises(ValueError,match='modified'):capture_flutter_evidence(tmp_path,evidence)
    (evidence/'unowned.txt').unlink()
    copied=tmp_path/'relocated';copied.mkdir()
    for name in ('intl_ru.arb','intl_kk_fixed.arb'):(copied/name).write_bytes((tmp_path/name).read_bytes())
    if include_uz:
        (copied/'uz').mkdir();(copied/'uz/intl_uz_fixed.arb').write_bytes(uz.raw)
    assert restore_flutter_evidence(evidence,copied)['status']=='passed'
    assert cached_flutter(copied)['checks']['A']['log']=='flutter_bundle/generation.log'
    if include_uz:
        (copied/'uz/intl_uz_fixed.arb').write_bytes(uz.raw+b' ')
        assert cached_flutter(copied) is None
        (copied/'uz/intl_uz_fixed.arb').write_bytes(uz.raw)
    proof_file=evidence/'flutter_bundle/generation.log'
    proof_file.write_bytes(proof_file.read_bytes()+b' changed')
    with pytest.raises(ValueError,match='hash differs'):restore_flutter_evidence(evidence,copied)
    proof_file.write_bytes(b'fixture generation evidence')
    (copied/'intl_kk_fixed.arb').write_bytes(fixed.raw+b' ')
    with pytest.raises(ValueError,match='stale'):restore_flutter_evidence(evidence,copied)
    (tmp_path/'intl_kk_fixed.arb').write_bytes(fixed.raw+b' ')
    assert cached_flutter(tmp_path) is None


def test_inherited_concat_spacing_does_not_block_a_fix_preserving_both_target_edges():
    from dalel.delivery.package import correction_exclusions
    from dalel.engine.rules import check
    source=document('ru',welcomePrefix='Привет ',welcomeSuffix='!')
    original=document('kk',welcomePrefix='Сәлем',welcomeSuffix='!')
    fixed=document('kk',welcomePrefix='Қош келдің',welcomeSuffix='!')
    changes=[{'key':'welcomePrefix','before':'Сәлем','after':'Қош келдің'}]
    assert any(d['type']=='concat_whitespace' for d in check(source,fixed)[1])
    assert correction_exclusions(source,original,fixed,changes,None)==[]
    changed_edges=document('kk',welcomePrefix='Қош келдің ',welcomeSuffix='!')
    changes[0]['after']='Қош келдің '
    assert correction_exclusions(source,original,changed_edges,changes,None)


def tree_bytes(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_package_twice_is_byte_identical_and_failed_build_keeps_prior_tree(tmp_path,monkeypatch):
    import dalel.delivery.package as delivery
    fixture(tmp_path);out=tmp_path/'out'
    build(tmp_path/'delivery.json',out,verify_flutter=False)
    before=tree_bytes(out)
    build(tmp_path/'delivery.json',out,verify_flutter=False)
    assert tree_bytes(out)==before
    def fail(*args,**kwargs):raise ValueError('injected late review failure')
    monkeypatch.setattr(delivery,'write_screen',fail)
    with pytest.raises(ValueError,match='injected'):
        build(tmp_path/'delivery.json',out,verify_flutter=False)
    assert tree_bytes(out)==before
    fresh=tmp_path/'fresh'
    with pytest.raises(ValueError,match='injected'):
        build(tmp_path/'delivery.json',fresh,verify_flutter=False)
    assert not fresh.exists()
    assert not list(tmp_path.glob('.*-locqa-build-*'))


def test_failed_public_swap_restores_both_previous_outputs(tmp_path,monkeypatch):
    import dalel.delivery.package as delivery
    fixture(tmp_path);out=tmp_path/'out';public=tmp_path/'public';version={'value':'first'}
    def export(project,destination):
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'readme.txt').write_bytes(version['value'].encode())
        return {'files':1}
    monkeypatch.setattr(delivery,'public_export',export)
    build(tmp_path/'delivery.json',out,public_out=public,verify_flutter=False)
    before=tree_bytes(out);public_before=tree_bytes(public);version['value']='second'
    rename=delivery._replace_directory;failed={'once':False}
    def fail_publish(source,destination):
        if source.name=='next' and destination==public and not failed['once']:
            failed['once']=True;raise OSError('injected public rename failure')
        rename(source,destination)
    monkeypatch.setattr(delivery,'_replace_directory',fail_publish)
    with pytest.raises(OSError,match='injected'):
        build(tmp_path/'delivery.json',out,public_out=public,verify_flutter=False)
    assert tree_bytes(out)==before and tree_bytes(public)==public_before
    assert not list(tmp_path.glob('.*-locqa-build-*'))


def test_formal_fix_follows_source_caps_without_changing_argument_case():
    from dalel.engine.rules import check
    source=document('en',greeting='HELLO {User}')
    target=document('de',greeting='Hallo {Usr}')
    findings,_=check(source,target)
    assert findings[0].suggestion=='HALLO {User}'


def test_complete_check_cannot_ship_an_excluded_correction(tmp_path):
    config=fixture(tmp_path);out=tmp_path/'out'
    status=build(tmp_path/'delivery.json',out,verify_flutter=False)
    before=tree_bytes(out)
    config['self_check']='self_check.json'
    row=json.loads((tmp_path/'report.json').read_text(encoding='utf8'))[0]
    row.update(target='Кітап',suggestion=None,evidence='Значение «Кітап» требует проверки контекста.')
    save(tmp_path/'self_check.json',{'status':'requires_remediation','fixed_sha256':status['fixed_sha256'],
        'language_complete':True,'coverage':{'source_strings_evaluated':1},'findings_detail':[row]})
    save(tmp_path/'delivery.json',config)
    with pytest.raises(ValueError,match='Excluded corrections'):
        build(tmp_path/'delivery.json',out,verify_flutter=False)
    assert tree_bytes(out)==before


def test_review_refresh_updates_latin_and_counts_and_invalidates_old_proof(tmp_path):
    from dalel.delivery.package import refresh_after_review
    from dalel.languages.latin import convert_document
    from dalel.formats.arb import ArbDocument
    from dalel.delivery.replay import load, ReplayMismatch
    fixture(tmp_path);out=tmp_path/'out'
    build(tmp_path/'delivery.json',out,verify_flutter=False)
    fixed=document('kk',read='Жаңа кітап')
    (out/'intl_kk_fixed.arb').write_bytes(fixed.raw)
    save(out/'flutter_bundle/validation.json',{'status':'passed','old_snapshot':True})
    refresh_after_review(out,{'changed_strings':1,'human_accepted_strings':1,'human_approved_terms':0})
    assert (out/'intl_kk_Latn.arb').read_bytes()==convert_document(fixed)[0]
    assert json.loads((out/'flutter_bundle/validation.json').read_bytes())['status']=='not_run'
    assert len(list((out/'flutter_bundle/history').glob('*.json')))==1
    status=json.loads((out/'delivery_status.json').read_bytes())
    assert status['fixed_sha256']==hashlib.sha256(fixed.raw).hexdigest()
    assert status['status']=='incomplete' and status['human_acceptances']==1
    with pytest.raises(ReplayMismatch,match='Review decisions'):
        load(out/'locqa-replay.json',ArbDocument.read(out/'intl_ru.arb'),ArbDocument.read(out/'intl_kk.arb'))
