"""Distinguish a concrete call-site migration from a replacement message."""
import re


def planned_keys(diagnostics):
    result = set()
    for plan in diagnostics:
        if plan.get('type') != 'requires_code_change' or not plan.get('reason') or not plan.get('call_site'):
            continue
        template, arguments = plan.get('template'), plan.get('arguments')
        if not isinstance(template, str) or not isinstance(arguments, dict) or not arguments:
            continue
        parameters = set(re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)[},]', template))
        if parameters == set(arguments) and all(arguments.values()):
            result.add(plan['key'])
    return result
