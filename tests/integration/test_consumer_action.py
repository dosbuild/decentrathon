import importlib.util
import json
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('consumer_check',Path(__file__).parents[2]/'tools/consumer_check.py')
consumer=importlib.util.module_from_spec(spec);spec.loader.exec_module(consumer)


def put(root,locale,text):
    path=root/'lib/l10n'/('app_'+locale+'.arb');path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(json.dumps({'@@locale':locale,'welcome':text},ensure_ascii=False).encode('utf8'))


def test_consumer_own_arb_regression_blocks_but_existing_defect_does_not(tmp_path):
    base=tmp_path/'base';head=tmp_path/'head'
    for root in (base,head):put(root,'en','Hello {person}');put(root,'de','Hallo {person}')
    put(head,'de','Hallo')
    summary,markdown=consumer.run(head,base,tmp_path/'result')
    assert summary['checked_pairs']==1 and summary['blocking_pairs']==1 and summary['api_calls']==0
    assert 'welcome' in markdown and '«' in markdown and '| new | high |' in markdown
    put(base,'de','Hallo');put(head,'de','Hallo {person}')
    repaired,repair_markdown=consumer.run(head,base,tmp_path/'repaired')
    assert repaired['blocking_pairs']==0 and '| resolved | high | welcome |' in repair_markdown
    put(base,'de','Hallo');put(head,'de','Guten Tag')
    changed,_=consumer.run(head,base,tmp_path/'changed-high')
    assert changed['blocking_pairs']==1
    put(head,'de','Hallo')
    path=head/'lib/l10n/app_de.arb';value=json.loads(path.read_text(encoding='utf8'));value['@@comment']='Changed metadata only'
    path.write_bytes(json.dumps(value,ensure_ascii=False).encode('utf8'))
    summary,_=consumer.run(head,base,tmp_path/'existing')
    assert summary['blocking_pairs']==0


def test_nondefault_literal_flutter_paths_and_escape_rejection(tmp_path):
    root=tmp_path/'root';root.mkdir()
    (root/'l10n.yaml').write_bytes(b"arb-dir: 'assets/messages'\ntemplate-arb-file: catalog_fr.arb\n")
    directory,template=consumer.config_paths(root.resolve())
    assert template==directory/'catalog_fr.arb'
    with pytest.raises(ValueError):consumer.confined(root.resolve(),'../private.arb')
    with pytest.raises(ValueError):consumer.confined(root.resolve(),'C:/outside.arb')
