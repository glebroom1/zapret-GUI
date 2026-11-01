"""Bounded release notification. Beta never replaces a running executable."""
import json
import re
import urllib.request

API = 'https://api.github.com/repos/glebroom1/zapret-GUI/releases/latest'
PREFIX = 'https://github.com/glebroom1/zapret-GUI/releases/tag/'


def version_key(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)(?:\.(\d+))?(?:-beta\.(\d+))?', value)
    if not match:
        raise ValueError('Неизвестный формат версии: '+value)
    a, b, c, beta = match.groups()
    return int(a), int(b), int(c or 0), 1 if beta is None else 0, int(beta or 0)


def parse_release(data, current):
    if data.get('draft') or data.get('prerelease'):
        return None
    tag = data['tag_name']
    if version_key(tag) <= version_key(current):
        return None
    url = data['html_url']
    if url != PREFIX + tag:
        raise ValueError('Неожиданный адрес релиза')
    return {'tag': tag, 'url': url}


def latest_release(current):
    request = urllib.request.Request(API, headers={'User-Agent': 'zapret-GUI/0.8-beta', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError('Ответ сервера слишком большой')
    return parse_release(json.loads(payload), current)
