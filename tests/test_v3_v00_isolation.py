"""Standard-library subprocess checks; no application import before bootstrap."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def child(code):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    return subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=90)


class IsolationTests(unittest.TestCase):
    def test_v3_v00_preimport_empty_database_and_synthetic_reads(self):
        result = child('''
import sys, sqlite3, json
from tests.v00_isolation import bootstrap
assert not any(n == 'core' or n.startswith('core.') for n in sys.modules)
s = bootstrap()
with sqlite3.connect(s['database']) as db:
 assert db.execute('select count(*) from sqlite_master').fetchone()[0] == 0
from core.engine import character_db
assert character_db.DATABASE_PATH.resolve().is_relative_to(s['runtime'])
assert character_db.get_connection().execute('select count(*) from characters').fetchone()[0] == 0
from core import server, fate_engine
assert server.operation_journal.path.resolve().is_relative_to(s['runtime'])
assert str(s['runtime']) in fate_engine.WORK_LIBRARY_PATH
print('guarded-import-ok')
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('guarded-import-ok', result.stdout)

    def test_v3_v00_escape_and_network_rejected(self):
        result = child('''
from tests.v00_isolation import bootstrap, IsolationViolation
from pathlib import Path
import socket, sqlite3, asyncio, tempfile, subprocess, os
s = bootstrap()
a, b = socket.socketpair()
try:
 a.sendall(b'v00')
 assert b.recv(3) == b'v00'
finally:
 a.close(); b.close()
async def local_task():
 return 42
assert asyncio.run(local_task()) == 42
with sqlite3.connect(s['database'].as_uri() + '?mode=ro', uri=True) as db:
 assert db.execute('PRAGMA user_version').fetchone()[0] == 0
checks = [lambda: (s['root']/'var'/'v00-forbidden').write_text('x'),
 lambda: (s['root']/'var'/'db'/'fate_engine.db').read_bytes(),
 lambda: sqlite3.connect(s['root']/'var'/'db'/'fate_engine.db'),
 lambda: sqlite3.connect((s['root']/'var/db/fate_engine.db').as_uri() + '?mode=ro', uri=True),
 lambda: socket.create_connection(('127.0.0.1', 1)),
 lambda: (s['runtime']/'..'/'escape').write_text('x'),
 lambda: socket.socket().connect(('127.0.0.1', 1)),
 lambda: socket.socket().connect(('203.0.113.1', 443)),
 lambda: socket.socket().bind(('127.0.0.1', 0)),
 lambda: socket.getaddrinfo('example.com', 443),
 lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b'x', ('127.0.0.1', 1))]
for check in checks:
 try: check()
 except IsolationViolation: pass
 else: raise AssertionError('guard allowed forbidden operation')
# Exercise a real Windows junction; unlink only the junction, never its target.
link = s['runtime'] / 'escape-junction'
if os.name == 'nt':
 made = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(s['root'] / 'var')], capture_output=True)
 assert made.returncode == 0, made.stderr
else:
 link.symlink_to(s['root'] / 'var', target_is_directory=True)
try:
 for operation in (lambda: (link / 'v00-forbidden').write_text('x'),
                   lambda: (link / 'db/fate_engine.db').read_bytes()):
  try: operation()
  except IsolationViolation: pass
  else: raise AssertionError('junction escape accepted')
finally:
 if os.name == 'nt':
  removed = subprocess.run(['cmd.exe', '/d', '/c', 'rmdir', str(link)], capture_output=True)
  assert removed.returncode == 0, removed.stderr
 else:
  # Audit resolves the link; remove it in a deliberately scoped helper process.
  removed = subprocess.run([__import__('sys').executable, '-B', '-c', 'import os,sys; os.unlink(sys.argv[1])', str(link)])
  assert removed.returncode == 0
assert not link.exists()
sentinel = s['runtime'] / 'var/saves/sentinel.json'
before = sentinel.read_bytes()
try:
 with tempfile.TemporaryDirectory() as scratch:
  scratch_path = Path(scratch)
  (scratch_path / 'partial').write_text('injected failure')
  raise RuntimeError('injected')
except RuntimeError:
 pass
assert not scratch_path.exists()
assert sentinel.read_bytes() == before
print('network-junction-failure-cleanup-ok')
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_v3_v00_parallel_unique_runtime(self):
        code = "from tests.v00_isolation import bootstrap; print(bootstrap()['runtime'])"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        processes = [subprocess.Popen([sys.executable, '-B', '-c', code], cwd=ROOT,
                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [p.communicate(timeout=90) for p in processes]
        self.assertEqual([p.returncode for p in processes], [0, 0], outputs)
        self.assertNotEqual(outputs[0][0], outputs[1][0])

    def test_v3_v00_reject_existing_application_import(self):
        result = child("import sys, types; sys.modules['core'] = types.ModuleType('core'); from tests.v00_isolation import bootstrap, IsolationViolation\ntry: bootstrap()\nexcept IsolationViolation: print('late-import-rejected')\nelse: raise AssertionError('late guard accepted')")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('late-import-rejected', result.stdout)
        # Isolation is process-scoped: even a failing child cannot change the
        # caller's environment, working directory, or tempfile cache.
        import tempfile
        before = (dict(os.environ), os.getcwd(), tempfile.tempdir)
        failed = child("from tests.v00_isolation import bootstrap; bootstrap(); raise RuntimeError('injected child failure')")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('injected child failure', failed.stderr)
        self.assertEqual(before, (dict(os.environ), os.getcwd(), tempfile.tempdir))
        from tests.conftest import isolated_test_temp
        fixture = isolated_test_temp.__wrapped__()
        next(fixture)
        try:
            fixture.throw(RuntimeError('injected fixture failure'))
        except RuntimeError:
            pass
        else:
            self.fail('fixture swallowed failure')
        self.assertEqual(before, (dict(os.environ), os.getcwd(), tempfile.tempdir))


if __name__ == '__main__':
    unittest.main(verbosity=2)
