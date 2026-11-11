"""Read section context from ordered ARB members, including repeated comments."""


def sections(document):
    active = ''
    result = {}
    for member in document.members:
        if member.key == '@@comment' and isinstance(member.value, str):
            active = member.value
        elif not member.key.startswith('@'):
            result[member.key] = active
    return result
