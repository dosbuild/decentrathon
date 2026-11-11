"""Private portable replay with confined paths, exact dependencies and hashes."""
from __future__ import annotations
import hashlib
import json
import csv
from pathlib import Path,PurePosixPath,PureWindowsPath
from dalel.formats.arb import ArbDocument
from dalel.delivery.comparison import detector_digest
from dalel.engine.pipeline import canonical,digest
from dalel.contracts import Finding

class ReplayMismatch(ValueError):
    """A valid package belongs to different inputs or detector dependencies."""

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def terms(path,source_locale,target_locale,*,complete=False):
    if path is None:
        return [] if complete else {}
    from dalel.terminology.glossary import rows
    entries=rows(path)
    if complete:return entries
    if any(r.get('scope') or r.get('notes') or r.get(target_locale+'_variants') for r in entries):
        raise ReplayMismatch('Extended glossary requires complete row dependencies')
    return {r[source_locale]:r[target_locale] for r in entries
            if r.get(source_locale) and r.get(target_locale)}

def confined(root,value):
    if not isinstance(value,str) or not value or ':' in value or PureWindowsPath(value).is_absolute():
        raise ValueError('Replay path must be relative and confined')
    pure=PurePosixPath(value.replace('\\','/'))
    if pure.is_absolute() or '..' in pure.parts:
        raise ValueError('Replay path escapes the package')
    path=(root/Path(*pure.parts)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Replay symlink escapes the package')
    return path

def file_record(root,path):
    return {'path':path.relative_to(root).as_posix(),'sha256':sha(path.read_bytes())}

def checked(root,record):
    path=confined(root,record['path'])
    if not path.is_file() or sha(path.read_bytes())!=record['sha256']:
        raise ValueError('Replay file hash mismatch: '+record['path'])
    return path

def package(source,target,result,out,*,references=(),glossary=None,escaping=False):
    """Package an explicitly attributed analysis; never invent human review."""
    out=Path(out).resolve();dependencies=result['dependencies']
    origin=result.get('origin','student_result')
    if origin not in ('student_result','curated_language_report'):
        raise ValueError('Unsupported replay origin')
    expected={'source_sha256':sha(source.raw),'target_sha256':sha(target.raw),
        'references':[[r.locale,sha(r.raw)] for r in references],'rules':detector_digest(),'escaping':escaping,
        'glossary':terms(glossary,source.locale,target.locale,complete=isinstance(dependencies.get('glossary'),list))}
    if any(dependencies.get(k)!=v for k,v in expected.items()):
        raise ReplayMismatch('Student result belongs to different inputs or rules')
    if result['coverage']['status']!='complete':
        raise ValueError('Only a complete Student result can be packaged')
    if digest(dependencies)!=result['identity'] or result['coverage'].get('human_acceptances')!=0:
        raise ValueError('Invalid Student provenance')
    for name in ('report','report_auto'):
        for row in result[name]:
            Finding.model_validate(row)
    artifact_key='student' if origin=='student_result' else 'analysis'
    destinations=[out/'inputs/source.arb',out/'inputs/target.arb',out/f'cache/{artifact_key}.json',out/'locqa-replay.json']
    destinations.extend(out/f'inputs/reference-{i}.arb' for i,_ in enumerate(references))
    if glossary:
        destinations.append(out/'inputs/glossary.csv')
    from dalel.terminology.glossary import sidecar,rules_digest
    glossary_rules=sidecar(glossary)
    if glossary_rules and glossary_rules.is_file():destinations.append(out/'inputs/glossary_rules.json')
    originals=[d.path for d in (source,target,*references) if d.path]+([Path(glossary)] if glossary else [])
    if glossary_rules and glossary_rules.is_file():originals.append(glossary_rules)
    if any(p.resolve()==q.resolve() or p.exists() and q.exists() and p.samefile(q) for p in destinations for q in originals):
        raise ValueError('Replay output would overwrite an input')
    for p in destinations:
        confined(out,p.relative_to(out).as_posix())
        p.parent.mkdir(parents=True,exist_ok=True)
    manifest={'format':1,'origin':origin,'human_acceptances':0,'inputs':[]}
    for role,doc,path in [('source',source,destinations[0]),('target',target,destinations[1])]+[
            ('reference',ref,out/f'inputs/reference-{i}.arb') for i,ref in enumerate(references)]:
        path.write_bytes(doc.raw)
        manifest['inputs'].append(dict(role=role,locale=doc.locale,**file_record(out,path)))
    if glossary:
        path=out/'inputs/glossary.csv';path.write_bytes(Path(glossary).read_bytes())
        manifest['glossary']=file_record(out,path)
    else:
        manifest['glossary']=None
    manifest['glossary_rules']=None
    if glossary_rules and glossary_rules.is_file():
        path=out/'inputs/glossary_rules.json';path.write_bytes(glossary_rules.read_bytes())
        manifest['glossary_rules']=file_record(out,path)
    payload=dict(result);payload.pop('payload_sha256',None)
    payload['glossary_rules_sha256']=rules_digest(glossary)
    destinations[2].write_bytes((canonical(dict(payload,payload_sha256=digest(payload)))+'\n').encode('utf8'))
    manifest[artifact_key]=file_record(out,destinations[2]);manifest['escaping']=escaping
    destinations[3].write_bytes((canonical(manifest)+'\n').encode('utf8'))
    return destinations[3]

def load(manifest_path,source,target,*,references=(),glossary=None,escaping=False):
    manifest_path=Path(manifest_path).resolve();root=manifest_path.parent
    manifest=json.loads(manifest_path.read_text(encoding='utf8'))
    if manifest.get('origin') == 'reviewed_report':
        from dalel.review.binding import load as load_reviewed
        return load_reviewed(manifest_path, source, target, references=references, glossary=glossary, escaping=escaping)
    if manifest.get('stale'):
        raise ReplayMismatch(manifest.get('stale_reason','Replay snapshot is stale'))
    if manifest.get('format')!=1 or manifest.get('origin') not in ('student_result','curated_language_report') or manifest.get('human_acceptances')!=0:
        raise ValueError('Unsupported replay provenance')
    inputs=manifest['inputs']
    if sum(r['role']=='source' for r in inputs)!=1 or sum(r['role']=='target' for r in inputs)!=1 or any(r['role'] not in ('source','target','reference') for r in inputs):
        raise ValueError('Replay input role mismatch')
    paths=[checked(root,r) for r in inputs];documents=[ArbDocument.read(p) for p in paths]
    for item,doc in zip(inputs,documents):
        if item['locale']!=doc.locale:
            raise ValueError('Replay locale metadata mismatch')
    for role,current in [('source',source),('target',target)]:
        expected=next(r for r in inputs if r['role']==role)
        if expected['sha256']!=sha(current.raw):
            raise ReplayMismatch('Replay '+role+' snapshot differs')
    refs=[doc for item,doc in zip(inputs,documents) if item['role']=='reference']
    if references and [(r.locale,sha(r.raw)) for r in references]!=[(r.locale,sha(r.raw)) for r in refs]:
        raise ReplayMismatch('Replay references differ')
    glossary_path=checked(root,manifest['glossary']) if manifest['glossary'] else None
    from dalel.terminology.glossary import rules_digest
    rules_path=checked(root,manifest['glossary_rules']) if manifest.get('glossary_rules') else None
    if rules_path and (not glossary_path or rules_path!=glossary_path.with_name('glossary_rules.json')):
        raise ValueError('Glossary policy must accompany its CSV')
    if glossary is not None and (glossary_path is None or sha(Path(glossary).read_bytes())!=sha(glossary_path.read_bytes())):
        raise ReplayMismatch('Replay glossary differs')
    if glossary is not None and rules_digest(glossary)!=rules_digest(glossary_path):
        raise ReplayMismatch('Replay glossary policy differs')
    artifact_key='student' if manifest['origin']=='student_result' else 'analysis'
    artifact=checked(root,manifest[artifact_key]);result=json.loads(artifact.read_text(encoding='utf8'))
    checksum=result.pop('payload_sha256',None)
    if digest(result)!=checksum:
        raise ValueError('Replay Student payload checksum mismatch')
    if result.get('origin','student_result')!=manifest['origin']:
        raise ValueError('Replay analysis origin mismatch')
    if result.get('glossary_rules_sha256')!=rules_digest(glossary_path):
        raise ReplayMismatch('Replay glossary policy payload differs')
    dep=result['dependencies']
    expected={'source_sha256':sha(source.raw),'target_sha256':sha(target.raw),
        'references':[[r.locale,sha(r.raw)] for r in refs],'rules':detector_digest(),'escaping':escaping,
        'glossary':terms(glossary_path,source.locale,target.locale,complete=isinstance(dep.get('glossary'),list))}
    if any(dep.get(k)!=v for k,v in expected.items()) or manifest['escaping']!=escaping:
        raise ReplayMismatch('Replay detector dependencies differ')
    if digest(dep)!=result['identity'] or result['coverage']['status']!='complete':
        raise ValueError('Replay result is incomplete or has an invalid identity')
    allowed=set(source.strings)|set(target.strings)
    for name in ('report','report_auto'):
        normalized=[]
        for row in result[name]:
            item=Finding.model_validate(row)
            if item.key not in allowed or item.source!=source.strings.get(item.key) or item.target!=target.strings.get(item.key):
                raise ValueError('Replay finding input does not match the snapshot')
            normalized.append(item.model_dump())
        result[name]=normalized
    result.update(replay=True,api_attempts=0,known_paid_response_cost=0.,replay_manifest=manifest_path.name)
    return {'language':result,'references':refs,'glossary':glossary_path,
        'protected_paths':[manifest_path,artifact,*paths,*([glossary_path] if glossary_path else []),*([rules_path] if rules_path else [])]}

def package_report(source,target,records,out,*,coverage,diagnostics=(),provenance,
                   references=(),glossary=None,escaping=False,automatic_records=None,verified_fixes=()):
    """Freeze a curated full report, explicitly separate from Student scoring.

    The supplied provenance and coverage are retained, not recast as Student
    predictions. Current formal findings are merged; curated duplicate rows
    carry their independently assessed fix/evidence. Exact input hashes bind
    the result to this dataset only. Unseen pairs never inherit these answers.
    """
    from dalel.engine.rules import check
    from dalel.contracts import finding_order
    if provenance.get('producer') not in ('teacher','student','external'):
        raise ValueError('Explicit report producer is required')
    if coverage.get('status')!='complete' or coverage.get('source_strings_evaluated')!=len(source.strings):
        raise ValueError('Complete source coverage is required for curated replay')
    formal,formal_diagnostics=check(source,target,references,glossary,escaping)
    from dalel.terminology.glossary import gate_findings,rows as glossary_rows
    records,glossary_diagnostics=gate_findings(records,glossary_rows(glossary),source.locale,target.locale)
    formal_diagnostics.extend(glossary_diagnostics)
    merged={(f.key,f.issue_type):f for f in formal};seen=set()
    for row in records:
        finding=Finding.model_validate(row);identity=(finding.key,finding.issue_type)
        if identity in seen:raise ValueError('Duplicate curated finding')
        seen.add(identity)
        if finding.source!=source.strings.get(finding.key) or finding.target!=target.strings.get(finding.key):
            raise ValueError('Curated finding input does not match the snapshot')
        if identity in merged:
            # The language layer may supply a verified fix, but cannot erase
            # the confidence attached to an independent deterministic proof.
            finding=finding.model_copy(update={'confidence':merged[identity].confidence})
        merged[identity]=finding
    report=[f.model_dump() for f in sorted(merged.values(),key=finding_order)]
    # Formal glossary rows must not reintroduce a second card for the same
    # whole-label meaning error after curated publication has been filtered.
    report,merged_glossary_diagnostics=gate_findings(report,glossary_rows(glossary),source.locale,target.locale)
    formal_diagnostics.extend(merged_glossary_diagnostics)
    from dalel.engine.merge import deduplicate
    report,merge_diagnostics=deduplicate(report,curated=records)
    formal_diagnostics.extend(merge_diagnostics)
    automatic=report
    if automatic_records is not None:
        automatic=[]
        for row in automatic_records:
            finding=Finding.model_validate(row)
            if finding.source!=source.strings.get(finding.key) or finding.target!=target.strings.get(finding.key):
                raise ValueError('Automatic candidate input does not match the snapshot')
            if finding.reviewed_by_human:raise ValueError('Automatic candidates cannot claim human review')
            automatic.append(finding.model_dump())
    dependencies={'source_sha256':sha(source.raw),'target_sha256':sha(target.raw),
        'references':[[r.locale,sha(r.raw)] for r in references],'rules':detector_digest(),'escaping':escaping,
        'glossary':terms(glossary,source.locale,target.locale,complete=True)}
    unique={canonical(d):d for d in [*formal_diagnostics,*diagnostics]}
    result={'origin':'curated_language_report','provenance':provenance,'full_merged_report':True,
        'dependencies':dependencies,'identity':digest(dependencies),'report':report,'report_auto':automatic,
        'diagnostics':list(unique.values()),'coverage':dict(coverage,human_acceptances=0),
        'replay':False,'api_attempts':0,'known_paid_response_cost':0.0,
        'layer_counts':{'formal':len(formal),'curated':len(records),'merged':len(report)}}
    if verified_fixes:
        result['fixes'] = list(verified_fixes)
    return package(source,target,result,out,references=references,glossary=glossary,escaping=escaping)

def discover(source,target,*,references=(),glossary=None,escaping=False,explicit=None,cwd=None):
    candidates=[Path(explicit)] if explicit else [Path(cwd or Path.cwd())/'locqa-replay.json',
                         *([Path(glossary).resolve().parent/'locqa-replay.json'] if glossary else [])]
    matches=[]
    for path in dict.fromkeys(p.resolve() for p in candidates):
        if not path.exists():
            if explicit:
                raise ValueError('Explicit replay manifest is missing')
            continue
        try:
            matches.append(load(path,source,target,references=references,glossary=glossary,escaping=escaping))
        except ReplayMismatch:
            if explicit:
                raise
    if len(matches)>1:
        raise ValueError('Ambiguous compatible replay manifests')
    return matches[0] if matches else None
