"""Reproduce a reviewed report by revalidating its exact imported decisions."""
import json
from pathlib import Path

from dalel.contracts import ReviewedFinding
from dalel.delivery.comparison import detector_digest
from dalel.delivery.replay import ReplayMismatch, checked, file_record, sha
from dalel.formats.arb import ArbDocument
from dalel.review.screen import apply, canonical


def bind(bundle, summary):
    bundle = Path(bundle).resolve()
    manifest_path = bundle/'review_manifest.json'
    review = json.loads(manifest_path.read_text(encoding='utf8'))
    source = bundle/f"intl_{review.get('source_locale', 'ru')}.arb"
    target = bundle/f"intl_{review.get('target_locale', 'kk')}.arb"
    decisions_path = next((p for p in (bundle/'review_history').glob('*.json')
        if sha(canonical(json.loads(p.read_text(encoding='utf8'))).encode('utf8')) == summary['decisions_sha256']), None)
    if decisions_path is None:
        raise ValueError('Exact imported decision history is required')
    evaluated = apply(review, json.loads(decisions_path.read_text(encoding='utf8')), ArbDocument.read(source), ArbDocument.read(target))
    report_path = bundle/'report.json'
    if json.loads(report_path.read_text(encoding='utf8')) != evaluated['report']:
        raise ValueError('Reviewed report differs from the decision record')
    fixed = bundle/f"intl_{ArbDocument.read(target).locale}_fixed.arb"
    if fixed.read_bytes() != evaluated['fixed']:
        raise ValueError('Reviewed ARB differs from the decision record')
    old_path = bundle/'locqa-replay.json'
    old = json.loads(old_path.read_text(encoding='utf8')) if old_path.exists() else {}
    references = [checked(bundle, r) for r in old.get('inputs', []) if r['role'] == 'reference']
    coverage_path = bundle/'coverage.json'
    coverage = json.loads(coverage_path.read_text(encoding='utf8')) if coverage_path.exists() else {}
    # A human import changes approval, not the measured language coverage.
    coverage.update(mode='replay', api_calls=0, seconds=0, human_acceptances=summary['human_accepted_strings'],
                    findings=len(evaluated['report']), replay_origin='reviewed_report')
    diagnostics_path = bundle/'diagnostics.json'
    diagnostics = json.loads(diagnostics_path.read_text(encoding='utf8')) if diagnostics_path.exists() else []
    from dalel.presentation.report import render_report
    html = render_report(evaluated['report'], coverage, diagnostics)
    automatic_path = bundle/'report_auto.json'
    automatic = json.loads(automatic_path.read_text(encoding='utf8')) if automatic_path.exists() else []
    artifact = dict(report=evaluated['report'], report_json=report_path.read_bytes().decode('utf8'),
                    report_html=html, coverage=coverage, diagnostics=diagnostics, report_auto=automatic)
    archive = bundle/'cache'/('reviewed-' + sha(canonical(artifact).encode('utf8')) + '.json')
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(canonical(artifact) + '\n', encoding='utf8')
    manifest = dict(format=1, origin='reviewed_report', rules=detector_digest(), escaping=old.get('escaping', False),
        inputs=[dict(role=role, **file_record(bundle, p)) for role, p in
                [('source', source), ('target', target), *[('reference', p) for p in references]]],
        review_manifest=file_record(bundle, manifest_path), decisions=file_record(bundle, decisions_path),
        reviewed=file_record(bundle, archive),
        outputs=[file_record(bundle, p) for p in (fixed, bundle/'changes.json')],
        glossary=file_record(bundle, bundle/'glossary.csv') if (bundle/'glossary.csv').exists() else None,
        glossary_rules=file_record(bundle, bundle/'glossary_rules.json') if (bundle/'glossary_rules.json').exists() else None)
    (bundle/'report.html').write_bytes(html.encode('utf8'))
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    old_path.write_text(canonical(manifest) + '\n', encoding='utf8')


def load(path, source, target, *, references=(), glossary=None, escaping=False):
    path = Path(path).resolve()
    root = path.parent
    manifest = json.loads(path.read_text(encoding='utf8'))
    if manifest.get('stale') or manifest['rules'] != detector_digest() or manifest['escaping'] != escaping:
        raise ReplayMismatch('Reviewed replay dependencies changed; the existing report is preserved')
    documents = [(r, ArbDocument.read(checked(root, r))) for r in manifest['inputs']]
    for role, document in [('source', source), ('target', target)]:
        matching = [r for r, _ in documents if r['role'] == role]
        if len(matching) != 1 or matching[0]['sha256'] != sha(document.raw):
            raise ReplayMismatch('Reviewed input differs')
    refs = [d for r, d in documents if r['role'] == 'reference']
    if references and [(r.locale, sha(r.raw)) for r in references] != [(r.locale, sha(r.raw)) for r in refs]:
        raise ReplayMismatch('Reviewed references differ')
    glossary_path = checked(root, manifest['glossary']) if manifest.get('glossary') else None
    rules_path = checked(root, manifest['glossary_rules']) if manifest.get('glossary_rules') else None
    if glossary:
        from dalel.terminology.glossary import rules_digest
        if glossary_path is None or sha(Path(glossary).read_bytes()) != sha(glossary_path.read_bytes()) or rules_digest(glossary) != rules_digest(glossary_path):
            raise ReplayMismatch('Reviewed glossary differs')
    review_path, decisions_path = (checked(root, manifest[k]) for k in ('review_manifest', 'decisions'))
    result = apply(json.loads(review_path.read_text(encoding='utf8')), json.loads(decisions_path.read_text(encoding='utf8')), source, target)
    output_paths = [checked(root, r) for r in manifest['outputs']]
    fixed = next(p for p in output_paths if p.suffix == '.arb')
    changes = next(p for p in output_paths if p.name == 'changes.json')
    artifact_path = checked(root, manifest['reviewed'])
    artifact = json.loads(artifact_path.read_text(encoding='utf8'))
    rows = [ReviewedFinding.model_validate(row).model_dump() for row in artifact['report']]
    if rows != result['report'] or json.loads(artifact['report_json']) != rows or fixed.read_bytes() != result['fixed'] or json.loads(changes.read_text(encoding='utf8')) != result['changes']:
        raise ValueError('Reviewed output does not match imported decisions')
    language = dict(origin='reviewed_report', full_merged_report=True, report=rows,
        report_auto=artifact['report_auto'], diagnostics=artifact['diagnostics'], coverage=dict(artifact['coverage'], status='complete'),
        identity=manifest['reviewed']['sha256'], replay=True, api_attempts=0, known_paid_response_cost=0,
        provenance={'producer': artifact['coverage'].get('producer', 'external')},
        reviewed_report_json=artifact['report_json'], reviewed_report_html=artifact['report_html'],
        display_coverage=artifact['coverage'], reviewed_fixed=result['fixed'].decode('utf8'), reviewed_changes=result['changes'])
    return dict(language=language, references=refs, glossary=glossary_path,
        protected_paths=[path, artifact_path, review_path, decisions_path, *output_paths,
                         *[d.path for _, d in documents], *([glossary_path] if glossary_path else []), *([rules_path] if rules_path else [])])
