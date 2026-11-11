from dalel.engine.suggestions import placeholder_name_repair


def test_name_repair_preserves_text_format_and_all_occurrences():
    assert placeholder_name_repair('Hello {visitor}','Hallo { visito } / {visito}')=='Hallo { visitor } / {visitor}'
    assert placeholder_name_repair('<b>{distance}</b>\ue004','<b>{distanc}</b>\ue004')=='<b>{distance}</b>\ue004'


def test_no_guess_when_ambiguous_or_other_protected_data_is_broken():
    assert placeholder_name_repair('{person} {place}','{perso} {plac}') is None
    assert placeholder_name_repair('{quantity}','{total}') is None
    assert placeholder_name_repair('<b>{visitor}</b>','{visito}') is None
    assert placeholder_name_repair('{n, plural, other{A}}','{m, plural, other{B}}') is None
