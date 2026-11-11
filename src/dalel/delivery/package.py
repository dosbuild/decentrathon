"""Build a private canonical delivery and an explicitly allowlisted public tree."""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from pathlib import Path
from dalel.resources import PACKAGE_ROOT
from dalel.formats.arb import ArbDocument
from dalel.engine.corrections import apply_ai_changes
from dalel.engine.dependencies import planned_keys
from dalel.engine.coverage import describe
from dalel.presentation.report import render_report
from dalel.languages.latin import convert_document
from dalel.review.screen import write_screen
from dalel.delivery.replay import package_report,load
from dalel.contracts import Finding


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8'))


def methodology(stats):
    counts=stats['findings_by_type'];nulls=stats['null_suggestions_by_type']
    methods=[
        ('untranslated','Source keys absent from target; empty concatenation fragments are checked with siblings.','Independent translation, preserving source arguments and line structure.'),
        ('same_as_source','Exact copied source text with language and accepted-loan filters. Russian lexical coverage is strongest.','Independent translation; shared loans are preserved.'),
        ('placeholder_mismatch','Parsed argument identities, roles and selector paths; positional token multisets.','One-edit argument-name repair where unambiguous, otherwise a verified full rewrite.'),
        ('plural_mismatch','ICU grammar, required other branch and compatible control roles.','Explicit structural repair or developer migration; never a guessed argument.'),
        ('wrong_language','Reference-locale equality and foreign-script function words, with brand/shared-token exclusions; language assessment for other cases.','Verified target-language replacement.'),
        ('suffix_harmony','Kazakh fixed case/possessive endings attached to runtime placeholders.','Affix-free rephrase or explicit requires_code_change with a call-site plan.'),
        ('glossary_violation','Approved terms only: binding UI conventions, a consumer-supplied glossary, or named human-approved rows. AI-proposed alternatives never establish a defect. Token boundaries, allowed variants and attested Kazakh inflections are respected.','AI-proposed terms guide consistency of independently justified fixes; they do not justify synonym replacement.'),
        ('orthography','Public Apertium-kaz lexicon, morphology and letter-confusion rules, plus independently checked linguistic judgments.','Attested replacement; no letter-wide substitutions.'),
        ('machine_translation_suspect','Source meaning, references, related strings and independent linguistic checks of action, agency, negation, grammar and quantities.','Blind comparison against the original; only a strict improvement can enter the changed set.'),
        ('inconsistent_term','Related game entities and minority variants; harmless synonyms and inflection differences are diagnostic.','Approved canonical game terminology, with the original register retained.'),
        ('length_overflow','Short UI length risk and linguistic assessment; exact implementation coverage is recorded in coverage.json.','Concise wording only where a real defect is independently established.'),
    ]
    table='\n'.join('| '+kind+' | '+str(counts.get(kind,0))+' | '+detection+' | '+fix+' |' for kind,detection,fix in methods)
    return f'''# Methodology

This package contains machine analysis and AI-verified proposed changes. Human acceptance is a separate, named action. No AI decision is a human review.

## Reproduced counts

- Source messages: {stats['source_strings']}; merged findings: {stats['findings']}.
- Raw automatic candidates before publication review: {stats.get('automatic_candidates',stats['findings'])}. report_auto.json preserves this separate audit input; it is not the accepted finding set and does not authorize corrections.
- Formal layer: {stats['layer_counts'].get('formal',0)}; curated language layer: {stats['layer_counts'].get('curated',0)}.
- Published non-null suggestions: {stats['suggestions']}; distinct verified changed strings: {stats['changed_strings']}.
- Human-accepted changed strings: {stats['human_accepted_strings']}.
- Language coverage: {stats['language_coverage']}; self-check status: {stats['self_check_status']}.
- Suggestions requiring code changes: {stats['requires_code_change']}.

| Type | Findings | Detection | Correction |
|---|---:|---|---|
{table}

## Verification and thresholds

Formal parsing is deterministic. Named arguments are compared by identity, role and selector path, not repeated occurrence count. Byte-preserving edits touch effective value spans; original duplicate members and unaffected metadata remain unchanged.

The linguistic layer uses an OpenAI-compatible provider and an exact-input cache. The replay identifies its producer explicitly; a Teacher replay is not a measured Student prediction. References are supporting evidence, not approved translations. Glossary terms retain separate machine/policy provenance.

Each AI correction must fix a medium or high defect, preserve placeholders, tags, private-use glyphs and original edge whitespace, retain source line structure, and pass independent blind verification. The validator also checks singular nouns after counts (including inherited count context), ordinal hyphens, comparison case, source ALL CAPS, and dominant family register. Source capitals are restored only in visible ICU text nodes; placeholders, selectors, tags, URLs and positional arguments are unchanged. The exact independently assessed text and any deterministic case transformation are recorded separately in changes.json. The transformation is not claimed as a new model review. A family register needs at least three marked strings and 80% agreement. Short-label expansion is flagged above both a 1.8 ratio and 12 extra visible characters for sources up to 32 characters and four words. These conservative screening thresholds flag review work; they do not establish semantic correctness.

Severity: high for missing meaning, broken arguments/ICU, foreign or untranslated text, and opposite/unrelated meaning; medium for spelling, grammar with meaning retained, affixes and approved terminology; low for length risk. Confidence is detector evidence strength, not a promise of human acceptance. The observed distribution is in delivery_stats.json; statistical calibration on new locales remains a limitation.

Null suggestions by type: `{json.dumps(nulls,ensure_ascii=False,sort_keys=True)}`. Their reason classes are retained in diagnostics.json. A code-change instruction is never written into a translated message.

## Human acceptance and integration

Open review.html offline. Applicable canon decisions come first; their cards show all matching siblings. A accepts, R rejects, E edits and the arrow keys navigate. Changing a canon invalidates dependent message decisions. Export named, timestamped decisions and run `locqa apply-review decisions.json --bundle .`. Only a real accepted decision sets reviewed_by_human to true. Edited text passes the structural validator again. The import updates the fixed ARB, change manifest, report and these counts, regenerates Latin output and marks the old full-file check, replay and Flutter proof stale. Rebuild the full-file analysis and rerun Flutter verification.

Latin conversion uses the archived 2018 decree profile and the final fixed ARB as its input. latin_length_report.json records lengths, unresolved characters and the actual glyph-coverage status. Flutter evidence must match the fixed input hash; an unavailable or stale check is reported as not_run.

## Limits

Language judgments can be wrong; a human acceptance pass is required for release. Keyless analysis of new files has only the deterministic coverage listed by the command. An offline replay applies only to its exact inputs and rule version. A successful JSON/schema check does not imply linguistic precision. The package's delivery_status.json records unmet release conditions instead of treating them as passed.
'''


def refresh_methodology(root,summary=None):
    root=Path(root);path=root/'delivery_stats.json'
    if not path.is_file():return
    stats=json.loads(path.read_text(encoding='utf8'))
    changes=json.loads((root/'changes.json').read_text(encoding='utf8'))
    stats['changed_strings']=len(changes)
    stats['human_accepted_strings']=sum(r.get('reviewed_by_human') is True for r in changes)
    report=json.loads((root/'report.json').read_text(encoding='utf8'))
    stats.update(findings=len(report),findings_by_type=dict(Counter(r['issue_type'] for r in report)),
        suggestions=sum(r.get('suggestion') is not None for r in report),
        null_suggestions_by_type=dict(Counter(r['issue_type'] for r in report if r.get('suggestion') is None)))
    if summary is not None:stats['last_human_import']=summary
    write_json(path,stats);(root/'methodology.md').write_bytes(methodology(stats).encode('utf8'))


def refresh_after_review(root,summary,*,bind_review=False):
    """Refresh derived bytes and invalidate proofs tied to the earlier ARB."""
    root=Path(root);fixed=ArbDocument.read(root/'intl_kk_fixed.arb')
    digest=hashlib.sha256(fixed.raw).hexdigest()
    latin,length=convert_document(fixed)
    (root/'intl_kk_Latn.arb').write_bytes(latin);write_json(root/'latin_length_report.json',length)
    stale={'status':'not_run','reason':'Human review changed the input snapshot; rerun validation.',
        'fixed_sha256':digest,'language_complete':False}
    for name in ('flutter_validation.json','flutter_bundle/validation.json','self_check.json','fixed_delta.json'):
        path=root/name
        if path.exists():
            prior=json.loads(path.read_text(encoding='utf8'))
            if name=='flutter_bundle/validation.json':
                write_json(root/'flutter_bundle/history'/(hashlib.sha256(path.read_bytes()).hexdigest()+'.json'),
                    {'archive_reason':'Superseded by reviewed ARB bytes','evidence':prior})
        write_json(path,stale)
    stats=root/'delivery_stats.json'
    if stats.exists():
        value=json.loads(stats.read_text(encoding='utf8'));value['self_check_status']='not_run';write_json(stats,value)
    status=root/'delivery_status.json'
    value=json.loads(status.read_text(encoding='utf8')) if status.exists() else {}
    value.update(status='incomplete',fixed_sha256=digest,changed_strings=summary['changed_strings'],
        human_acceptances=summary['human_accepted_strings'],human_approved_terms=summary.get('human_approved_terms',0),
        report_sha256=hashlib.sha256((root/'report.json').read_bytes()).hexdigest(),fixed_file_check=stale,
        unmet_conditions=list(dict.fromkeys([*value.get('unmet_conditions',[]),'full_fixed_file_self_check','flutter_verification'])))
    write_json(status,value)
    replay=root/'locqa-replay.json'
    if replay.exists():
        value=json.loads(replay.read_text(encoding='utf8'));value['stale']=True
        value['stale_reason']='Review decisions changed the delivered report or glossary; regenerate the analysis package.'
        write_json(replay,value)
    if bind_review:
        from dalel.review.binding import bind
        bind(root, summary)
    refresh_methodology(root,summary)
    readme=f'''# dalel reviewed delivery

Current changes: {summary['changed_strings']}; human-accepted changed strings: {summary['human_accepted_strings']}; human-approved terms in this import: {summary.get('human_approved_terms',0)}.
Fixed ARB SHA256: `{digest}`. Original input ARBs remain unchanged.

`intl_kk_Latn.arb` and `latin_length_report.json` were regenerated from these exact reviewed bytes. `changes.json`, `report.json`, `delivery_status.json` and `methodology.md` reflect the imported decisions. Exact decisions are retained in `review_history/`.

The earlier full-file check and Flutter proof are stale. Run `locqa verify-flutter --bundle . --out flutter_bundle/validation.json` with Flutter 3.47.9. Rebuild the full-file analysis against the reviewed ARB and approved glossary before release. The reviewed replay revalidates the imported decisions and preserves their exact human flags.

Canon decisions do not accept messages. Changing a canon requires all dependent messages to be reviewed again. No simulation decision creates human approval.
'''
    (root/'README.md').write_bytes(readme.encode('utf8'))


def correction_exclusions(source,target,fixed,changes,glossary,references=(),*,language_findings=()):
    """Current per-change rules, shared by publication and the review gate."""
    from dalel.engine.rules import check
    from dalel.engine.hygiene import FixValidator
    from dalel.engine.context import CorpusPolicy
    from dalel.engine.pipeline import input_rows
    from dalel.terminology.glossary import rows,policy_map
    formal,diagnostics=check(source,fixed,references,glossary)
    defects=[r.model_dump() for r in formal]
    for row in language_findings:
        item=Finding.model_validate(row)
        if item.source!=source.strings.get(item.key) or item.target!=fixed.strings.get(item.key):
            raise ValueError('Cached self-check finding belongs to different message text')
        defects.append(item.model_dump())
    terms=rows(glossary,include_candidates=True)
    validator=FixValidator(context=CorpusPolicy(input_rows(source,target,references),(),policy_map(terms,source.locale,target.locale)),
        approved_glossary=rows(glossary))
    excluded=[]
    for change in changes:
        key=change['key'];reasons=[]
        reasons.extend({'rule':'fixed_file_finding','finding':r} for r in defects if r['key']==key and r['severity'] in ('high','medium'))
        if target.locale.split('_')[0].split('-')[0]=='kk':
            reasons.extend(validator.check(source.strings.get(key),change['before'],change['after'],key=key))
        for diagnostic in diagnostics:
            if diagnostic.get('key')!=key or diagnostic['type'] not in ('protected_token_mismatch','concat_whitespace'):continue
            if diagnostic['type']=='concat_whitespace' and isinstance(change['before'],str):
                import re
                # An inherited source/target spacing mismatch remains visible
                # in diagnostics. A semantic fix that preserves both original
                # target edges has not introduced a new concatenation defect.
                if all(re.search(pattern,change['before']).group()==re.search(pattern,change['after']).group()
                       for pattern in (r'^\s*',r'\s*$')):continue
            reasons.append(diagnostic)
        if reasons:excluded.append({'key':key,'reasons':reasons})
    return excluded


def review_gate(source,target,fixed,changes,cached,glossary,references=()):
    """Use exact cached language evidence; reject individual unsafe changes."""
    complete=(cached.get('language_complete') is True and cached.get('fixed_sha256')==hashlib.sha256(fixed.raw).hexdigest()
        and cached.get('coverage',{}).get('source_strings_evaluated')==len(source.strings)
        and isinstance(cached.get('findings_detail'),list))
    excluded=correction_exclusions(source,target,fixed,changes,glossary,references,
        language_findings=cached['findings_detail'] if complete else ())
    if not complete:
        indexed={r['key']:r for r in excluded}
        for change in changes:
            indexed.setdefault(change['key'],{'key':change['key'],'reasons':[]})['reasons'].append({'rule':'cached_language_self_check_unavailable'})
        excluded=list(indexed.values())
    return {'passed':complete,'reason':'cached_language_and_current_formal_per_change',
        'excluded_keys':[r['key'] for r in excluded],'api_calls':0},excluded


def public_export(project,out):
    project=Path(project).resolve();out=Path(out).resolve()
    if out==project or project.is_relative_to(out):raise ValueError('Public export overlaps its project')
    manifest=out/'.locqa-public-export.json';prior={}
    if out.exists() and any(out.iterdir()):
        if not manifest.is_file():raise ValueError('Nonempty public output is not a managed export')
        prior=json.loads(manifest.read_text(encoding='utf8'))['files']
        for name,digest in prior.items():
            path=(out/name).resolve()
            if not path.is_relative_to(out) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('Public export was modified; preserve it and use another output')
        extras=[p for p in out.rglob('*') if p.is_file() and p!=manifest and p.relative_to(out).as_posix() not in prior]
        if extras:raise ValueError('Public export contains unowned files')
    copied=[]
    for name in ('pyproject.toml','build_backend.py','LICENSE','README.md','METHODOLOGY.md','NOTES.md','.env.example','MANIFEST.in','action.yml','docs/design.md','docs/contract.md','docs/guide.md',
                 'docs/images/readme/workspace.jpg','docs/images/readme/validation.jpg','docs/images/readme/glossary.jpg',
                 'docs/images/readme/review.jpg','docs/images/readme/release.jpg',
                 'tools/consumer_check.py','tools/import_kazakh_lexicon.py','tools/import_uzbek_lexicon.py'):
        path=project/name
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(project):copied.append((path,out/name))
    for folder in ('src/dalel','src/locqa'):
        for path in (project/folder).rglob('*'):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(project) and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.pyo') and 'demo' not in path.parts and path.name != 'demo.js':
                copied.append((path,out/path.relative_to(project)))
    for source,destination in copied:
        if not destination.resolve().is_relative_to(out):raise ValueError('Public destination escapes output')
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(source.read_bytes())
    current={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for _,p in copied}
    for name in prior.keys()-current.keys():
        path=(out/name).resolve()
        if not path.is_relative_to(out):raise ValueError('Obsolete public destination escapes output')
        path.unlink()
    write_json(manifest,{'format':1,'files':current})
    return {'files':len(copied),'policy':'allowlist: package source, build metadata, licence and product documentation'}


def consumer_bundle(project,out):
    """Ship actual installable, hash-pinned software beside the consumer workflow."""
    import os
    import subprocess
    import sys
    project=Path(project).resolve();out=Path(out).resolve();source=out/'public-source'
    public_export(project,source)
    action=out/'.github/actions/locqa';action.mkdir(parents=True,exist_ok=True)
    source_hash=hashlib.sha256((source/'.locqa-public-export.json').read_bytes()).hexdigest()
    record_path=action/'wheel.json';prior=json.loads(record_path.read_text(encoding='utf8')) if record_path.is_file() else {}
    cached=action/prior.get('path','missing')
    valid=prior.get('source_sha256')==source_hash and cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest()==prior.get('sha256')
    if not valid:
        # Build on a disposable copy; setuptools must not mutate the canonical
        # public source export or its manifest with build/egg-info files.
        import tempfile,shutil
        with tempfile.TemporaryDirectory(prefix='locqa-wheel-') as temporary:
            if not Path(temporary).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise ValueError('Temporary build directory escapes the system temp root')
            staging=Path(temporary)/'source';shutil.copytree(source,staging)
            destination=action/'wheel';destination.mkdir(exist_ok=True)
            command=[sys.executable,'-m','pip','wheel','--no-deps','--disable-pip-version-check','--wheel-dir',str(destination),str(staging)]
            result=subprocess.run(command,env=dict(os.environ,SOURCE_DATE_EPOCH='315532800',PYTHONUTF8='1'),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            log=result.stdout.decode('utf8',errors='replace').replace('\r\n','\n')
            import re
            for path,label in ((temporary,'BUILD_DIR'),(str(out),'DELIVERY_DIR'),(str(project),'PROJECT_DIR'),
                               (tempfile.gettempdir(),'TEMP_DIR'),(str(Path.home()),'USER_HOME')):
                for spelling in (path,path.replace('\\','/')):log=re.sub(re.escape(spelling),lambda _: '<'+label+'>',log,flags=re.I)
            if result.returncode:
                (out/'wheel_build.log').write_bytes(log.encode('utf8'))
                raise ValueError('Consumer wheel build failed; inspect wheel_build.log')
        wheels=list(destination.glob('dalel-*.whl'))
        if len(wheels)!=1:raise ValueError('Expected one pinned locqa wheel')
        wheel=wheels[0];prior={'path':wheel.relative_to(action).as_posix(),'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'source_sha256':source_hash}
        write_json(record_path,prior)
    (action/'action.yml').write_bytes((project/'action.yml').read_bytes())
    (action/'tools').mkdir(exist_ok=True)
    (action/'tools/consumer_check.py').write_bytes((project/'tools/consumer_check.py').read_bytes())
    return {'status':'shipped','wheel_sha256':prior['sha256'],'source_sha256':source_hash}


def capture_flutter_evidence(bundle,out):
    """Freeze real byte-matched SDK evidence as a portable package-build input."""
    bundle=Path(bundle).resolve();out=Path(out).resolve()
    if out==bundle or out.is_relative_to(bundle) or bundle.is_relative_to(out):
        raise ValueError('Flutter evidence output overlaps the delivery')
    proof=cached_flutter(bundle)
    if proof is None:raise ValueError('Only current passed Flutter evidence can be captured')
    def portable(value,key=None):
        if isinstance(value,dict):return {k:portable(v,k) for k,v in value.items()}
        if isinstance(value,list):return [portable(v,key) for v in value]
        if key in ('log','generated_file','screenshot') and isinstance(value,str):
            path=Path(value)
            if path.is_absolute() and path.is_relative_to(bundle):return path.relative_to(bundle).as_posix()
        return value
    files={};payloads={}
    for path in sorted((bundle/'flutter_bundle').rglob('*')):
        if not path.is_file() or any(part in ('.dart_tool','build') for part in path.relative_to(bundle).parts):continue
        if path.suffix not in ('.arb','.dart','.json','.yaml','.lock','.log','.png'):continue
        if not path.resolve().is_relative_to(bundle):raise ValueError('Flutter evidence symlink escapes the delivery')
        name=path.relative_to(bundle).as_posix();raw=path.read_bytes()
        if path.suffix=='.json':raw=(json.dumps(portable(json.loads(raw)),ensure_ascii=False,indent=2)+'\n').encode('utf8')
        payloads[name]=raw;files[name]=hashlib.sha256(raw).hexdigest()
    result={'format':1,'fixed_sha256':hashlib.sha256((bundle/'intl_kk_fixed.arb').read_bytes()).hexdigest(),
        'origin':'actual_pinned_sdk_run','files':files}
    if out.exists() and any(out.iterdir()):
        manifest=out/'flutter_evidence.json'
        if not manifest.is_file() or json.loads(manifest.read_text(encoding='utf8'))!=result:
            raise ValueError('Preserve existing Flutter evidence; use another output directory')
        present={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in out.rglob('*') if p.is_file() and p!=manifest}
        if present!=files:raise ValueError('Existing Flutter evidence was modified')
        return {'status':'captured','files':len(files),'fixed_sha256':result['fixed_sha256']}
    for name,raw in payloads.items():
        destination=out/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
    write_json(out/'flutter_evidence.json',result)
    return {'status':'captured','files':len(files),'fixed_sha256':result['fixed_sha256']}


def restore_flutter_evidence(evidence,bundle):
    """Copy authenticated frozen evidence; never infer a pass from its presence."""
    from dalel.delivery.replay import confined
    evidence=Path(evidence).resolve();bundle=Path(bundle).resolve()
    record=json.loads((evidence/'flutter_evidence.json').read_text(encoding='utf8'))
    if record.get('format')!=1 or record.get('origin')!='actual_pinned_sdk_run':raise ValueError('Unknown Flutter evidence format')
    if record.get('fixed_sha256')!=hashlib.sha256((bundle/'intl_kk_fixed.arb').read_bytes()).hexdigest():
        raise ValueError('Frozen Flutter evidence is stale for these fixed bytes')
    files=[]
    for name,digest in record['files'].items():
        if not name.startswith('flutter_bundle/'):raise ValueError('Unexpected Flutter evidence destination')
        source=confined(evidence,name);destination=confined(bundle,name);raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Frozen Flutter evidence file hash differs')
        files.append((destination,raw))
    for destination,raw in files:
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
    proof=cached_flutter(bundle)
    if proof is None:raise ValueError('Frozen Flutter evidence does not prove the current package')
    return proof


def cached_flutter(bundle):
    """Reuse real SDK evidence only when every tested ARB byte hash matches."""
    from dalel.delivery.flutter import VERSION,REVISION,VALID_NAME,without_members,key_mapping,normalize_document
    bundle=Path(bundle).resolve();path=bundle/'flutter_bundle/validation.json'
    if not path.is_file():return None
    result=json.loads(path.read_text(encoding='utf8'))
    if result.get('status')!='passed' or result.get('sdk',{}).get('version')!=VERSION or result['sdk'].get('revision')!=REVISION:return None
    source=ArbDocument.read(bundle/'intl_ru.arb');fixed=ArbDocument.read(bundle/'intl_kk_fixed.arb')
    latin=bundle/'intl_kk_Latn.arb';docs=[source,fixed]+([ArbDocument.read(latin)] if latin.exists() else [])
    uzbek=bundle/'uz/intl_uz_fixed.arb'
    if uzbek.exists():
        uz_doc=ArbDocument.read(uzbek)
        if uz_doc.locale!='uz':return None
        docs.append(uz_doc)
    excluded={key for key in source.strings if not VALID_NAME.fullmatch(key)}
    clean=without_members(source,excluded|{'@'+key for key in excluded})
    expected={'A_source':{'intl_ru.arb':clean},'A':{'intl_ru.arb':clean,'intl_kk.arb':fixed.raw}}
    if uzbek.exists():expected['A']['intl_uz.arb']=uzbek.read_bytes()
    mapping,actions=key_mapping(docs);files={}
    for index,doc in enumerate(docs):
        files[f'intl_{doc.locale}.arb']=normalize_document(doc,mapping,actions,source)
        expected['B'+str(index+1)]=dict(files)
    for name,inputs in expected.items():
        record=result.get('checks',{}).get(name,{})
        if record.get('generation')!='passed' or record.get('input_sha256')!={k:hashlib.sha256(v).hexdigest() for k,v in inputs.items()}:return None
        for key in ('log','generated_file'):
            artifact=Path(record.get(key,''))
            if not artifact.is_absolute():artifact=bundle/artifact
            if not artifact.is_file() or not artifact.resolve().is_relative_to(bundle):return None
    if result.get('api_signatures')!='passed' or result.get('check_a_api_signatures')!='passed' or result.get('runtime_smoke')!='passed':return None
    if result.get('oracle',{}).get('status')!='passed':return None
    return result


def _build(manifest_path,out,*,public_out=None,project=None,verify_flutter=True):
    manifest_path=Path(manifest_path).resolve();config=json.loads(manifest_path.read_text(encoding='utf8'))
    if config.get('format')!=1:raise ValueError('Unsupported delivery manifest')
    def path(name):
        value=config[name]
        if not isinstance(value,str):raise ValueError('Delivery input path must be text')
        return (manifest_path.parent/value).resolve()
    inputs={name:path(name) for name in ('source','target','report','glossary','coverage','verification')}
    source=ArbDocument.read(inputs['source']);target=ArbDocument.read(inputs['target'])
    references=[ArbDocument.read((manifest_path.parent/value).resolve()) for value in config.get('references',[])]
    records=[Finding.model_validate(r).model_dump() for r in json.loads(inputs['report'].read_text(encoding='utf8'))]
    coverage=json.loads(inputs['coverage'].read_text(encoding='utf8'))
    verification=json.loads(inputs['verification'].read_text(encoding='utf8'))
    sha=lambda raw:hashlib.sha256(raw).hexdigest()
    if verification['source_sha256']!=sha(source.raw) or verification['target_sha256']!=sha(target.raw):
        raise ValueError('Verification belongs to different ARB inputs')
    approved={r['key']:r['text'] for r in verification['fixes'] if r.get('verdict')=='strict_win'}
    for row in records:
        suggestion=row.get('suggestion')
        if suggestion is not None and not suggestion.startswith('requires_code_change'):
            if row['severity']=='low' or approved.get(row['key'])!=suggestion:
                raise ValueError('A proposed change lacks a matching strict verification')
    out=Path(out).resolve()
    if any(p==out or p.is_relative_to(out) for p in [manifest_path,*inputs.values(),*[r.path for r in references]]):
        raise ValueError('Delivery output overlaps an input')
    if out.exists() and any(out.iterdir()):
        if not (out/'delivery_status.json').is_file():raise ValueError('Nonempty output is not a managed delivery')
        prior=json.loads((out/'changes.json').read_text(encoding='utf8'))
        if (out/'review_history').exists() or any(r.get('reviewed_by_human') for r in prior):
            raise ValueError('Preserve human-reviewed delivery; use another output directory')
    out.mkdir(parents=True,exist_ok=True)
    diagnostics=json.loads(path('diagnostics').read_text(encoding='utf8')) if config.get('diagnostics') else []
    provenance={'producer':config.get('producer','external'),'report_sha256':sha(inputs['report'].read_bytes()),
        'verification_sha256':sha(inputs['verification'].read_bytes()),'student_agreement_claim':False}
    automatic=None
    if config.get('report_auto'):
        raw_path=path('report_auto')
        if raw_path.is_relative_to(out):raise ValueError('Automatic report input overlaps output')
        automatic=json.loads(raw_path.read_text(encoding='utf8'))
        provenance['automatic_report_sha256']=sha(raw_path.read_bytes())
    replay=package_report(source,target,records,out,coverage=coverage,provenance=provenance,
        references=references,glossary=inputs['glossary'],diagnostics=diagnostics,automatic_records=automatic)
    loaded=load(replay,source,target,references=references,glossary=inputs['glossary']);language=loaded['language']
    merged=language['report']
    # The formal merge may discover an additional suggestion. It is visible
    # in the report but cannot enter the correction set without verification.
    for row in merged:
        if row.get('suggestion') and not row['suggestion'].startswith('requires_code_change') and approved.get(row['key'])!=row['suggestion']:
            raise ValueError('Current formal layer introduced an unverified correction; rebuild verification')
    from dalel.terminology.glossary import rows as glossary_rows
    fixed,changes=apply_ai_changes(source,target,merged,provenance=provenance,verified_fixes=verification['fixes'],
        glossary=glossary_rows(inputs['glossary'],include_candidates=True))
    for locale,raw in [(source.locale,source.raw),(target.locale,target.raw),*[(r.locale,r.raw) for r in references]]:
        (out/f'intl_{locale}.arb').write_bytes(raw)
    (out/f'intl_{target.locale}_fixed.arb').write_bytes(fixed)
    for name,value in [('report',merged),('report_auto',language['report_auto']),('diagnostics',language['diagnostics']),('coverage',coverage),('changes',changes)]:
        write_json(out/(name+'.json'),value)
    for name in ('glossary.csv','glossary_rules.json'):
        original=inputs['glossary'] if name.endswith('.csv') else inputs['glossary'].with_name(name)
        (out/name).write_bytes(original.read_bytes())
    metadata = describe(source, target, merged, language, inputs['glossary'], mode='replay', profile='offline', language_complete=True)
    write_json(out/'coverage.json', metadata)
    (out/'report.html').write_bytes(render_report(merged,metadata,language['diagnostics']).encode('utf8'))
    fixed_doc=ArbDocument(fixed);latin,length=convert_document(fixed_doc)
    (out/'intl_kk_Latn.arb').write_bytes(latin);write_json(out/'latin_length_report.json',length)
    missing=[];self_check={'status':'not_run'}
    if config.get('self_check'):
        self_check=json.loads(path('self_check').read_text(encoding='utf8'))
        if self_check.get('fixed_sha256')!=sha(fixed):raise ValueError('Fixed-file self-check is stale')
    zero_metrics=('missing_keys','placeholder_findings','wrong_language_findings','same_as_source_findings','remaining_high_without_code_change')
    if self_check.get('status')!='passed' or self_check.get('language_complete') is not True or any(self_check.get(k)!=0 for k in zero_metrics):
        missing.append('full_fixed_file_self_check')
    # Failures elsewhere in the file do not block independently safe cards.
    from dalel.terminology.glossary import rows as glossary_rows
    gate,excluded=review_gate(source,target,fixed_doc,changes,self_check,inputs['glossary'],references)
    if gate['passed'] and excluded:
        raise ValueError('Excluded corrections cannot enter the fixed ARB; revert them and rebuild the fixed-file verification')
    standing={'status':self_check.get('status','not_run'),
        **{key:self_check.get(key) for key in zero_metrics},'new_findings':None}
    if self_check.get('language_complete') is True and isinstance(self_check.get('findings_detail'),list):
        from dalel.delivery.comparison import compare,run_metadata
        checked=self_check['findings_detail']
        delta=compare(merged,checked,run_metadata(merged,source,target,references,inputs['glossary']),
            run_metadata(checked,source,fixed_doc,references,inputs['glossary']))
        standing.update(new_findings=delta['summary']['new'],baseline=delta['summary'])
        write_json(out/'fixed_delta.json',delta)
        if standing['new_findings'] and 'full_fixed_file_self_check' not in missing:missing.append('full_fixed_file_self_check')
    write_json(out/'review_exclusions.json',excluded)
    write_json(out/'self_check.json',self_check)
    review=write_screen(source,target,merged,out,gate=gate,
        glossary=glossary_rows(inputs['glossary'],include_candidates=True))
    from dalel.terminology.approval import write_screen as write_glossary_screen
    write_glossary_screen(out/'glossary.csv',out)
    review_path=out/'review.html'
    review_path.write_bytes(review_path.read_bytes().replace(b'<main class="wrap">',
        '<main class="wrap"><p><a href="glossary_review.html">Проверить и утвердить термины глоссария</a></p>'.encode('utf8')))
    for name in ('uz_report','uz_html'):
        if config.get(name):
            destination=out/'uz'/('report.json' if name.endswith('report') else 'report.html')
            destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(path(name).read_bytes())
        else:missing.append(name)
    flutter={'status':'not_run','reason':'Run locqa verify-flutter against this exact package; previous input hashes are not reused.'}
    (out/'flutter_bundle').mkdir(exist_ok=True)
    proven=restore_flutter_evidence(path('flutter_evidence'),out) if config.get('flutter_evidence') else cached_flutter(out)
    if proven is not None:
        flutter=proven
    elif verify_flutter:
        from dalel.delivery.flutter import verify_bundle
        flutter=verify_bundle(out,out/'flutter_bundle/validation.json')
    write_json(out/'flutter_validation.json',flutter)
    if flutter.get('status')!='passed':missing.append('flutter_verification')
    workflow=PACKAGE_ROOT/'assets/locqa-consumer.yml'
    if workflow.is_file():
        destination=out/'.github/workflows/locqa.yml';destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(workflow.read_bytes())
    else:missing.append('consumer_workflow')
    consumer={'status':'not_run'}
    if project is not None:consumer=consumer_bundle(project,out)
    else:missing.append('consumer_installable_bundle')
    stats={'source_strings':len(source.strings),'findings':len(merged),'findings_by_type':dict(Counter(r['issue_type'] for r in merged)),
        'automatic_candidates':len(language['report_auto']),
        'null_suggestions_by_type':dict(Counter(r['issue_type'] for r in merged if r['suggestion'] is None)),
        'confidence_distribution':dict(Counter(str(r['confidence']) for r in merged)),
        'suggestions':sum(r['suggestion'] is not None for r in merged),'changed_strings':len(changes),'human_accepted_strings':0,
        'requires_code_change':len(planned_keys(language['diagnostics']) & {r['key'] for r in merged}),
        'layer_counts':language['layer_counts'],'language_coverage':coverage['status'],'self_check_status':self_check.get('status')}
    write_json(out/'delivery_stats.json',stats);(out/'methodology.md').write_bytes(methodology(stats).encode('utf8'))
    readme=f'''# dalel delivery

Install the GPL-3.0-only locqa package from the accompanying public source: `python -m pip install ./public-source`.
In this private delivery directory run:

```sh
locqa check --source intl_{source.locale}.arb --target intl_{target.locale}.arb --glossary glossary.csv --out report.json
```

The exact-input offline replay reproduces {len(merged)} merged findings with zero API calls. Open report.html for evidence. The fixed file contains {len(changes)} AI-verified changes and zero human acceptances. Read methodology.md and delivery_status.json before integration.

report_auto.json contains {len(language['report_auto'])} original automatic candidates before publication filtering and independent fix review. It is retained for auditing; report.json is the current published finding set. Raw candidates never authorize changes.

The actual fixed-file check reports {standing['new_findings']} new findings relative to the original and {standing['remaining_high_without_code_change']} high findings without a code-change exemption. Missing translations: {standing['missing_keys']}; placeholder defects: {standing['placeholder_findings']}; other-language findings: {standing['wrong_language_findings']}. These are unresolved release conditions, not accepted defects. See self_check.json and fixed_delta.json for exact text and evidence.

Open review.html offline with no API key: {review['cards']} individually qualified cards are available. Use A/R/E and arrow keys, export decisions.json, then run `locqa apply-review decisions.json --bundle .`. Excluded changes and reasons are in review_exclusions.json. Cached language judgments and current deterministic rules gate each card; unresolved defects elsewhere do not lock the queue. AI simulation decisions cannot create a human review.

Run `locqa verify-flutter --bundle . --out flutter_bundle/validation.json` with pinned Flutter 3.47.9 after every accepted edit. Original ARBs are private and remain unchanged.
'''
    (out/'README.md').write_bytes(readme.encode('utf8'))
    status={'status':'ready_for_human_acceptance' if not missing else 'incomplete','unmet_conditions':missing,
        'changed_strings':len(changes),'review_cards':review['cards'],'human_acceptances':0,
        'fixed_sha256':sha(fixed),'report_sha256':sha((out/'report.json').read_bytes()),'consumer_action':consumer,
        'fixed_file_check':standing}
    if public_out:status['public_export']=public_export(project or Path.cwd(),public_out)
    write_json(out/'delivery_status.json',status)
    return status


def _replace_directory(source,destination):
    """One same-volume rename, separated for failure-injection contracts."""
    import os
    os.replace(source,destination)


def _existing_delivery(out):
    if not out.exists() or not any(out.iterdir()):return
    if not (out/'delivery_status.json').is_file():raise ValueError('Nonempty output is not a managed delivery')
    changes=json.loads((out/'changes.json').read_text(encoding='utf8'))
    if (out/'review_history').exists() or any(r.get('reviewed_by_human') for r in changes):
        raise ValueError('Preserve human-reviewed delivery; use another output directory')


def build(manifest_path,out,*,public_out=None,project=None,verify_flutter=True):
    """Prepare complete trees before publication; restore prior trees on failure.

    Windows cannot replace a nonempty directory in one rename. A same-volume
    backup rename and publication rename provide rollback on any failed swap.
    The old tree is never edited. A rollback failure preserves its backup.
    """
    import os
    import shutil
    import tempfile
    manifest_path=Path(manifest_path).resolve();out=Path(out).resolve()
    config=json.loads(manifest_path.read_text(encoding='utf8'))
    names=('source','target','report','report_auto','diagnostics','glossary','coverage','verification','self_check','flutter_evidence','uz_report','uz_html')
    inputs=[manifest_path,*[(manifest_path.parent/config[name]).resolve() for name in names if config.get(name)],
        *[(manifest_path.parent/value).resolve() for value in config.get('references',[])]]
    if any(p==out or p.is_relative_to(out) for p in inputs):raise ValueError('Delivery output overlaps an input')
    _existing_delivery(out)
    public=Path(public_out).resolve() if public_out is not None else None
    if public is not None and (public==out or public.is_relative_to(out) or out.is_relative_to(public)):
        raise ValueError('Public export and delivery outputs overlap')
    containers=[];transactions=[];preserve=False
    def staging(destination):
        destination.parent.mkdir(parents=True,exist_ok=True)
        container=Path(tempfile.mkdtemp(prefix='.'+destination.name+'-locqa-build-',dir=destination.parent)).resolve()
        if container.parent!=destination.parent:raise ValueError('Build staging escapes its output parent')
        containers.append(container)
        return container,container/'next'
    try:
        container,stage=staging(out)
        # Reuse only expensive managed evidence, never arbitrary old report
        # files. Rebuilding also removes stale, superseded output artifacts.
        if (out/'delivery_status.json').is_file():
            stage.mkdir()
            for name in ('delivery_status.json','changes.json'):
                (stage/name).write_bytes((out/name).read_bytes())
            for name in ('flutter_bundle','.github/actions/locqa'):
                old=out/name
                if old.is_dir():
                    if old.is_symlink() or any(p.is_symlink() for p in old.rglob('*')):
                        raise ValueError('Managed delivery evidence contains a symlink')
                    shutil.copytree(old,stage/name)
        public_stage=None
        if public is not None:
            public_container,public_stage=staging(public)
            # Copying first lets the existing export validator detect user
            # edits without mutating their public tree.
            if public.exists():
                if any(p.is_symlink() for p in public.rglob('*')):raise ValueError('Public export contains a symlink')
                shutil.copytree(public,public_stage)
        result=_build(manifest_path,stage,public_out=public_stage,project=project,verify_flutter=verify_flutter)
        # Existing real-SDK runs may have absolute artifact references. Keep
        # references portable when the completed staging tree is renamed.
        def portable(value,key=None):
            if isinstance(value,dict):return {k:portable(v,k) for k,v in value.items()}
            if isinstance(value,list):return [portable(v,key) for v in value]
            if key in ('log','generated_file','screenshot') and isinstance(value,str):
                path=Path(value)
                if path.is_absolute() and path.is_relative_to(stage):return path.relative_to(stage).as_posix()
            return value
        for path in [stage/'flutter_validation.json',*(stage/'flutter_bundle').rglob('*.json')]:
            if any(part in ('.dart_tool','build') for part in path.relative_to(stage).parts):continue
            raw=json.loads(path.read_text(encoding='utf8'));converted=portable(raw)
            if converted!=raw:write_json(path,converted)
        _existing_delivery(out)
        transactions=[{'destination':out,'stage':stage,'backup':container/'previous','old_moved':False,'published':False}]
        if public is not None:
            transactions.append({'destination':public,'stage':public_stage,'backup':public_container/'previous','old_moved':False,'published':False})
        cwd=Path.cwd().resolve();cwd_moved=False
        for item in transactions:
            if cwd.is_relative_to(item['destination']):
                relative=cwd.relative_to(item['destination'])
                if not (item['stage']/relative).is_dir():
                    raise ValueError('Run the package build outside a directory removed by the rebuild')
                # Windows holds the process CWD open. Release that handle
                # during the swap, then return to the same published path.
                os.chdir(item['destination'].parent);cwd_moved=True;break
        try:
            for item in transactions:
                if item['destination'].exists():
                    _replace_directory(item['destination'],item['backup']);item['old_moved']=True
                _replace_directory(item['stage'],item['destination']);item['published']=True
        except BaseException:
            for item in reversed(transactions):
                try:
                    if item['published']:_replace_directory(item['destination'],item['stage'])
                    if item['old_moved']:_replace_directory(item['backup'],item['destination'])
                except OSError:
                    preserve=True
            if preserve:raise RuntimeError('Publication rollback needs recovery; previous trees retained at '+', '.join(str(p) for p in containers))
            raise
        finally:
            if cwd_moved and cwd.is_dir():os.chdir(cwd)
        return result
    finally:
        if not preserve:
            for container in containers:
                if container.exists():
                    if container.is_symlink() or container.parent not in {out.parent,*([public.parent] if public is not None else [])}:
                        raise ValueError('Unsafe staging cleanup path')
                    shutil.rmtree(container)
