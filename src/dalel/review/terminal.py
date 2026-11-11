"""Keyboard review for a real named reviewer; no batch-approval endpoint."""
from __future__ import annotations
import json
import sys
import re
from pathlib import Path
from dalel.review.store import ReviewStore

def visible(value):
    # JSON spelling makes whitespace visible and neutralizes terminal escapes.
    return json.dumps(value,ensure_ascii=False)

def write_export(store,snapshot,out,protected_paths=()):
    out=Path(out);export=store.export(snapshot)
    locale=export['summary']['target_locale']
    fixed_name='intl_'+locale+'_fixed.arb' if re.fullmatch('[A-Za-z]{2,8}(?:[_-][A-Za-z0-9]{2,8})*',locale) else 'target_fixed.arb'
    outputs=[out/fixed_name,out/'report.json',out/'changes.json',out/'review_log.json',out/'review_summary.json']
    for destination in outputs:
        if any(destination.resolve()==p.resolve() or destination.exists() and p.exists() and destination.samefile(p) for p in protected_paths):
            raise ValueError('Review export would overwrite an input or review database')
    out.mkdir(parents=True,exist_ok=True);outputs[0].write_bytes(export['fixed'])
    for destination,name in zip(outputs[1:],('report','changes','review_log','summary')):
        destination.write_bytes((json.dumps(export[name],ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    return export['summary']

def run_review(source,target,findings,project,reviewer,out,*,dependencies=None,protected_paths=()):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('Human review requires an interactive terminal; redirected or batch input cannot approve')
    if any(Path(project).resolve()==p.resolve() or Path(project).exists() and p.exists() and Path(project).samefile(p) for p in protected_paths):
        raise ValueError('Review database would overwrite an input')
    store=ReviewStore(project,actor=reviewer,human=True)
    snapshot=store.register(source,target,findings,dependencies=dependencies)
    items=store.findings(snapshot);position=0
    protected=[Path(project),*protected_paths]
    print('Ручная проверка. Решения записываются немедленно. Черновик сам по себе не утверждён.')
    print('A — утвердить показанный текст и эту находку; C — подтвердить только находку; R — отклонить; S — отложить.')
    print('E — сохранить черновик; U — отозвать правку; L — связать эту находку с утверждённой правкой; N/P — далее/назад; X — экспорт; Q — выход.')
    while items and position<len(items):
        item=items[position];key=item['key'];state=store.state(snapshot,key)
        draft=state['draft'] if state['draft'] is not None else item.get('suggestion')
        approval=state['approval'];status=state['findings'].get(item['id'],{}).get('status','unreviewed')
        print(f'\n[{position+1}/{len(items)}] {key} · {item["issue_type"]} · {status}')
        print(source.locale+': '+visible(item['source']));print(target.locale+' исходный: '+visible(item['target']))
        print('Доказательство: '+visible(item['evidence']))
        print('Машинное предложение: '+visible(item.get('suggestion')));print('Черновик: '+visible(draft))
        print('В экспорт: '+visible(approval['text'] if approval else item['target']))
        if approval and draft!=approval['text']:
            print('В экспорт пойдёт предыдущая утверждённая версия. Новый черновик ещё не утверждён.')
        try:
            command=input('Действие [A/C/R/S/E/U/L/N/P/X/Q]: ').strip().upper()
            if command=='Q':
                break
            if command=='X':
                print(json.dumps(write_export(store,snapshot,out,protected),ensure_ascii=False));continue
            if command=='P':
                position=max(0,position-1);continue
            if command=='N':
                position+=1;continue
            action={'A':'approve','C':'confirm','R':'reject','S':'defer','E':'draft','U':'revoke','L':'link'}.get(command)
            if not action:
                print('Неизвестная команда.');continue
            text=draft if action=='approve' else None;reason=''
            if action=='draft':
                raw=input('Полный текст (для переносов введите JSON-строку с \\n): ')
                text=json.loads(raw) if raw.startswith('"') else raw
            if action in ('reject','defer'):
                reason=input('Причина: ')
            store.decide(snapshot,key,action,revision=state['revision'],text=text,
                finding_id=item['id'] if action in ('approve','confirm','reject','defer','link') else None,reason=reason)
            if action in ('approve','confirm','reject','defer','link'):
                position+=1
        except (ValueError,TypeError) as exc:
            print('Решение не сохранено: '+str(exc))
        except (EOFError,KeyboardInterrupt):
            print('\nПроверка остановлена; записанные решения сохранены.');break
    return write_export(store,snapshot,out,protected)
