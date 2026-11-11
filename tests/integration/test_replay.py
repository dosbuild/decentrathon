import json
import shutil
import pytest
from typer.testing import CliRunner
from dalel.formats.arb import ArbDocument
from dalel.cli import app
from dalel.engine.pipeline import analyze,canonical,digest
from dalel.inference.client import Config
from dalel.delivery.replay import package,package_report,load,discover,ReplayMismatch,sha
from test_production import inputs,document,ModelFixture

def fixture(tmp_path):
    source,target=inputs();reference=document('fr',entry='Ouvrir {item}',help='Aide')
    result=analyze(source,target,ModelFixture(),references=[reference],cache_dir=tmp_path/'cache')
    manifest=package(source,target,result,tmp_path/'bundle',references=[reference])
    return source,target,result,manifest

def test_portable_replay_in_clean_directory_without_provider_or_original_cache(tmp_path,monkeypatch):
    source,target,result,manifest=fixture(tmp_path)
    moved=tmp_path/'clean';shutil.copytree(manifest.parent,moved)
    cache=(tmp_path/'cache').resolve()
    assert cache.is_relative_to(tmp_path.resolve())
    shutil.rmtree(cache)
    monkeypatch.chdir(moved)
    def forbidden():
        raise AssertionError('Replay must not read provider configuration')
    monkeypatch.setattr(Config,'from_env',forbidden)
    response=CliRunner().invoke(app,['check','--source','inputs/source.arb','--target','inputs/target.arb',
        '--profile','full','--out','report.json'])
    assert response.exit_code==0,response.output
    coverage=json.loads(response.stdout)
    assert coverage['mode']=='replay' and coverage['api_calls']==0 and coverage['language_complete']
    assert json.loads((moved/'report.json').read_text(encoding='utf8'))==result['report']
    assert source.raw==(moved/'inputs/source.arb').read_bytes()
    assert target.raw==(moved/'inputs/target.arb').read_bytes()

def test_source_reference_and_escaping_invalidate_without_inheriting_findings(tmp_path):
    source,target,result,manifest=fixture(tmp_path)
    changed=document('en',entry='Close {item}',help='Help')
    with pytest.raises(ReplayMismatch):
        load(manifest,changed,target)
    assert discover(changed,target,cwd=manifest.parent) is None
    with pytest.raises(ReplayMismatch):
        discover(changed,target,explicit=manifest)
    with pytest.raises(ReplayMismatch):
        load(manifest,source,target,references=[document('fr',entry='Fermer {item}',help='Aide')])
    with pytest.raises(ReplayMismatch):
        load(manifest,source,target,escaping=True)

@pytest.mark.parametrize('escape',['../secret.json','..\\secret.json','C:\\private\\secret.json','/tmp/secret.json','cache/file:stream'])
def test_manifest_paths_are_confined_on_both_operating_systems(tmp_path,escape):
    source,target,result,manifest=fixture(tmp_path)
    value=json.loads(manifest.read_text(encoding='utf8'));value['student']['path']=escape
    manifest.write_bytes(canonical(value).encode())
    with pytest.raises(ValueError,match='path'):
        load(manifest,source,target)

def test_hashes_glossary_and_input_values_are_independently_validated(tmp_path):
    source,target,result,manifest=fixture(tmp_path)
    value=json.loads(manifest.read_text(encoding='utf8'));artifact=manifest.parent/value['student']['path']
    original=artifact.read_bytes();artifact.write_bytes(original+b' ')
    with pytest.raises(ValueError,match='hash'):
        load(manifest,source,target)
    payload=json.loads(original);payload['report'][0]['source']='Changed {item}'
    payload.pop('payload_sha256');payload['payload_sha256']=digest(payload)
    artifact.write_bytes(canonical(payload).encode());value['student']['sha256']=sha(artifact.read_bytes())
    manifest.write_bytes(canonical(value).encode())
    with pytest.raises(ValueError,match='input'):
        load(manifest,source,target)
    glossary=tmp_path/'terms.csv';glossary.write_bytes(b'en,de\nHelp,Hilfe\n')
    with pytest.raises(ReplayMismatch,match='different inputs'):
        package(source,target,result,tmp_path/'bad-glossary',glossary=glossary)

def test_partial_results_and_input_overwrites_are_rejected(tmp_path):
    source,target=inputs();partial=analyze(source,target,ModelFixture(fail=True),cache_dir=tmp_path/'cache')
    with pytest.raises(ValueError,match='complete'):
        package(source,target,partial,tmp_path/'partial')
    source,target,result,manifest=fixture(tmp_path)
    with pytest.raises(ValueError,match='overwrite'):
        package(ArbDocument.read(manifest.parent/'inputs/source.arb'),
            ArbDocument.read(manifest.parent/'inputs/target.arb'),result,manifest.parent,
            references=[ArbDocument.read(manifest.parent/'inputs/reference-0.arb')])

def test_curated_report_default_offline_replay_is_not_student_prediction(tmp_path,monkeypatch):
    source,target,result,_=fixture(tmp_path)
    root=tmp_path/'curated'
    manifest=package_report(source,target,result['report'],root,
        coverage={'status':'complete','source_strings_evaluated':len(source.strings)},
        provenance={'producer':'teacher','assessment':'synthetic contract fixture'})
    monkeypatch.chdir(root)
    def forbidden():raise AssertionError('Offline replay must never read credentials')
    monkeypatch.setattr(Config,'from_env',forbidden)
    response=CliRunner().invoke(app,['check','--source','inputs/source.arb','--target','inputs/target.arb','--out','report.json'])
    assert response.exit_code==0,response.output
    coverage=json.loads(response.stdout)
    assert coverage['producer']=='teacher' and coverage['replay_origin']=='curated_language_report'
    assert coverage['api_calls']==0 and coverage['layer_counts']['merged']==1
    assert json.loads((root/'report.json').read_text(encoding='utf8'))==result['report']
    page=(root/'report.html').read_text(encoding='utf8')
    assert 'Языковая модель (сохранённые результаты)' in page and 'Покрытие по типам' in page
    assert 'Teacher' not in page
    assert 'не запускался' not in page
    changed=document('en',entry='Close {item}',help='Help')
    with pytest.raises(ReplayMismatch):load(manifest,changed,target)

def test_offline_default_never_reads_credentials_for_unseen_pair(tmp_path,monkeypatch):
    source,target=inputs();(tmp_path/'source.arb').write_bytes(source.raw);(tmp_path/'target.arb').write_bytes(target.raw)
    monkeypatch.chdir(tmp_path)
    def forbidden():raise AssertionError('Offline default must not admit paid calls')
    monkeypatch.setattr(Config,'from_env',forbidden)
    response=CliRunner().invoke(app,['check','--source','source.arb','--target','target.arb','--out','report.json'])
    assert response.exit_code==0,response.output
    assert json.loads(response.stdout)['api_calls']==0
