"""Shared coverage declarations for command output, reports and saved analyses."""


def describe(source, target, records, language, glossary, *, mode, profile,
             language_complete=False, language_error=None, strict=False):
    source_locale = source.locale.replace('-', '_').split('_')[0]
    target_locale = target.locale.replace('-', '_').split('_')[0]
    supported = {'wrong_language': True, 'same_as_source': True,
        'suffix_harmony': target_locale == 'kk', 'orthography': target_locale in ('kk', 'uz'),
        'glossary_violation': True, 'length_overflow': (source_locale, target_locale) == ('ru', 'kk')}
    screened = [kind for kind, available in supported.items() if available]
    linguistic = ['machine_translation_suspect', 'inconsistent_term'] + [kind for kind, available in supported.items() if not available]
    result = dict(mode=mode, api_calls=language['api_attempts'] if language else 0,
        strings=len(source.strings), source_locale=source.locale, target_locale=target.locale,
        human_acceptances=0, findings=len(records), seconds=0,
        formal=['untranslated', 'placeholder_mismatch', 'plural_mismatch'],
        partial=screened, requires_language_pass=linguistic)
    if glossary is None:
        screened.remove('glossary_violation')
        result['not_configured'] = ['glossary_violation']
    if target_locale == 'uz':
        linguistic.remove('suffix_harmony')
        result.update(not_applicable=['suffix_harmony'], calibration='uncalibrated', uz_reviewer=None,
            language_candidates=language['coverage'].get('language_candidates', 0) if language else 0)
    if profile != 'deterministic' or strict:
        result.update(language_complete=language_complete, language_error=language_error,
                      language=language['coverage'] if language else {'status': 'not_run'})
    if language:
        result.update(replay_origin=language.get('origin', 'student_result'),
            producer=language.get('provenance', {}).get('producer', 'student'), layer_counts=language.get('layer_counts', {}),
            human_acceptances=language['coverage'].get('human_acceptances', 0))
    return result
