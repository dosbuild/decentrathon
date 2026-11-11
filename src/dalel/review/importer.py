"""Validate an offline decision export before replacing any delivery files."""
import json
from pathlib import Path
import shutil
import tempfile

from dalel.formats.arb import ArbDocument
from dalel.review.screen import apply, sha


def replace_files(stage, output, previous):
    """Keep the delivery directory openable on Windows; roll back failed writes."""
    previous.mkdir()
    installed, created = [], []
    try:
        for source in sorted(stage.rglob('*')):
            if not source.is_file():
                continue
            relative = source.relative_to(stage)
            target = output/relative
            if target.is_file() and target.read_bytes() == source.read_bytes():
                continue
            existed = target.exists()
            if existed:
                saved = previous/relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, saved)
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()
                created.append(parent)
            source.replace(target)
            installed.append((relative, existed))
    except BaseException:
        try:
            for relative, existed in reversed(installed):
                if existed:
                    (previous/relative).replace(output/relative)
                else:
                    (output/relative).unlink()
            for directory in reversed(created):
                directory.rmdir()
        except BaseException as recovery_error:
            recovery = Path(tempfile.mkdtemp(prefix='.review-recovery-', dir=output.parent))
            previous.rename(recovery/'previous')
            raise RuntimeError(f'Review rollback needs recovery from {recovery}') from recovery_error
        raise


def import_decisions(bundle, decisions_path, output=None):
    bundle, decisions_path = Path(bundle).resolve(), Path(decisions_path).resolve()
    output = Path(output).resolve() if output is not None else bundle
    if output != bundle and (output.exists() or output.is_relative_to(bundle) or bundle.is_relative_to(output)):
        raise ValueError('Review output directory must be new and separate from the bundle')
    payload = json.loads((bundle/'review_manifest.json').read_text(encoding='utf8'))
    decisions = json.loads(decisions_path.read_text(encoding='utf8'))
    source_locale, target_locale = payload.get('source_locale', 'ru'), payload.get('target_locale', 'kk')
    source, target = (ArbDocument.read(bundle/f'intl_{locale}.arb') for locale in (source_locale, target_locale))
    result = apply(payload, decisions, source, target)
    if payload.get('glossary_digest'):
        from dalel.terminology.glossary import rows
        from dalel.terminology.review import digest
        applied = result.get('glossary')
        same_export = bool(applied and (bundle/'glossary.csv').read_bytes() == applied['csv']
                           and json.loads((bundle/'glossary_rules.json').read_text(encoding='utf8')) == applied['rules'])
        if not same_export and digest(rows(bundle/'glossary.csv', include_candidates=True)) != payload['glossary_digest']:
            raise ValueError('Glossary changed; rebuild review cards before accepting messages')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.review-', dir=output.parent) as temporary:
        stage = Path(temporary)/'delivery'
        shutil.copytree(bundle, stage)
        from dalel.cli import write_json
        history = stage/'review_history'
        history.mkdir(exist_ok=True)
        audit = history/(sha(decisions_path.read_bytes()) + '.json')
        if not audit.exists():
            audit.write_bytes(decisions_path.read_bytes())
        (stage/f'intl_{target_locale}_fixed.arb').write_bytes(result['fixed'])
        for name in ('changes', 'report'):
            write_json(stage/(name + '.json'), result[name])
        write_json(stage/'review_summary.json', result['summary'])
        if result.get('glossary'):
            (stage/'glossary.csv').write_bytes(result['glossary']['csv'])
            write_json(stage/'glossary_rules.json', result['glossary']['rules'])
            write_json(stage/'glossary_review_summary.json', result['glossary']['summary'])
        if target_locale == 'kk':
            from dalel.delivery.package import refresh_after_review
            refresh_after_review(stage, result['summary'], bind_review=True)
        else:
            from dalel.review.binding import bind
            bind(stage, result['summary'])
            write_json(stage/'flutter_validation.json', dict(status='not_run', reason='Selected output changed'))
        previous = Path(temporary)/'previous'
        if output.exists():
            # A user's shell may hold this directory open on Windows.
            replace_files(stage, output, previous)
        else:
            stage.rename(output)
    return result['summary']
