import json
from typer.testing import CliRunner
from dalel.cli import app
from dalel.inference.client import Config
from test_production import inputs,ModelFixture

def command(tmp_path):
    source,target=inputs();src=tmp_path/'en.arb';tgt=tmp_path/'de.arb'
    src.write_bytes(source.raw);tgt.write_bytes(target.raw)
    return ['check','--source',str(src),'--target',str(tgt),'--out',str(tmp_path/'report.json'),
            '--student-cache',str(tmp_path/'cache')]

def test_auto_without_credentials_is_explicit_and_full_is_incomplete(tmp_path,monkeypatch):
    monkeypatch.setattr(Config,'from_env',lambda:Config('','','',budget=0))
    args=command(tmp_path);runner=CliRunner()
    result=runner.invoke(app,args+['--profile','auto'])
    assert result.exit_code==0
    coverage=json.loads(result.stdout)
    assert coverage['mode']=='deterministic' and not coverage['language_complete'] and coverage['api_calls']==0
    assert runner.invoke(app,args+['--profile','full']).exit_code==3
    assert runner.invoke(app,args+['--strict-coverage']).exit_code==3

def test_full_and_cached_cli_reports_match_with_no_second_call(tmp_path,monkeypatch):
    provider=ModelFixture()
    monkeypatch.setattr(Config,'from_env',lambda:Config('https://synthetic.invalid/v1','synthetic-model','synthetic'))
    monkeypatch.setattr('dalel.inference.client.Provider',lambda _:provider)
    args=command(tmp_path)+['--profile','full'];runner=CliRunner()
    first=runner.invoke(app,args)
    assert first.exit_code==0,first.output
    assert json.loads(first.stdout)['language_complete']
    before=(tmp_path/'report.json').read_bytes();count=len(provider.requests)
    again=runner.invoke(app,args)
    assert again.exit_code==0,again.output
    assert json.loads(again.stdout)['mode']=='replay' and json.loads(again.stdout)['api_calls']==0
    assert len(provider.requests)==count and (tmp_path/'report.json').read_bytes()==before
    assert b'\r' not in before and (tmp_path/'report.html').exists()

def test_interrupted_full_keeps_formal_report_and_exits_partial(tmp_path,monkeypatch):
    provider=ModelFixture(fail=True)
    monkeypatch.setattr(Config,'from_env',lambda:Config('https://synthetic.invalid/v1','synthetic-model','synthetic'))
    monkeypatch.setattr('dalel.inference.client.Provider',lambda _:provider)
    result=CliRunner().invoke(app,command(tmp_path)+['--profile','auto'])
    assert result.exit_code==3,result.output
    assert json.loads(result.stdout)['mode']=='partial'
    assert (tmp_path/'report.json').exists() and (tmp_path/'language.json').exists()
