from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from dalel.formats.arb import ArbDocument
from dalel.engine.rules import check as formal_check

app = typer.Typer(name='dalel', no_args_is_help=True,
                  help='Проверка локализации ARB: формат, язык и проверенные исправления.')


def write_json(path: Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


@app.callback()
def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8')


@app.command()
def check(source: Path=typer.Option(...,exists=True,dir_okay=False),
          target: Path=typer.Option(...,exists=True,dir_okay=False),
          out: Path=typer.Option(Path('out/report.json')),
          glossary: Optional[Path]=typer.Option(None,exists=True,dir_okay=False),
          ref: Optional[list[Path]]=typer.Option(None),
          use_escaping: bool=typer.Option(False),
          baseline: Optional[Path]=typer.Option(None,exists=True,dir_okay=False),
          fail_on: Optional[str]=typer.Option(None),
          profile: str=typer.Option('offline'),
          strict_coverage: bool=typer.Option(False),
          effort: str=typer.Option('medium'),
          workers: int=typer.Option(2,min=1,max=4),
          batch_size: int=typer.Option(20,min=1,max=50),
          max_output_tokens: int=typer.Option(24000,min=1000),
          budget_group: Optional[str]=typer.Option(None),
          budget_cap: Optional[float]=typer.Option(None,min=0.001),
          context: Optional[Path]=typer.Option(None,exists=True,dir_okay=False),
          protocol_out: Optional[Path]=typer.Option(None),
          corrected: Optional[Path]=typer.Option(None),
          student_cache: Path=typer.Option(Path('data/private/locqa/analysis'), '--cache-dir', '--student-cache'),
          replay_manifest: Optional[Path]=typer.Option(None,exists=True,dir_okay=False),
          review_out: Optional[Path]=typer.Option(None)):
    """Check ARBs offline by default; auto/full may call the configured model."""
    started = time.perf_counter();replay=None
    from dalel.delivery.comparison import LEVEL,run_metadata,load_baseline,compare
    try:
        if profile not in ('offline','deterministic','auto','full'):
            raise ValueError('--profile must be offline, deterministic, auto or full')
        if baseline and profile not in ('offline','deterministic'):
            raise ValueError('PR baselines require --profile deterministic')
        if baseline:profile='deterministic'
        if replay_manifest and profile=='deterministic':
            raise ValueError('Use --profile auto or full with a language replay manifest')
        if context and replay_manifest:raise ValueError('External context requires its own Student run; replay context is not bound')
        if effort not in ('low','medium','high','xhigh'):
            raise ValueError('Unknown reasoning effort')
        if fail_on is not None and fail_on not in LEVEL:
            raise ValueError('--fail-on must be high, medium or low')
        src,tgt = ArbDocument.read(source),ArbDocument.read(target)
        game_context=json.loads(context.read_text(encoding='utf8')) if context else None
        from dalel.formats.references import discover as discover_references
        references = [ArbDocument.read(path) for path in ref] if ref else discover_references(src,tgt)
        if profile!='deterministic' and context is None:
            from dalel.delivery.replay import discover
            replay=discover(src,tgt,references=references,glossary=glossary,escaping=use_escaping,explicit=replay_manifest)
            if replay:
                references=replay['references'];glossary=glossary or replay['glossary']
        if replay is None and out.name == 'report.json' and (out.parent/'delivery_status.json').is_file():
            raise ValueError('Packaged report requires a matching replay; the existing report is preserved. Use a new output path for an independent check.')
        formal_processed=[]
        findings,diagnostics = formal_check(src,tgt,references,glossary,use_escaping,processed_keys=formal_processed)
    except (ValueError,OSError) as exc:
        typer.echo(f'Ошибка входных данных: {exc}',err=True)
        raise typer.Exit(2) from None
    from dalel.terminology.glossary import sidecar
    glossary_rules=sidecar(glossary)
    input_paths = [source,target,*[r.path for r in references if r.path],*([glossary] if glossary else []),
                   *([glossary_rules] if glossary_rules and glossary_rules.is_file() else []),
                   *([baseline,baseline.with_suffix('.run.json')] if baseline else []),
                   *([context] if context else []),*(replay['protected_paths'] if replay else [])]
    inputs = {path.resolve() for path in input_paths}
    html_out=out.with_suffix('.html')
    paths = [out,out.with_name('report_auto.json'),out.with_name('diagnostics.json'),out.with_name('coverage.json'),html_out,
             out.with_suffix('.run.json'),out.with_suffix('.delta.json'),out.with_name('language.json'),
             *([protocol_out] if protocol_out else []),*([corrected,corrected.with_suffix('.changes.json')] if corrected else [])]
    if len({p.resolve() for p in paths})!=len(paths):
        typer.echo('Output paths must be distinct.',err=True);raise typer.Exit(2)
    if out==html_out:
        typer.echo('JSON и HTML отчёты должны иметь разные пути.',err=True)
        raise typer.Exit(2)
    if out.name in ('diagnostics.json','coverage.json','language.json','report_auto.json'):
        typer.echo('Имя отчёта занято служебным файлом.',err=True)
        raise typer.Exit(2)
    if any(path.resolve() in inputs or path.exists() and any(original.exists() and path.samefile(original) for original in input_paths) for path in paths):
        typer.echo('Путь отчёта совпадает с входным файлом.',err=True)
        raise typer.Exit(2)
    records = [f.model_dump() for f in findings];auto_records=list(records)
    language=None;language_error=None;language_complete=False;mode='deterministic'
    if profile!='deterministic':
        from dalel.inference.client import Config,Provider
        from dalel.engine.pipeline import analyze,AnalysisSettings
        from dalel.engine.rules import glossary_rows
        try:
            if replay:
                language=replay['language']
            elif profile in ('auto','full'):
                config=Config.from_env()
                configured=bool(config.base_url and config.model and config.api_key and config.budget>0)
                if configured:
                    limits={'budget_group':budget_group,'group_budget':budget_cap} if budget_group is not None or budget_cap is not None else {}
                    provider=Provider(config,**limits)
                    terms=glossary_rows(glossary)
                    language=analyze(src,tgt,provider,references=references,glossary=terms,game_context=game_context,
                        settings=AnalysisSettings(effort=effort,workers=workers,batch_size=batch_size,
                                                 max_tokens=max_output_tokens),cache_dir=student_cache,escaping=use_escaping)
            if language:
                language_complete=language['coverage']['status']=='complete'
                if language.get('origin') == 'reviewed_report':
                    language_complete = language['display_coverage'].get('language_complete') is True
                mode='replay' if language['replay'] else 'full' if language_complete else 'partial'
                if not language_complete:
                    language_error='Language pass did not complete; inspect language.json'
                if language.get('full_merged_report'):
                    records=language['report'];auto_records=language['report_auto'];diagnostics=language['diagnostics']
                else:
                    present={(r['key'],r['issue_type']) for r in records}
                    records.extend(r for r in language['report'] if (r['key'],r['issue_type']) not in present)
                    auto_records.extend(r for r in language['report_auto'] if (r['key'],r['issue_type']) not in present)
                    diagnostics.extend(language['diagnostics'])
                records.sort(key=lambda r:({'high':0,'medium':1,'low':2}[r['severity']],r['key'],r['issue_type']))
                auto_records.sort(key=lambda r:({'high':0,'medium':1,'low':2}[r['severity']],r['key'],r['issue_type']))
            else:
                language_error='No matching offline replay; deterministic coverage only' if profile=='offline' else 'Provider is not fully configured; deterministic coverage only'
        except ValueError as exc:
            typer.echo(f'Ошибка конфигурации языкового прохода: {exc}',err=True)
            raise typer.Exit(2) from None
        except Exception as exc:
            mode='partial';language_error='Language provider failed: '+type(exc).__name__
    try:
        metadata=run_metadata(records,src,tgt,references,glossary,use_escaping)
        if profile in ('auto','full') or language:
            metadata['compatibility'].update(mode=mode,language_profile=profile,student_effort=effort)
            metadata['language']={'complete':language_complete,'error':language_error,
                'identity':language['identity'] if language else None}
        delta=None
        if baseline:
            prior,prior_metadata=load_baseline(baseline,metadata)
            delta=compare(prior,records,prior_metadata,metadata,fail_on)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        typer.echo(f'Ошибка baseline: {exc}',err=True);raise typer.Exit(2) from None
    if language and language.get('origin') == 'reviewed_report':
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(language['reviewed_report_json'].encode('utf8'))
    else:
        write_json(out,records)
    write_json(out.with_suffix('.run.json'),metadata)
    if delta is not None:
        write_json(out.with_suffix('.delta.json'),delta)
    else:
        write_json(out.with_suffix('.delta.json'),{'status':'not_applied','report_filtered':False})
    write_json(out.with_name('report_auto.json'),auto_records)
    write_json(out.with_name('diagnostics.json'),diagnostics)
    write_json(out.with_name('language.json'),language or {'coverage':'not_run','reason':language_error or 'deterministic profile'})
    if protocol_out:
        decisions=(language or {}).get('decisions',{})
        reported={r['key'] for r in records}
        abstentions=[{'key':key,'reason':value.get('negative_rationale') or 'Unresolved language context'}
                     for key,value in decisions.items() if value.get('needs_review') and key not in reported]
        processed=formal_processed if profile=='deterministic' else list(decisions)
        write_json(protocol_out,{'findings':records,'processed_keys':processed,'abstentions':abstentions})
    if corrected:
        from dalel.engine.corrections import apply_ai_changes
        from dalel.terminology.glossary import rows as glossary_rows
        fixes={r['key']:r for r in (language or {}).get('fixes',[])}
        eligible=[dict(r,suggestion=fixes[r['key']]['text']) for r in records if r['key'] in fixes]
        raw,changes=apply_ai_changes(src,tgt,eligible,provenance={'producer':'student','review_stage':'ai_only'},
                                    verified_fixes=list(fixes.values()),escaping=use_escaping,annotate_metadata=bool((language or {}).get('full_merged_report')),
                                    glossary=glossary_rows(glossary))
        if language and language.get('origin') == 'reviewed_report':
            raw, changes = language['reviewed_fixed'].encode('utf8'), language['reviewed_changes']
        corrected.parent.mkdir(parents=True,exist_ok=True);corrected.write_bytes(raw)
        write_json(corrected.with_suffix('.changes.json'),changes)
    from dalel.engine.coverage import describe
    coverage = describe(src, tgt, records, language, glossary, mode=mode, profile=profile,
        language_complete=language_complete, language_error=language_error, strict=strict_coverage)
    from dalel.presentation.report import render_report
    if language and language.get('origin') == 'reviewed_report':
        coverage = dict(language['display_coverage'])
        html_out.write_bytes(language['reviewed_report_html'].encode('utf8'))
    else:
        html_out.write_bytes(render_report(records,coverage,diagnostics).encode('utf8'))
    coverage['seconds']=0 if mode == 'replay' else round(time.perf_counter()-started,4)
    if delta is not None:
        coverage['baseline']=delta['summary']
    write_json(out.with_name('coverage.json'),coverage)
    typer.echo(json.dumps(coverage,ensure_ascii=False))
    if mode=='partial' or (profile=='full' or strict_coverage) and not language_complete:
        raise typer.Exit(3)
    if review_out:
        if not language_complete:
            typer.echo('Expert preparation needs a complete saved language analysis.', err=True)
            raise typer.Exit(3)
        from dalel.delivery.expert import from_result
        try:
            prepared = from_result(src, tgt, language, review_out, references=references, glossary=glossary, escaping=use_escaping)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from None
        typer.echo(json.dumps(prepared, ensure_ascii=False))
    blocking=delta['blocking'] if delta is not None else bool(fail_on and any(LEVEL[r['severity']]>=LEVEL[fail_on] for r in records))
    if blocking:
        raise typer.Exit(1)


@app.command('pack-replay')
def pack_replay(source: Path=typer.Option(...,exists=True,dir_okay=False),
                target: Path=typer.Option(...,exists=True,dir_okay=False),
                language_result: Path=typer.Option(...,exists=True,dir_okay=False),
                out: Path=typer.Option(...),ref: Optional[list[Path]]=typer.Option(None),
                glossary: Optional[Path]=typer.Option(None,exists=True,dir_okay=False),
                use_escaping: bool=typer.Option(False)):
    """Package a complete private Student run for replay without credentials."""
    from dalel.delivery.replay import package
    try:
        result=json.loads(language_result.read_text(encoding='utf8'))
        manifest=package(ArbDocument.read(source),ArbDocument.read(target),result,out,
            references=[ArbDocument.read(p) for p in ref or []],glossary=glossary,escaping=use_escaping)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        typer.echo(f'Ошибка replay-пакета: {exc}',err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps({'manifest':str(manifest),'findings':len(result['report']),'human_acceptances':0},ensure_ascii=False))


@app.command('review')
def review(source: Path=typer.Option(...,exists=True,dir_okay=False),
           target: Path=typer.Option(...,exists=True,dir_okay=False),
           report: Path=typer.Option(...,exists=True,dir_okay=False),
           reviewer: str=typer.Option(...),project: Path=typer.Option(Path('.locqa/review.sqlite3')),
           out: Path=typer.Option(Path('out/reviewed')),use_escaping: bool=typer.Option(False),
           glossary: Optional[Path]=typer.Option(None,exists=True,dir_okay=False)):
    """Start an interactive, named human accept pass. No automatic approval."""
    from dalel.review.terminal import run_review
    from dalel.delivery.replay import terms
    try:
        src,tgt=ArbDocument.read(source),ArbDocument.read(target)
        result=run_review(src,tgt,
            json.loads(report.read_text(encoding='utf8')),project,reviewer,out,
            dependencies={'escaping':use_escaping,'glossary':terms(glossary,src.locale,tgt.locale)},
            protected_paths=[source,target,report,*([glossary] if glossary else [])])
    except (ValueError,OSError,KeyError,TypeError) as exc:
        typer.echo(f'Ошибка ручной проверки: {exc}',err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps(result,ensure_ascii=False))


@app.command('render-html')
def render_html(report: Path=typer.Option(...,exists=True,dir_okay=False),out: Path=typer.Option(...)):
    """Render an existing JSON report as an offline Russian HTML file."""
    from dalel.presentation.report import render_report
    from dalel.contracts import ReviewedFinding
    try:
        if out.resolve()==report.resolve() or out.exists() and out.samefile(report):
            raise ValueError('HTML output overlaps the input report')
        records=[ReviewedFinding.model_validate(r).model_dump() for r in json.loads(report.read_text(encoding='utf8'))]
        content=render_report(records)
        out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(content.encode('utf8'))
    except (ValueError,OSError,TypeError) as exc:
        typer.echo(f'Ошибка HTML-отчёта: {exc}',err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps({'findings':len(records),'html':str(out),'offline':True},ensure_ascii=False))


@app.command('verify-flutter')
def verify_flutter(bundle: Path=typer.Option(...,exists=True,file_okay=False),
                   out: Path=typer.Option(Path('out/flutter/flutter_validation.json'))):
    """Run real pinned Flutter checks A/B, API comparisons and E0-E9."""
    from dalel.delivery.flutter import verify_bundle
    try:
        result = verify_bundle(bundle,out)
    except (ValueError,OSError) as exc:
        typer.echo(f'Flutter verification input error: {exc}',err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps({'status':result['status'],'evidence':str(out)},ensure_ascii=False))
    if result['status']!='passed':
        raise typer.Exit(3)


@app.command('package')
def package_delivery(manifest: Path=typer.Option(...,exists=True,dir_okay=False),
                     out: Path=typer.Option(Path('submission')),
                     public_out: Optional[Path]=typer.Option(None),
                     project: Optional[Path]=typer.Option(None),
                     verify_flutter: bool=typer.Option(True)):
    """Build canonical private deliverables from a verified snapshot manifest."""
    from dalel.delivery.package import build
    try:
        result=build(manifest,out,public_out=public_out,project=project or Path.cwd(),verify_flutter=verify_flutter)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        typer.echo(f'Ошибка сборки сдачи: {exc}',err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps(result,ensure_ascii=False))
    if result['status']=='incomplete':raise typer.Exit(3)


@app.command('review-html')
def review_html(source: Path=typer.Option(...,exists=True,dir_okay=False),
                target: Path=typer.Option(...,exists=True,dir_okay=False),
                report: Path=typer.Option(...,exists=True,dir_okay=False),
                out: Path=typer.Option(...),simulation: bool=typer.Option(False)):
    """Build a self-contained offline A/R/E/arrow-key acceptance screen."""
    from dalel.review.screen import write_screen
    try:
        protected={p.resolve() for p in (source,target,report)}
        if any((out/name).resolve() in protected for name in ('review.html','review_manifest.json')):
            raise ValueError('Review screen output overlaps an input')
        result=write_screen(ArbDocument.read(source),ArbDocument.read(target),
            json.loads(report.read_text(encoding='utf8')),out,simulation=simulation)
    except (ValueError,OSError,TypeError,KeyError) as exc:
        typer.echo(f'Ошибка экрана проверки: {exc}',err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps(result,ensure_ascii=False))


@app.command('apply-review')
def apply_review(decisions: Path=typer.Argument(...,exists=True,dir_okay=False),
                 bundle: Path=typer.Option(Path('.'),exists=True,file_okay=False),
                 out: Optional[Path]=typer.Option(None)):
    """Apply a real named review exported by review.html; simulation is rejected."""
    from dalel.review.importer import import_decisions
    try:
        summary = import_decisions(bundle, decisions, out)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        typer.echo(f'Ошибка применения решений: {exc}', err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps(summary, ensure_ascii=False))


@app.command('apply-glossary-review')
def apply_glossary_review(decisions: Path=typer.Argument(...,exists=True,dir_okay=False),
                          bundle: Path=typer.Option(Path('.'),exists=True,file_okay=False)):
    """Import named human glossary decisions; message acceptance is separate."""
    from dalel.terminology.approval import apply
    from dalel.review.screen import sha
    try:
        glossary=bundle/'glossary.csv';rules=bundle/'glossary_rules.json'
        if decisions.resolve() in (glossary.resolve(),rules.resolve()):raise ValueError('Decision input overlaps glossary output')
        payload=json.loads((bundle/'glossary_review_manifest.json').read_text(encoding='utf8'))
        content=json.loads(decisions.read_text(encoding='utf8'))
        result=apply(payload,content,glossary)
        history=bundle/'glossary_review_history';history.mkdir(exist_ok=True)
        audit=history/(sha(decisions.read_bytes())+'.json')
        if not audit.exists():audit.write_bytes(decisions.read_bytes())
        glossary.write_bytes(result['csv']);write_json(rules,result['rules'])
        write_json(bundle/'glossary_review_summary.json',result['summary'])
        if (bundle/'intl_kk_fixed.arb').is_file() and (bundle/'changes.json').is_file():
            from dalel.delivery.package import refresh_after_review
            changes=json.loads((bundle/'changes.json').read_text(encoding='utf8'))
            refresh_after_review(bundle,dict(result['summary'],changed_strings=len(changes),
                human_accepted_strings=sum(r.get('reviewed_by_human') is True for r in changes)), bind_review=False)
    except (ValueError,OSError,TypeError,KeyError) as exc:
        typer.echo(f'Ошибка утверждения глоссария: {exc}',err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps(result['summary'],ensure_ascii=False))


@app.command()
def transliterate(source: Path=typer.Option(...,exists=True,dir_okay=False),
                  out: Path=typer.Option(...),profile: str=typer.Option('kk-latn-2018-decree637'),
                  use_escaping: bool=typer.Option(False),preserve: Optional[list[str]]=typer.Option(None)):
    """Convert ICU text nodes using the archived 2018 Latin profile."""
    from dalel.languages.latin import convert_document,load_profile
    sidecar=out.with_name('latin_length_report.json')
    try:
        if out.resolve()==sidecar.resolve() or any(path.resolve()==source.resolve() or path.exists() and path.samefile(source) for path in (out,sidecar)):
            raise ValueError('Latin outputs must be distinct from every input')
        raw,report=convert_document(ArbDocument.read(source),load_profile(profile),escaping=use_escaping,preserve=preserve or ())
        out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(raw)
        write_json(sidecar,report)
    except (ValueError,OSError) as exc:
        typer.echo(f'Latin conversion error: {exc}',err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False))
    if report['unresolved_strings']:
        raise typer.Exit(3)


@app.command('open-workspace')
def open_saved_workspace(bundle: Path=typer.Option(...,exists=True,file_okay=False),
                         data_dir: Path=typer.Option(Path('data/private/workspaces'))):
    """Open a compatible saved analysis in the expert workspace; no API calls."""
    from dalel.workspace.service import WorkspaceService
    try:
        state = WorkspaceService(data_dir).open_bundle(bundle)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps({'workspace': state['id'], 'entries': len(state['order']), 'api_calls': 0}))


@app.command('import-analysis')
def import_saved_analysis(manifest: Path=typer.Option(...,exists=True,dir_okay=False),
                          out: Path=typer.Option(...)):
    """Migrate a checked nonhuman replay, recomputing current rule decisions."""
    from dalel.delivery.analysis_import import import_analysis
    try:
        result = import_analysis(manifest, out)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps(result, ensure_ascii=False))


@app.command('serve')
def serve_workspace(port: int=typer.Option(4173,min=0,max=65535),
                    data_dir: Path=typer.Option(Path('data/private/workspaces')),
                    no_browser: bool=typer.Option(False),simulation: bool=typer.Option(False)):
    """Open the connected local workspace. Paid model calls remain disabled."""
    from dalel.workspace.server import serve
    serve(directory=data_dir,port=port,open_browser=not no_browser,simulation=simulation)


@app.command('prepare-review')
def prepare_review(bundle: Path=typer.Option(...,exists=True,file_okay=False), out: Path=typer.Option(...)):
    """Prepare a separate private expert queue with unreviewed model drafts."""
    from dalel.delivery.expert import prepare
    try:
        result = prepare(bundle, out)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    typer.echo(json.dumps(result, ensure_ascii=False))




@app.command('prepare-approval')
def prepare_approval(data_dir: Path=typer.Option(...,exists=True,file_okay=False),
                     out: Path=typer.Option(...),
                     editorial: Optional[Path]=typer.Option(None,exists=True,dir_okay=False)):
    """Create a numbered reading packet bound to exact versions and context."""
    from dalel.workspace.approval import snapshot, write
    from dalel.workspace.service import WorkspaceService
    try:
        state=WorkspaceService(data_dir).current()
        if state is None: raise ValueError('No active workspace')
        packet=snapshot(state,json.loads(editorial.read_text()) if editorial else {})
        write(packet,out)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        typer.echo(str(exc),err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps(dict(sha256=packet['sha256'],cards=len(packet['rows']),locale=packet['target_locale'])))


@app.command('import-approval')
def import_approval(data_dir: Path=typer.Option(...,exists=True,file_okay=False),
                    snapshot: Path=typer.Option(...,exists=True,dir_okay=False),
                    response: Path=typer.Option(...,exists=True,dir_okay=False)):
    """Import explicit human decisions for the displayed immutable snapshot."""
    from dalel.workspace.approval import apply_response
    from dalel.workspace.service import WorkspaceService
    try:
        state=apply_response(WorkspaceService(data_dir),json.loads(snapshot.read_text()),json.loads(response.read_text()))
    except (ValueError,OSError,KeyError,TypeError) as exc:
        typer.echo(str(exc),err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps(dict(workspace=state['id'],version=state['backend']['version'])))


if __name__ == '__main__':
    app()
