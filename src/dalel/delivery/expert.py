"""Prepare an isolated annotation workspace from exact saved machine proposals."""
import copy
import json
from pathlib import Path
import tempfile

from dalel.delivery.analysis_import import import_analysis
from dalel.workspace.identity import uid
from dalel.workspace.service import WorkspaceService


def prepare(bundle, output):
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(bundle) or bundle.is_relative_to(output):
        raise ValueError('Use a new expert directory separate from the saved analysis')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.expert-', dir=output.parent) as temporary:
        stage = Path(temporary)/'packet'
        stage.mkdir()
        summary = import_analysis(bundle/'locqa-replay.json', stage/'analysis')
        service = WorkspaceService(stage/'workspaces')
        state = service.open_bundle(stage/'analysis')
        candidate = copy.deepcopy(state)
        changes = json.loads((stage/'analysis/changes.json').read_text(encoding='utf8'))
        for change in changes:
            candidate['entries'][change['key']].update(workingText=change['after'], currentRevisionId=uid('proposal-'))
        candidate['ui'].update(filter='attention', selectedKey=changes[0]['key'] if changes else state['ui']['selectedKey'])
        state = service.save(state['id'], state['backend']['version'], candidate)
        summary.update(workspace=state['id'], proposal_drafts=len(changes), human_approvals=0, api_calls=0,
            structural_blockers=sum(len(e['backendValidation']['errors']) for e in state['entries'].values()),
            inherited_layout_guidance=sum(len(e['backendValidation'].get('warnings', [])) for e in state['entries'].values()))
        (stage/'expert_status.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
        (stage/'START.md').write_text('''# Экспертная разметка dalel

Из этой папки запустите `locqa serve --data-dir workspaces`. Модельные предложения уже внесены как неутверждённые черновики. Исходные ARB и сохранённый анализ находятся в `analysis/`.

Проверьте исходник, перевод, основание предложения и контекст. Можно изменить текст, подтвердить точную ревизию или запросить исправление. В Settings укажите своё имя или инициалы. Изменение глоссария снимает прежние утверждения.

В Release доступны промежуточный пакет черновиков и утверждённые изменения. Последний вариант требует успешной буквальной компиляции выбранного ARB и закрытых структурных зависимостей. Адаптированные Flutter-копии и миграция имён показаны отдельно. Защищённые маркеры нельзя снимать как замечание об оформлении.

Резервные копии и экспорт решений доступны в Files. Содержимое этой папки приватное. Подготовка очереди и работа интерфейса не вызывают модель.
''', encoding='utf8')
        stage.rename(output)
    return summary


def from_result(source, target, result, output, *, references=(), glossary=None, escaping=False):
    from dalel.delivery.replay import package
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.analysis-', dir=output.parent) as temporary:
        saved = Path(temporary)/'saved'
        package(source, target, result, saved, references=references, glossary=glossary, escaping=escaping)
        return prepare(saved, output)
