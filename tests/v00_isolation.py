"""V00 test-only bootstrap. Standard library only; install before core imports.

Core source is copied into an isolated tree and imported from there, so every
resource-relative __file__ points into the runtime without patching the import
machinery. This is a test harness, not an OS sandbox; subprocesses must
explicitly bootstrap too.
"""
from pathlib import Path
import json
import os
import shutil
import sqlite3
import socket
import sys
import tempfile
import uuid
from urllib.parse import urlsplit
from urllib.request import url2pathname

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/v3.0.0/evidence/V00'
_STATE = None


class IsolationViolation(RuntimeError):
    pass


def bootstrap():
    global _STATE
    if _STATE is not None:
        return _STATE
    if any(n == 'core' or n.startswith('core.') for n in sys.modules):
        raise IsolationViolation('V00: application imported before isolation')
    parent = Path(os.environ.get('V00_EVIDENCE_DIR', EVIDENCE)).resolve()
    if not parent.is_relative_to(EVIDENCE.resolve()):
        raise IsolationViolation('V00: evidence path escaped workspace')
    runtime = parent / ('runtime-' + uuid.uuid4().hex)
    runtime.mkdir(parents=True)
    for name in ('core/engine', 'core/prompts', 'core/services', 'core/ui',
                 'var/db', 'var/saves', 'var/books', 'var/exports', 'var/cache',
                 'tmp', 'home', 'assets/personas/standard', 'assets/personas/enhanced',
                 'assets/data/characters/builtin', 'assets/data/characters/user/overrides'):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    # Core source is imported from the runtime copy; __file__ stays truthful.
    shutil.copytree(ROOT / 'core', runtime / 'core', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # Only general application resources; never copy character/persona/main DB seeds.
    for group in ('prompts', 'papers', 'rules', 'lore', 'data'):
        for source in (ROOT / 'assets' / group).glob('*'):
            if not source.is_file() or source.name in ('work_library.md', 'character_pools.json'):
                continue
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise IsolationViolation('V00: unsafe resource link')
            dest = runtime / 'assets' / group / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
    (runtime / 'assets/data/character_pools.json').write_text('{"characters": []}', encoding='utf8')
    (runtime / 'assets/rules/work_library.md').write_text('# Synthetic library\n\n## 第六章 权限与扩展\n', encoding='utf8')
    for book_id in ('synthetic-a', 'synthetic-b'):
        book = runtime / 'var/books' / book_id
        (book / 'chapters').mkdir(parents=True)
        texts = ['第一章 林澈又名阿澈，守着灯塔。𠮷字刻在门上。',
                 '第二章 林澈隐藏钥匙的秘密。证人说钥匙已丢失，彼此矛盾。',
                 '第三章 林澈为救人死亡。另一作品的同名人物并非此人。']
        for n, text in enumerate(texts, 1):
            (book / 'chapters' / f'{n:04d}.txt').write_text(text, encoding='utf8')
        (book / 'chapter_index.json').write_text(json.dumps({'book_id': book_id, 'chapters': [{'idx': n, 'title': f'Chapter {n}'} for n in range(1, 4)]}), encoding='utf8')
    (runtime / 'var/saves/sentinel.json').write_text('{"synthetic":true,"history":"preserve"}', encoding='utf8')
    database = runtime / 'var/db/fate_engine.db'
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA user_version=0')
    for key in list(os.environ):
        if any(word in key.upper() for word in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')):
            os.environ.pop(key, None)
    for key in ('TEMP', 'TMP', 'TMPDIR', 'GRADIO_TEMP_DIR'):
        os.environ[key] = str(runtime / 'tmp')
    for key in ('HOME', 'USERPROFILE', 'XDG_CACHE_HOME', 'APPDATA', 'LOCALAPPDATA'):
        os.environ[key] = str(runtime / 'home')
    os.environ.update(FATE_VAR_DIR=str(runtime / 'var'), PYTHONDONTWRITEBYTECODE='1',
                      GRADIO_ANALYTICS_ENABLED='False', HF_HUB_OFFLINE='1',
                      PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    sys.dont_write_bytecode = True
    tempfile.tempdir = str(runtime / 'tmp')
    os.chdir(runtime)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(runtime))
    protected = [ROOT / 'var', ROOT / 'assets/personas', ROOT / 'assets/data/characters',
                 ROOT / 'assets/data/character_pools.json', ROOT / 'assets/rules/work_library.md']

    def check_path(value, writing=False):
        if isinstance(value, int) or value is None:
            return
        if os.fsdecode(value).lower() in (os.devnull.lower(), 'nul'):
            return
        p = Path(os.fsdecode(value)).resolve()
        if writing and not p.is_relative_to(runtime):
            raise IsolationViolation('V00: write outside isolated runtime rejected')
        if not writing and any(p == q or p.is_relative_to(q) for q in protected):
            # Existing asset gate may hash the original work library, never parse it.
            frame = sys._getframe(2)
            while frame is not None:
                if p == protected[-1] and frame.f_code.co_name == '_sha256' and Path(frame.f_code.co_filename).resolve() == ROOT / 'build/asset_gate.py':
                    return
                frame = frame.f_back
            raise IsolationViolation('V00: protected asset read rejected')

    # Windows implements socketpair in Python using an ephemeral loopback TCP
    # listener. Allow only that exact stdlib frame and its own socket objects,
    # never general loopback traffic, DNS, HTTP, or model connections.
    socketpair_code = getattr(socket.socketpair, '__code__', None)

    def internal_socketpair(event, args):
        if event not in ('socket.bind', 'socket.connect'):
            return False
        frame = sys._getframe(2)
        if socketpair_code is None or frame.f_code is not socketpair_code:
            return False
        local = frame.f_locals
        sock, address = args
        if event == 'socket.bind':
            return sock is local.get('lsock') and address in (('127.0.0.1', 0), ('::1', 0))
        listener = local.get('lsock')
        return (sock is local.get('csock') and listener is not None
                and address == listener.getsockname()[:2]
                and address[0] in ('127.0.0.1', '::1'))

    def audit(event, args):
        if event == 'open':
            mode, flags = args[1], args[2]
            check_path(args[0], bool((isinstance(mode, str) and any(c in mode for c in 'wax+')) or
                                    (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))))
        elif event == 'sqlite3.connect':
            database_path = str(args[0])
            if database_path.startswith('file:'):
                uri = urlsplit(database_path)
                if uri.netloc not in ('', 'localhost'):
                    raise IsolationViolation('V00: remote database URI rejected')
                database_path = url2pathname(uri.path)
            if database_path != ':memory:':
                # Audit has no uri=True argument. Conservatively require even
                # read-only URI connections to target the isolated runtime.
                check_path(database_path, True)
        elif event in ('os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.utime'):
            check_path(args[0], True)
        elif event in ('os.rename', 'os.link', 'os.symlink'):
            check_path(args[0], True)
            check_path(args[1], True)
        elif event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.sendto', 'socket.bind'):
            if not internal_socketpair(event, args):
                raise IsolationViolation('V00: network access rejected')
        elif event in ('os.system', 'os.startfile', 'os.posix_spawn'):
            raise IsolationViolation('V00: unguarded process rejected')
    sys.addaudithook(audit)
    _STATE = {'root': ROOT, 'runtime': runtime, 'database': database}
    (runtime / 'isolation.json').write_text(json.dumps({k: str(v) for k, v in _STATE.items()}, indent=2))
    return _STATE
