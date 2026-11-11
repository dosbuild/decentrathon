"""Build one immutable output selection and its matching reader artifacts."""
import copy
import hashlib
import json
from dalel import __version__
from dalel.contracts import Finding
from dalel.formats.arb import ArbDocument
from dalel.review.store import canonical, digest
from dalel.workspace.glossary import glossary_text
from dalel.workspace.identity import raw_digest, stamp, uid


def select_output(state, expected, mode, *, documents, get_approval, validate, fingerprint, guidance):
    source, target = documents
    selected = {}; selected_revisions = {}; edits = {}; additions = {}; changes = []; errors = []; code = []
    for key, e in state['entries'].items():
        approval = get_approval(state, key)
        include = e['workingText'] != e['originalTarget'] and (mode == 'draft' or approval is not None)
        text = e['workingText'] if include else e['originalTarget']
        selected[key] = text; selected_revisions[key] = e['currentRevisionId'] if include else e['revisions'][0]['id']
        errors.extend(validate(state, key, text))
        decision = e['decisions'][-1] if e['decisions'] else None
        if decision and decision['action'] == 'code_dependency': code.append(key)
        if include:
            if text is None: raise ValueError('Cannot export a null ARB message')
            (edits if key in target.strings else additions)[key] = text
            changes.append({'key': key, 'from': e['originalTarget'], 'to': text, 'before': e['originalTarget'], 'after': text,
                'revisionId': e['currentRevisionId'], 'revision': state['backend']['entryVersions'][key],
                'approved': bool(approval), 'reviewed_by_human': bool(approval), 'reviewer': approval['reviewer'] if approval else None,
                'nativeSelfDeclared': bool(approval and approval['nativeSelfDeclared']),
                'source_hash': digest(e['source']), 'before_hash': digest(e['originalTarget']), 'after_hash': digest(text),
                'author': approval['reviewer'] if approval else 'Unreviewed workspace draft', 'at': approval['at'] if approval else e['revisions'][-1]['createdAt']})
    fixed = ArbDocument(target.render(edits), path=target.path)
    fixed = ArbDocument(fixed.insert_in_source_order(additions, list(source.strings)), path=target.path)
    for change in changes:
        key = change['key']; name = '@' + key; existing = fixed.effective.get(name)
        metadata = copy.deepcopy(existing.value) if existing else copy.deepcopy(source.effective[name].value) if name in source.effective else {}
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError(f'{key}: existing metadata cannot safely hold review provenance')
        prior = metadata.get('x-locqa', {})
        if not isinstance(prior, dict): raise ValueError(f'{key}: invalid existing review metadata')
        metadata['reviewed_by_human'] = change['reviewed_by_human']
        metadata['x-locqa'] = {**prior, **{k: change[k] for k in ('source_hash', 'before_hash', 'after_hash', 'author', 'at', 'revision')}}
        metadata['locqaReview'] = {'revision_id': change['revisionId'], 'reviewed_by_human': change['reviewed_by_human'],
            'reviewer': change['reviewer'], 'reviewed_at': change['at'] if change['reviewed_by_human'] else None,
            'native_speaker_self_declared': change['nativeSelfDeclared'], 'source_fingerprint': change['source_hash']}
        fixed = ArbDocument(fixed.render({name: metadata}) if existing else fixed.insert(name, metadata, after=key), path=target.path)
    records = []
    for key, e in state['entries'].items():
        for bucket in ('localFindings', 'importedFindings', 'manualFindings'):
            for f in e[bucket]:
                judgment = next((j for j in reversed(f.get('judgments', [])) if j.get('revisionId') == selected_revisions[key] and j.get('text_sha256') == digest(selected[key])), None)
                row = {k: f.get(k) for k in Finding.model_fields}
                row['reviewed_by_human'] = bool(judgment and judgment.get('reviewed_by_human'))
                row.update(id=f['id'], origin=f['origin'], finding_judgment=judgment['value'] if judgment else 'unassessed',
                           current_analysis=((f.get('revisionId') or f.get('basisRevisionId')) == selected_revisions[key]
                                             and f.get('glossaryVersion', 1) == state['glossaryVersion']))
                records.append(row)
                if row['current_analysis'] and isinstance(f.get('suggestion'), str) and f['suggestion'].startswith('requires_code_change') and row['finding_judgment'] != 'dismissed' and key not in code:
                    code.append(key)
    from dalel.engine.dependencies import planned_keys
    for key in planned_keys(state['backend'].get('modelDiagnostics', [])):
        if key in selected_revisions and selected_revisions[key] == state['entries'][key]['revisions'][0]['id'] and key not in code:
            code.append(key)
    active = [r for r in records if r['current_analysis'] and r['finding_judgment'] != 'dismissed']
    counts = {'entries': len(fixed.strings), 'findings': len(active), 'historical_findings': len(records) - len(active), 'affected_entries': len({r['key'] for r in active}),
              'exported_changes': len(changes), 'approved_exported_changes': sum(c['reviewed_by_human'] for c in changes),
              'native_self_declared_exported_changes': sum(c['nativeSelfDeclared'] for c in changes),
              'structural_blockers': len(errors), 'code_dependencies': len(code),
              'unresolved_findings': sum(r['finding_judgment'] == 'unassessed' for r in active)}
    coverage = copy.deepcopy(state['backend']['analysis'])
    coverage['retained_imported_types'] = sorted({r['issue_type'] for r in active if r['origin'] == 'imported'})
    coverage['matches_selected_output'] = coverage.get('checkedMessagesSha256') == digest(fixed.strings) and coverage.get('checkedGlossarySha256') == digest(state['glossary'])
    if not coverage['matches_selected_output']:
        coverage['workspace_analysis_out_of_date_for_selection'] = True
        coverage['language_complete'] = False
        coverage['language'] = {'status': 'stale', 'reason': 'Selected output or glossary differs from the recorded analysis'}
    flutter = state['backend'].get('flutter', {'status': 'not_run'})
    if flutter.get('output_sha256') != hashlib.sha256(fixed.raw).hexdigest():
        flutter = {'status': 'not_run', 'reason': 'This exact selected output has not been compiled'}
    report = {'schema_version': 1, 'product': 'dalel', 'backend_version': __version__, 'synthetic_demo': state['demo'],
              'source_locale': state['sourceLocale'], 'target_locale': state['targetLocale'], 'created_at': stamp(),
              'model_provenance': state['backend'].get('modelProvenance'), 'model_diagnostics': state['backend'].get('modelDiagnostics', []),
              'counts': counts, 'changes': changes, 'findings': records,
              'layout_guidance': [g for k, text in selected.items() for g in guidance(state, k, text)], 'check_run': state['checks'], 'coverage': coverage,
              'structural_validation': {'scope': 'full selected output', 'passed': not errors, 'errors': errors, 'flutter_compilation': flutter},
              'methodology': {'external_model_calls': 0, 'external_model_cost_usd': 0, 'flutter_gen_l10n': flutter['status'],
                              'native_review_status': 'self-declared, not independently verified'}}
    from dalel.presentation.report import render_report
    methodology = f'''# dalel — workspace release\n\nBackend version: {__version__}\nSelection: {mode}\nInput snapshot: {raw_digest(state)}\nOutput SHA-256: {hashlib.sha256(fixed.raw).hexdigest()}\n\n{len(changes)} selected changes; {counts['approved_exported_changes']} exact-version human approvals. Native-speaker attribution is self-declared ({counts['native_self_declared_exported_changes']} selected changes), not independently verified.\n\nAnalysis uses the current Python dalel engine. Mode: {coverage.get('mode', 'not_run')}. Language coverage: {canonical(coverage.get('language', {'status': 'not_run'}))}. No new model calls; API cost USD 0. Imported and manual findings remain separately attributed. A finding judgment is not a sentence approval.\n\nFull selected-output validation: {len(errors)} problems. Code dependencies: {len(code)}. Flutter gen-l10n: {flutter['status']}. Local validation is not Flutter compilation or a native-speaker verdict.\n'''
    filename = 'intl_' + state['targetLocale'] + '_fixed.arb'
    findings = [{k: row[k] for k in Finding.model_fields} for row in active]
    files = {filename: fixed.raw.decode('utf8'), 'report.json': json.dumps(findings, ensure_ascii=False, indent=2) + '\n',
             'report.html': render_report(findings, coverage, []), 'glossary.csv': glossary_text(state['glossary']),
             'methodology.md': methodology,
             'changes.json': json.dumps(changes, ensure_ascii=False, indent=2) + '\n',
             'coverage.json': json.dumps(coverage, ensure_ascii=False, indent=2) + '\n',
             'review_state.json': json.dumps(report, ensure_ascii=False, indent=2) + '\n'}
    if flutter.get('artifacts'):
        files.update(flutter.get('artifacts', {}))
    release = {'id': uid('release-'), 'at': stamp(), 'mode': mode, 'blocked': mode == 'validated' and bool(errors or code or flutter.get('literal_target', {}).get('status') != 'passed'),
               'errors': errors, 'codeDependencies': code, 'counts': counts, 'changes': changes, 'selected': selected,
               'selectedRevisions': selected_revisions, 'files': files, 'filename': filename,
               'notes': {'externalModelCalls': 0, 'externalCost': 0}, 'coverage': coverage, 'backendVersion': expected}
    release['flutter'] = state['backend'].get('flutter', {'status': 'not_run'})
    if release['flutter'].get('output_sha256') != hashlib.sha256(fixed.raw).hexdigest():
        release['flutter'] = {'status': 'not_run', 'reason': 'This exact selected output has not been compiled'}
    release['preparedReleaseId'] = next((r['id'] for r in reversed(state['releases']) if r['mode'] == mode and r.get('contentFingerprint') == fingerprint(state)), None)
    return release
