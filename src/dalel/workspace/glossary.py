"""Workspace glossary normalization and lossless CSV column export."""
import copy
import csv
import io
from dalel.review.store import canonical
from dalel.workspace.identity import uid

def glossary_text(terms, *, analysis=False, target_locale='kk'):
    locale = target_locale.replace('-', '_').split('_')[0]
    base = ['ru', 'kk', 'en', 'notes', 'status', 'variants', 'scope', 'ru_variants']
    fields = list(dict.fromkeys(base + ([locale + '_variants'] if analysis else []) + [k for term in terms for k in term if k not in ('id', '_rule')]))
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\r\n', extrasaction='ignore')
    writer.writeheader()
    for term in terms:
        row = dict(term)
        row['variants'] = ' | '.join(term.get('variants', []))
        if analysis:
            row['status'] = 'consumer_agreed' if term.get('status') == 'agreed' else 'ai_proposed'
            locale = target_locale.replace('-', '_').split('_')[0]
            row[locale + '_variants'] = row.get(locale + '_variants') or (row['variants'] if locale == 'kk' else '')
            # Ensure this extra column is present before selecting analysis rows.
        writer.writerow({k: (canonical(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()})
    return stream.getvalue()


def normalize_terms(terms):
    if not isinstance(terms, list):
        raise ValueError('Glossary must be a list of terms')
    result = []
    ids = set()
    for item in terms:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip() for k in ('ru', 'kk', 'en')):
            raise ValueError('Each glossary term needs Russian, Kazakh and English text')
        term = copy.deepcopy(item)
        term['id'] = str(term.get('id') or uid('term-'))
        if term['id'] in ids:
            raise ValueError('Duplicate glossary term identifier')
        ids.add(term['id'])
        term['status'] = 'agreed' if term.get('status') == 'agreed' else 'proposed'
        if not isinstance(term.get('variants', []), list) or any(not isinstance(v, str) for v in term.get('variants', [])):
            raise ValueError('Term variants must be strings')
        term['variants'] = term.get('variants', [])
        term['notes'] = str(term.get('notes') or term.get('comment') or term.get('usage') or '')
        term['scope'] = str(term.get('scope') or '')
        result.append(term)
    return result
