"""Numbered, immutable reading packets and explicit human response import."""
import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path

from dalel.review.store import digest
from dalel.workspace.decisions import manifest
from dalel.workspace.service import WorkspaceService


def context(state, entry):
    return {name: entry.get(name) for name in ('section', 'description', 'refs', 'sourceMeta')} | {
        'neighbors': [{k: state['entries'][key].get(k) for k in ('key', 'source', 'workingText')}
                      for key in entry.get('neighbors', []) if key in state['entries']]}


def snapshot(state, editorial=None):
    editorial = editorial or {}
    rows = []
    for key in sorted(state['entries']):
        entry = state['entries'][key]
        note = editorial.get(key, {})
        if entry['workingText'] == entry['originalTarget'] and not note:
            continue
        ctx = context(state, entry)
        rows.append(dict(number=len(rows)+1, key=key, source=entry['source'], before=entry['originalTarget'],
            proposal=entry['workingText'], revision=entry['currentRevisionId'], context=ctx,
            context_sha256=digest(ctx), editorial=note,
            reason=note.get('reason') or 'Сохранённое модельное предложение; в текущем проходе содержательно не просмотрено.',
            status=note.get('status', 'not_reviewed'), uncertainty=note.get('uncertainty', ''),
            changed_this_pass=note.get('prior_proposal', entry['workingText']) != entry['workingText'],
            reviewed_by_human=bool(WorkspaceService.approval(state, key))))
    content=dict(format='dalel-approval-snapshot/1', source_locale=state['sourceLocale'],
                 target_locale=state['targetLocale'], manifest=manifest(state), rows=rows)
    return dict(content, sha256=digest(content))


def write(packet, destination):
    destination=Path(destination); destination.mkdir(parents=True, exist_ok=True)
    (destination/'approval.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2)+'\n')
    esc=lambda v: html.escape('∅' if v is None else str(v))
    cards=[]
    for r in packet['rows']:
        cards.append(f'<article data-section="{esc(r["context"]["section"])}" data-status="{esc(r["status"])}" data-doubt="{str(bool(r["uncertainty"])).lower()}" data-new="{str(r["changed_this_pass"]).lower()}" data-human="{str(r["reviewed_by_human"]).lower()}"><h2>№ {r["number"]} · {esc(r["key"])}</h2><small>{esc(r["status"])} · {esc(r["context"]["section"])} · {"Изменение отменено: исходный текст сохранён" if r["proposal"] == r["before"] else "Утверждено человеком: точная версия" if r["reviewed_by_human"] else "Ожидает утверждения"}</small>'+''.join(f'<h3>{label}</h3><pre>{esc(r[field])}</pre>' for label,field in [('Русский исходник','source'),('Было','before'),('Предлагается','proposal'),('Причина','reason'),('Сомнения','uncertainty')])+f'<details><summary>Контекст и прежнее предложение</summary><pre>{esc(json.dumps(dict(context=r["context"],editorial=r["editorial"]),ensure_ascii=False,indent=2))}</pre></details></article>')
    selectors=''.join(f'<label>{label} <select data-field="{field}"><option value="">Все</option>'+''.join(f'<option>{esc(v)}</option>' for v in values)+'</select></label> ' for label,field,values in [('Раздел','section',sorted({r['context']['section'] or '' for r in packet['rows']})),('Решение','status',sorted({r['status'] for r in packet['rows']})),('Есть сомнения','doubt',['true','false']),('Правка сейчас','new',['true','false']),('Утверждено человеком','human',['true','false'])])
    page='<!doctype html><html lang="ru"><meta charset="utf-8"><title>Полный пакет утверждения</title><style>body{font:16px system-ui;max-width:1050px;margin:32px auto;padding:16px;background:#f7f7f4;color:#18221e}article{background:white;border:1px solid #ccd5ce;padding:24px;margin:18px 0}pre{white-space:pre-wrap;font:inherit;overflow-wrap:anywhere}h3{font-size:14px;color:#4a6054;margin-bottom:4px}select{max-width:220px}nav{position:sticky;top:0;background:#f7f7f4;padding:12px}small{color:#526359}</style><h1>Переводы для чтения и утверждения</h1><p>Модельная редактура не является человеческим утверждением. Все строки доступны ниже; фильтры только меняют видимость.</p><p>Снимок SHA-256: <code>'+packet['sha256']+'</code></p><p>Для утверждения укажите хеш снимка и номера либо явно подтвердите весь этот снимок. Неоднозначный ответ не импортируется.</p><nav>'+selectors+'<p id="count"></p></nav>'+''.join(cards)+'<script>const filters=[...document.querySelectorAll("select")];function apply(){let n=0;document.querySelectorAll("article").forEach(a=>{a.hidden=filters.some(f=>f.value&&a.dataset[f.dataset.field]!==f.value);if(!a.hidden)n++});document.querySelector("#count").textContent="Показано: "+n}filters.forEach(f=>f.onchange=apply);apply()</script></html>'
    (destination/'approval.html').write_text(page)


def apply_response(service, packet, response):
    content={k:v for k,v in packet.items() if k!='sha256'}
    if digest(content)!=packet.get('sha256') or response.get('snapshot_sha256')!=packet['sha256']:
        raise ValueError('Snapshot hash mismatch')
    state=service.get(packet['manifest']['workspace'])
    if manifest(state)!=packet['manifest']:
        raise ValueError('Stale snapshot: text revisions or glossary changed')
    for row in packet['rows']:
        if digest(context(state,state['entries'][row['key']]))!=row['context_sha256']:
            raise ValueError('Stale snapshot: context changed')
    if response.get('attest_human') is not True or not isinstance(response.get('reviewer'),str) or not response['reviewer'].strip():
        raise ValueError('Explicit human attestation and attribution required')
    selection=response.get('approve')
    if selection=='all': selection=[r['number'] for r in packet['rows'] if r['proposal']!=r['before']]
    if not isinstance(selection,list) or not selection or any(type(n) is not int for n in selection) or len(set(selection))!=len(selection):
        raise ValueError('Specify all or unique integer numbers; ambiguous answers are not imported')
    indexed={r['number']:r for r in packet['rows']}
    if any(n not in indexed for n in selection): raise ValueError('Unknown approval number')
    now=datetime.now(timezone.utc).isoformat()
    entries=[dict(key=indexed[n]['key'],text=indexed[n]['proposal'],revisionId=indexed[n]['revision'],action='approve',reviewer=response['reviewer'].strip(),at=now,nativeSelfDeclared=response.get('native_self_declared') is True) for n in selection]
    return service.import_review(state['id'],state['backend']['version'],dict(format='dalel-workspace-review/1',mode='human',attest_human=True,manifest=packet['manifest'],entries=entries))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,required=True)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--editorial',type=Path)
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--response',type=Path)
    args=parser.parse_args();service=WorkspaceService(args.data_dir)
    if args.response:
        if not args.snapshot: parser.error('--response requires --snapshot')
        state=apply_response(service,json.loads(args.snapshot.read_text()),json.loads(args.response.read_text()))
        print(json.dumps(dict(workspace=state['id'],version=state['backend']['version'])))
    else:
        if not args.out: parser.error('--out required')
        packet=snapshot(service.current(),json.loads(args.editorial.read_text()) if args.editorial else {})
        write(packet,args.out);print(packet['sha256'])

if __name__=='__main__': main()
