import os

name = os.environ.get('ZAPRET_NAME', 'zapret-bundled')
path = os.path.join(os.path.dirname(__file__), 'zapret_info.py')
with open(path, 'w', encoding='utf-8') as f:
    f.write('BUNDLE_NAME = %r\n' % name)
