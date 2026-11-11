"""Check a consumer's changed ARB pairs with one pinned installed locqa.

Only data is read from the two revisions. No consumer code, hooks, environment
file or configuration beyond literal Flutter ARB paths is executed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import html
from pathlib import Path,PureWindowsPath
from dalel.formats.arb import ArbDocument


def confined(root,value):
    relative=Path(value)
    if relative.is_absolute() or PureWindowsPath(value).is_absolute() or '..' in relative.parts or ':' in value:
        raise ValueError('ARB paths must be relative to the consumer repository')
    path=(root/relative).resolve()
    if not path.is_relative_to(root):raise ValueError('ARB path escapes the consumer repository')
    return path


def config_paths(root):
    config={'arb-dir':'lib/l10n','template-arb-file':'app_en.arb'}
    path=root/'l10n.yaml'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            match=re.match(r'^(arb-dir|template-arb-file):\s*(.*?)\s*$',line)
            if not match:continue
            key,value=match.groups()
            if value.startswith('"'):value=json.loads(value)
            elif value.startswith("'") and value.endswith("'"):value=value[1:-1].replace("''", "'")
            else:value=re.split(r'\s+#',value,maxsplit=1)[0]
            if not value or value.startswith(('*','&','{','[','|','>')):
                raise ValueError('Use literal l10n paths or explicit --source/--target inputs')
            config[key]=value
    directory=confined(root,config['arb-dir']);source=confined(root,(Path(config['arb-dir'])/config['template-arb-file']).as_posix())
    return directory,source


def pairs(head,base,source=None,targets=()):
    if bool(source)!=bool(targets):raise ValueError('Explicit source and target inputs are required together')
    if source:
        for target in targets:yield source,target
        return
    found=set()
    for root in (base,head):
        directory,template=config_paths(root)
        if not template.is_file():continue
        source_locale=ArbDocument.read(template).locale
        for target in directory.glob('*.arb'):
            confined(root,target.relative_to(root).as_posix())
            if target==template or target.stem.endswith('_fixed'):continue
            if ArbDocument.read(target).locale==source_locale:continue
            found.add((template.relative_to(root).as_posix(),target.relative_to(root).as_posix()))
    if not found:raise ValueError('No source/target ARB pairs found; provide inputs or l10n.yaml')
    yield from sorted(found)


def run(head,base,out,source=None,targets=()):
    head=Path(head).resolve();base=Path(base).resolve();out=Path(out).resolve();results=[];unchanged=0
    env=dict(os.environ,PYTHONUTF8='1')
    # Child CLI is explicitly deterministic; credentials are not inherited.
    env={k:v for k,v in env.items() if not k.startswith('LOCQA_')}
    for index,(src,tgt) in enumerate(pairs(head,base,source,targets)):
        files={(label,role):confined(root,value) for label,root in [('base',base),('head',head)] for role,value in [('source',src),('target',tgt)]}
        raw=lambda path:path.read_bytes() if path.is_file() else None
        if all(raw(files['base',role])==raw(files['head',role]) for role in ('source','target')):
            unchanged+=1;continue
        folder=out/str(index);folder.mkdir(parents=True,exist_ok=True)
        # Stage only literal data into controlled filenames. Deleted/new files
        # become empty locale documents using the other revision's metadata.
        for label in ('base','head'):
            for role in ('source','target'):
                path=files[label,role];content=raw(path)
                if content is None:
                    other=files['head' if label=='base' else 'base',role]
                    if not other.is_file():raise ValueError('Configured ARB is absent in both revisions')
                    content=(json.dumps({'@@locale':ArbDocument.read(other).locale})+'\n').encode('utf8')
                destination=folder/(label+'_'+role+'.arb');destination.write_bytes(content);files[label,role]=destination
        for label in ('base','head'):
            report=folder/(label+'.json')
            command=[sys.executable,'-X','utf8','-m','dalel.cli','check','--profile','deterministic',
                '--source',str(files[label,'source']),'--target',str(files[label,'target']),'--out',str(report)]
            if label=='head':command+=['--baseline',str(folder/'base.json'),'--fail-on','high']
            completed=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            report.with_suffix('.log').write_bytes(completed.stdout.replace(b'\r\n',b'\n'))
            if completed.returncode not in (0,1):raise ValueError('ARB comparison failed; inspect local logs')
        delta=json.loads((folder/'head.delta.json').read_text(encoding='utf8'))
        reports={label:json.loads((folder/(label+'.json')).read_text(encoding='utf8')) for label in ('base','head')}
        details=[]
        for change in delta['changes']:
            if change['status'] not in ('new','escalated','resolved','changed_high_requires_review'):continue
            record=reports['head'][change['after_index']] if change['after_index'] is not None else reports['base'][change['before_index']]
            details.append({'status':change['status'],'key':record['key'],'severity':record['severity'],
                'issue_type':record['issue_type'],'evidence':record['evidence']})
        results.append({'source':src,'target':tgt,'summary':delta['summary'],'blocking':completed.returncode==1,'details':details})
    summary={'checked_pairs':len(results),'unchanged_pairs':unchanged,'blocking_pairs':sum(r['blocking'] for r in results),
        'rules':'one pinned tool on both data revisions','api_calls':0,'results':results}
    out.mkdir(parents=True,exist_ok=True);(out/'summary.json').write_bytes((json.dumps(summary,indent=2)+'\n').encode('utf8'))
    markdown='## Localization QA\n\n'+f"Changed pairs: {len(results)}. Unchanged pairs: {unchanged}. Pairs with new high findings: {summary['blocking_pairs']}. API calls: 0.\n\n"
    markdown+='| Pair | Blocking | Counts |\n|---|---|---|\n'
    for row in results:
        name=(row['source']+' / '+row['target']).replace('|','&#124;').replace('\n',' ')
        markdown+='| '+name+' | '+str(row['blocking'])+' | '+json.dumps(row['summary'],sort_keys=True)+' |\n'
    markdown+='\n| Change | Severity | Key | Evidence (quoted fragment) |\n|---|---|---|---|\n'
    def cell(value):return html.escape(str(value)).replace('|','&#124;').replace('`','&#96;').replace('\r',' ').replace('\n',' ')
    for pair in results:
        for detail in pair['details']:
            markdown+='| '+' | '.join(cell(detail[k]) for k in ('status','severity','key','evidence'))+' |\n'
    (out/'summary.md').write_bytes(markdown.encode('utf8'))
    return summary,markdown


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--head-root',type=Path,default=Path.cwd())
    parser.add_argument('--base-root',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--source');parser.add_argument('--target',action='append',default=[])
    args=parser.parse_args()
    try:summary,markdown=run(args.head_root,args.base_root,args.out,args.source,args.target)
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);raise SystemExit(2)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a',encoding='utf8',newline='\n') as stream:stream.write(markdown)
    print(json.dumps(summary));raise SystemExit(bool(summary['blocking_pairs']))


if __name__=='__main__':main()
