from dalel.formats.messages import validate_suggestion,target_layout_problems

def test_layout_preserves_original_target_edges_even_when_source_differs():
    source='Next {person}';target=' Weiter {person}'
    assert not validate_suggestion(source,' Danach {person}',original_target=target)
    assert 'original target left edge whitespace differs' in validate_suggestion(source,'Danach {person}',original_target=target)
    assert target_layout_problems(' A\nB ',' A\nB ')==[]
    assert target_layout_problems('A ','A')==['original target right edge whitespace differs']

def test_line_count_needs_explicit_defect_exception_but_tokens_never_do():
    source='Short title';target='Kurzer\nTitel'
    assert not validate_suggestion(source,'Kurz',original_target=target)
    source='Short\ntitle'
    assert 'source newline count differs' in validate_suggestion(source,'Kurz',original_target=target)
    assert not validate_suggestion(source,'Kurz',original_target=target,allow_line_reflow=True)
    assert validate_suggestion('{person}','Kurz',original_target=target,allow_line_reflow=True)
    assert not target_layout_problems(None,'New\nmessage')
