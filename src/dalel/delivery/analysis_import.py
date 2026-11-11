"""Explicit migration of a portable analysis into the current engine contract.

An imported detector fingerprint is retained as provenance, never silently
declared compatible. The current rules merge the saved findings again.
"""
import json
from pathlib import Path

from dalel.contracts import Finding
from dalel.delivery.replay import checked, package_report, sha, terms as replay_terms
from dalel.engine.pipeline import digest
from dalel.formats.arb import ArbDocument


def import_analysis(manifest_path, destination):
    manifest_path, destination = Path(manifest_path).resolve(), Path(destination).resolve()
    root = manifest_path.parent
    if destination.exists():
        raise ValueError('Analysis import requires a new output directory')
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    if manifest.get('format') != 1 or manifest.get('origin') not in ('student_result', 'curated_language_report'):
        raise ValueError('Import a machine analysis; human decisions need the review importer')
    records = [(record, ArbDocument.read(checked(root, record))) for record in manifest['inputs']]
    def role(name):
        selected = [doc for record, doc in records if record['role'] == name]
        if len(selected) != 1:
            raise ValueError('Exactly one source and target are required')
        return selected[0]
    source, target = role('source'), role('target')
    references = [doc for record, doc in records if record['role'] == 'reference']
    glossary = checked(root, manifest['glossary']) if manifest.get('glossary') else None
    if manifest.get('glossary_rules'):
        checked(root, manifest['glossary_rules'])
    original = checked(root, manifest.get('analysis') or manifest['student'])
    saved = json.loads(original.read_text(encoding='utf8'))
    payload = dict(saved)
    checksum = payload.pop('payload_sha256', None)
    if checksum != digest(payload) or digest(saved['dependencies']) != saved['identity']:
        raise ValueError('Analysis checksum or dependency identity differs')
    from dalel.terminology.glossary import rules_digest
    if saved.get('glossary_rules_sha256') != rules_digest(glossary):
        raise ValueError('Saved glossary policy differs')
    expected = {'escaping': manifest['escaping'],
                'glossary': replay_terms(glossary, source.locale, target.locale, complete=isinstance(saved['dependencies'].get('glossary'), list)),
                'source_sha256': sha(source.raw), 'target_sha256': sha(target.raw),
                'references': [[r.locale, sha(r.raw)] for r in references]}
    if any(saved['dependencies'].get(key) != value for key, value in expected.items()):
        raise ValueError('Saved analysis belongs to different input bytes')
    if saved['coverage'].get('status') != 'complete' or saved['coverage'].get('human_acceptances') != 0:
        raise ValueError('Only complete nonhuman analysis can be migrated')
    for name in ('report', 'report_auto'):
        for row in saved[name]:
            finding = Finding.model_validate(row)
            if finding.source != source.strings.get(finding.key) or finding.target != target.strings.get(finding.key):
                raise ValueError('Saved finding belongs to a different message')
    provenance = dict(saved.get('provenance') or {'producer': 'student'},
                      imported_analysis_sha256=sha(original.read_bytes()),
                      original_detector_sha256=saved['dependencies']['rules'])
    verified_fixes = list(saved.get('fixes', []))
    changes_path = root/'changes.json'
    if changes_path.is_file():
        recorded_changes = json.loads(changes_path.read_text(encoding='utf8'))
        for change in recorded_changes:
            verification = change.get('verification') or {}
            if change.get('reviewed_by_human') or change.get('before') != target.strings.get(change['key']):
                raise ValueError('Machine change has human attribution or different original text')
            if verification.get('text') != change['after'] or verification.get('verdict') != 'strict_win':
                raise ValueError('Imported correction lacks an exact strict-improvement verdict')
            if not any(r['key'] == change['key'] and r.get('suggestion') == change['after'] for r in saved['report']):
                raise ValueError('Imported correction is not a saved finding proposal')
        verified_fixes = [c['verification'] for c in recorded_changes]
        provenance['recorded_changes_sha256'] = sha(changes_path.read_bytes())
    evaluated = saved['coverage'].get('source_strings_evaluated', saved['coverage'].get('total_strings'))
    if evaluated != len(source.strings):
        raise ValueError('Saved coverage does not cover these source messages')
    coverage = dict(saved['coverage'], source_strings_evaluated=evaluated)
    packaged = package_report(source, target, saved['report'], destination,
        coverage=coverage, provenance=provenance, references=references, glossary=glossary,
        diagnostics=saved['diagnostics'], automatic_records=saved['report_auto'], escaping=manifest['escaping'],
        verified_fixes=verified_fixes)
    from dalel.delivery.replay import load
    language = load(packaged, source, target, references=references, glossary=destination/'inputs/glossary.csv' if glossary else None,
                    escaping=manifest['escaping'])['language']
    from dalel.engine.corrections import apply_ai_changes
    from dalel.terminology.glossary import rows
    terms = rows(glossary, include_candidates=True)
    fixed, changes = apply_ai_changes(source, target, language['report'], provenance=provenance,
        glossary=terms, escaping=manifest['escaping'], verified_fixes=verified_fixes)
    from dalel.cli import write_json
    from dalel.presentation.report import render_report
    from dalel.review.screen import write_screen
    for document in (source, target, *references):
        (destination/f'intl_{document.locale}.arb').write_bytes(document.raw)
    if glossary:
        (destination/'glossary.csv').write_bytes(glossary.read_bytes())
        if manifest.get('glossary_rules'):
            (destination/'glossary_rules.json').write_bytes(checked(root, manifest['glossary_rules']).read_bytes())
    (destination/f'intl_{target.locale}_fixed.arb').write_bytes(fixed)
    for name, value in [('changes.json', changes), ('report.json', language['report']),
                        ('report_auto.json', language['report_auto']), ('diagnostics.json', language['diagnostics'])]:
        write_json(destination/name, value)
    from dalel.engine.coverage import describe
    display = describe(source, target, language['report'], language, glossary, mode='replay', profile='offline', language_complete=True)
    write_json(destination/'coverage.json', display)
    (destination/'report.html').write_text(render_report(language['report'], display, language['diagnostics']), encoding='utf8')
    write_screen(source, target, language['report'], destination, glossary=terms)
    result = dict(source_strings=len(source.strings), findings=len(language['report']), changes=len(changes),
                  exact_saved_findings=language['report'] == saved['report'], api_calls=0, human_acceptances=0,
                  original_analysis_sha256=sha(original.read_bytes()))
    write_json(destination/'import_summary.json', result)
    return result
