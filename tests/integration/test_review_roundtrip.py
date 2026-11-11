"""Small software contracts; temporary actors are fixtures, never expert records."""
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dalel.cli import app
from dalel.delivery.replay import package_report
from dalel.formats.arb import ArbDocument
from dalel.review.screen import write_screen
from dalel.review.importer import import_decisions


def bundle(folder):
    source = ArbDocument(b'{"@@locale":"en","command":"Open {item}"}')
    target = ArbDocument(b'{"@@locale":"de","command":"Zu {item}"}')
    finding = dict(key='command', source='Open {item}', target='Zu {item}', suggestion='Auf {item}',
        issue_type='machine_translation_suspect', severity='high', confidence=.9,
        evidence='В «Zu {item}» противоположное действие.', reviewed_by_human=False)
    package_report(source, target, [finding], folder,
        coverage=dict(status='complete', source_strings_evaluated=1), provenance=dict(producer='external'))
    (folder/'intl_en.arb').write_bytes(source.raw)
    (folder/'intl_de.arb').write_bytes(target.raw)
    write_screen(source, target, [finding], folder)
    manifest = json.loads((folder/'review_manifest.json').read_text())
    card = manifest['cards'][0]
    packet = dict(format=1, mode='human', attest_human=True, review_id=manifest['review_id'],
        reviewer='Software contract fixture', entries=[dict(key='command', card_hash=card['card_hash'],
            action='accept', text='Öffne {item}', reviewer='Software contract fixture', at='2026-01-01T00:00:00Z')])
    decisions = folder.parent/'fixture-decisions.json'
    decisions.write_text(json.dumps(packet), encoding='utf8')
    return decisions


def test_import_is_transactional_replay_keeps_exact_review_and_new_output_is_self_contained(tmp_path, monkeypatch):
    original = tmp_path/'original'
    decisions = bundle(original)
    output = tmp_path/'accepted'
    assert import_decisions(original, decisions, output)['human_accepted_strings'] == 1
    assert not (original/'review_history').exists()
    monkeypatch.chdir(output)
    command = ['check', '--source', 'intl_en.arb', '--target', 'intl_de.arb', '--out', 'report.json']
    expected_json = (output/'report.json').read_bytes()
    expected_html = (output/'report.html').read_bytes()
    result = CliRunner().invoke(app, command)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['api_calls'] == 0
    assert (output/'report.json').read_bytes() == expected_json
    assert (output/'report.html').read_bytes() == expected_html
    assert json.loads(expected_json)[0]['reviewed_by_human'] is True
    strict = CliRunner().invoke(app, command + ['--strict-coverage'])
    assert strict.exit_code == 3  # A review receipt alone cannot prove a language pass.
    import_decisions(output, decisions)
    assert len(list((output/'review_history').glob('*.json'))) == 1
    # A later edit invalidates the binding and cannot overwrite the reviewed report.
    (output/'intl_de.arb').write_bytes(b'{"@@locale":"de","command":"Anders {item}"}')
    (output/'delivery_status.json').write_text('{}')
    failed = CliRunner().invoke(app, command)
    assert failed.exit_code == 2 and (output/'report.json').read_bytes() == expected_json


def test_unsafe_expert_edit_cannot_partially_replace_delivery(tmp_path):
    original = tmp_path/'original'
    decisions = bundle(original)
    before = {p.relative_to(original): p.read_bytes() for p in original.rglob('*') if p.is_file()}
    packet = json.loads(decisions.read_text())
    packet['entries'][0]['text'] = 'Missing argument'
    decisions.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match='Unsafe'):
        import_decisions(original, decisions)
    assert {p.relative_to(original): p.read_bytes() for p in original.rglob('*') if p.is_file()} == before


def test_analysis_import_validates_saved_response_dependencies(tmp_path):
    from dalel.delivery.analysis_import import import_analysis
    original = tmp_path/'original'
    bundle(original)
    destination = tmp_path/'imported'
    summary = import_analysis(original/'locqa-replay.json', destination)
    assert summary['exact_saved_findings'] and summary['api_calls'] == 0
    assert (destination/'review_manifest.json').exists()
    manifest = json.loads((original/'locqa-replay.json').read_text())
    payload = original/manifest['analysis']['path']
    payload.write_bytes(payload.read_bytes() + b' ')
    with pytest.raises(ValueError, match='hash'):
        import_analysis(original/'locqa-replay.json', tmp_path/'invalid')


@pytest.mark.parametrize('failure_name', ['b.txt', 'new.txt'])
def test_in_place_write_failure_restores_previous_files(tmp_path, monkeypatch, failure_name):
    from dalel.review.importer import replace_files
    output, stage = tmp_path/'output', tmp_path/'stage'
    output.mkdir(); stage.mkdir()
    for name in ('a.txt', 'b.txt'):
        (output/name).write_bytes(b'original')
        (stage/name).write_bytes(b'changed')
    (stage/'new.txt').write_bytes(b'new')
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    replace = Path.replace
    def fail(source, target):
        if source.parent == stage and source.name == failure_name:
            raise OSError('injected write failure')
        return replace(source, target)
    monkeypatch.setattr(Path, 'replace', fail)
    with pytest.raises(OSError, match='injected write failure'):
        replace_files(stage, output, tmp_path/'previous')
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
