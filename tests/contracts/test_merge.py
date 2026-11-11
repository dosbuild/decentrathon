from dalel.engine.merge import deduplicate
from dalel.delivery.replay import package_report,load
from test_production import document


def row(kind,**changes):
    return dict(key='title',source='Пример',target='Китап',issue_type=kind,severity='medium',
        suggestion='Кітап',evidence='В написании «Китап» неверная буква.',confidence=.85,reviewed_by_human=False)|changes


def test_same_replacement_has_one_primary_but_independent_defects_survive():
    for evidence in ('Значение полностью неверно: «Китап» обозначает иной объект.',
                     'Смысл полностью не связан с исходным: «Китап».'):
        meaning=row('machine_translation_suspect',evidence=evidence)
        term=row('glossary_violation',confidence=.92)
        assert deduplicate([meaning,term],curated=[meaning])[0][0]['issue_type']=='machine_translation_suspect'
    a=row('machine_translation_suspect');b=row('orthography',confidence=.88)
    report,diagnostics=deduplicate([a,b])
    assert len(report)==1 and report[0]['issue_type']=='orthography' and report[0]['confidence']==.88
    assert len(diagnostics)==1 and len(diagnostics[0]['findings'])==2
    structural=row('placeholder_mismatch',source='Пример {item}',target='Китап {lost}',suggestion=None)
    assert len(deduplicate([a,structural])[0])==2
    foreign=row('wrong_language',suggestion=None);copied=row('same_as_source',suggestion=None)
    assert deduplicate([foreign,copied])[0][0]['issue_type']=='wrong_language'
    foreign.update(target=foreign['source'],evidence='Текст «Пример» не переведён.')
    copied.update(target=copied['source'],evidence='Текст «Пример» не переведён.')
    assert deduplicate([foreign,copied])[0][0]['issue_type']=='same_as_source'


def test_formal_confidence_survives_curated_merge_and_withheld_fix_stays_withheld(tmp_path):
    source=document('ru',title='Пример');target=document('kk')
    missing=row('untranslated',target=None,suggestion=None,severity='high',evidence='Перевод «Пример» отсутствует.')
    manifest=package_report(source,target,[missing],tmp_path/'out',coverage={'status':'complete','source_strings_evaluated':1},provenance={'producer':'external'})
    result=load(manifest,source,target)['language']['report']
    assert result[0]['confidence']==.99 and result[0]['suggestion'] is None
    meaning=row('machine_translation_suspect',source='Book',target='Heft',suggestion=None,
        evidence='В «Heft» использован другой термин для «Book».')
    term=row('glossary_violation',source='Book',target='Heft',suggestion='Buch',confidence=.92,
        evidence='Термин «Book» в «Heft» отличается от глоссария.')
    result,_=deduplicate([meaning,term],curated=[meaning])
    assert len(result)==1 and result[0]['issue_type']=='glossary_violation' and result[0]['suggestion'] is None
