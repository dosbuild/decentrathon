import pytest
from dalel.engine.labels import inspect_label
from dalel.engine.rules import check
from dalel.engine.merge import deduplicate
from dalel.languages.scripts import mixed_alphabet
from dalel.formats.messages import parse
from test_production import document


@pytest.mark.parametrize('suffix,kind', [(' үшін басыңыз', 'length_overflow'),
    (' бөліміне өту үшін басыңыз', 'machine_translation_suspect')])
def test_padding_distinguishes_an_instruction_from_added_meaning(suffix, kind):
    target = 'Басып шығару' + suffix
    result = inspect_label('Печать', target, 'ru', 'kk')
    assert result['issue_type'] == kind and result['suggestion'] == 'Басып шығару'
    assert inspect_label('Нажмите для печати', target, 'ru', 'kk') is None
    assert inspect_label('Печать', 'Қате мағыналы ұзақ сөйлем', 'ru', 'kk') is None


def test_shared_length_and_meaning_evidence_produces_one_primary_finding():
    findings, _ = check(document('ru', arbitrary='Печать'), document('kk', arbitrary='Басып шығару үшін басыңыз'))
    length = findings[0].model_dump()
    meaning = dict(length, issue_type='machine_translation_suspect', severity='high')
    merged, _ = deduplicate([length, meaning])
    assert len(merged) == 1 and merged[0]['issue_type'] == 'length_overflow'
    meaning.update(suggestion='Басқа әрекет', evidence='В «Басып шығару үшін басыңыз» искажён смысл.')
    merged, _ = deduplicate([length, meaning])
    assert {r['issue_type'] for r in merged} == {'length_overflow', 'machine_translation_suspect'}


def test_mixed_script_proposals_preserve_tags_icu_and_protected_tokens():
    value = '<b>Mashinaга</b> {n, plural, other{Arenaдан}} https://example.invalid/Arenaдан'
    result = mixed_alphabet('Машина', value, parse(value), 'uz')
    assert result['suggestion'] == '<b>Mashinaga</b> {n, plural, other{Arenadan}} https://example.invalid/Arenaдан'
    assert mixed_alphabet('Arenaдан', 'Arenaдан', parse('Arenaдан'), 'uz') is None
    assert mixed_alphabet('Name', 'Arenaдан', parse('Arenaдан'), 'uz_Cyrl') is None


def test_canon_proposal_alone_cannot_authorize_a_synonym_replacement():
    from dalel.engine.corrections import apply_ai_changes
    source, target = document('en', entry='Ready'), document('de', entry='Bereit')
    row = dict(key='entry', source='Ready', target='Bereit', suggestion='Fertig',
               issue_type='inconsistent_term', evidence='В «Bereit» предложено другое название.')
    raw, changes = apply_ai_changes(source, target, [row], provenance={})
    assert raw == target.raw and changes == []
    raw, changes = apply_ai_changes(source, target, [row], provenance={}, accepted_keys={'entry'})
    assert len(changes) == 1 and ArbValue(raw, 'entry') == 'Fertig'


def ArbValue(raw, key):
    from dalel.formats.arb import ArbDocument
    return ArbDocument(raw).strings[key]
