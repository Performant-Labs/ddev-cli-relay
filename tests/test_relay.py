"""Tests for the CLI relay. Run with: python3 -m unittest discover -s tests -v"""

import http.client
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY_DIR = os.path.join(REPO, 'cli-relay')
sys.path.insert(0, RELAY_DIR)

import relay  # noqa: E402

TOKEN = 'test-token-0123456789'


def write_exe(directory, name, body):
    path = os.path.join(directory, name)
    with open(path, 'w') as fh:
        fh.write('#!/bin/sh\n' + body + '\n')
    os.chmod(path, 0o755)
    return path


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class RelayServerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.bindir = os.path.join(cls.tmp, 'bin')
        os.mkdir(cls.bindir)
        # Echoes args then stdin, so tests can see both arrived intact.
        cls.stub = write_exe(cls.bindir, 'echo-stub', 'printf "args:%s\\n" "$*"; cat')
        cls.slow = write_exe(cls.bindir, 'slow-stub', 'sleep 5')
        cls.extra = write_exe(cls.bindir, 'explicit-stub', 'echo explicit')
        # Starts a long-lived grandchild, records its PID, then hangs.
        write_exe(cls.bindir, 'spawner-stub', 'sleep 30 & echo $! > "$1"; sleep 30')
        write_exe(cls.bindir, 'pwd-stub', 'pwd')
        cls.marker = os.path.join(cls.tmp, 'pwned')
        cls.old_path = os.environ['PATH']
        os.environ['PATH'] = cls.bindir + os.pathsep + cls.old_path
        allowed = ['echo-stub', 'slow-stub', 'spawner-stub', 'pwd-stub', cls.extra]
        cls.server = relay.make_server('127.0.0.1', 0, TOKEN, allowed)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        os.environ['PATH'] = cls.old_path
        shutil.rmtree(cls.tmp)

    def request(self, method, path, body=None, headers=None, token=TOKEN):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        hdrs = {'Content-Type': 'application/json'}
        if token is not None:
            hdrs['Authorization'] = f'Bearer {token}'
        hdrs.update(headers or {})
        payload = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = json.loads(resp.read() or b'null')
        conn.close()
        return resp.status, data

    def execute(self, body, **kw):
        return self.request('POST', '/execute', body, **kw)

    # --- Health -----------------------------------------------------------

    def test_health_needs_no_token(self):
        status, data = self.request('GET', '/health', token=None)
        self.assertEqual(200, status)
        self.assertEqual('ok', data['status'])

    def test_health_identifies_token_without_revealing_it(self):
        _, data = self.request('GET', '/health', token=None)
        self.assertEqual(relay.token_id(TOKEN), data['token_id'])
        self.assertEqual(12, len(data['token_id']))
        self.assertNotIn(TOKEN, json.dumps(data))

    def test_idle_connections_time_out(self):
        self.assertEqual(relay.SOCKET_TIMEOUT, self.server.RequestHandlerClass.timeout)

    # --- Authentication ---------------------------------------------------

    def test_missing_token_is_rejected(self):
        status, _ = self.execute({'binary': 'echo-stub'}, token=None)
        self.assertEqual(401, status)

    def test_wrong_token_is_rejected(self):
        status, _ = self.execute({'binary': 'echo-stub'}, token='nope')
        self.assertEqual(401, status)

    def test_token_without_bearer_prefix_is_rejected(self):
        status, _ = self.execute({'binary': 'echo-stub'}, token=None,
                                 headers={'Authorization': TOKEN})
        self.assertEqual(401, status)

    def test_browser_origin_is_rejected_even_with_token(self):
        status, _ = self.execute({'binary': 'echo-stub'},
                                 headers={'Origin': 'https://evil.example'})
        self.assertEqual(403, status)

    def test_non_json_content_type_is_rejected(self):
        # A browser "simple request" uses text/plain; it must not get through.
        status, _ = self.execute({'binary': 'echo-stub'},
                                 headers={'Content-Type': 'text/plain'})
        self.assertEqual(415, status)

    # --- Allowlist --------------------------------------------------------

    def assert_refused(self, binary):
        status, data = self.execute({
            'binary': binary,
            'args': ['-c', f'touch {self.marker}'],
        })
        self.assertEqual(403, status, data)
        self.assertFalse(os.path.exists(self.marker), 'refused binary must not run')

    def test_shell_by_name_is_refused(self):
        self.assert_refused('sh')

    def test_shell_by_absolute_path_is_refused(self):
        self.assert_refused('/bin/sh')

    def test_relative_path_is_refused(self):
        self.assert_refused('./echo-stub')

    def test_absolute_path_to_allowlisted_name_is_accepted(self):
        status, data = self.execute({'binary': self.stub, 'prompt': 'hi'})
        self.assertEqual(200, status, data)
        self.assertEqual(0, data['exit_code'])

    def test_explicit_absolute_path_entry_is_accepted(self):
        status, data = self.execute({'binary': self.extra})
        self.assertEqual(200, status, data)
        self.assertEqual('explicit', data['stdout'].strip())

    def test_symlink_to_allowed_binary_is_refused(self):
        # The caller's path is never executed, so a link it controls (and could
        # swap to /bin/sh after the check) is refused outright.
        link = os.path.join(self.tmp, 'link-to-stub')
        os.symlink(self.stub, link)
        try:
            self.assert_refused(link)
        finally:
            os.unlink(link)

    def test_allowed_name_runs_path_resolved_at_startup(self):
        # Shadowing the tool on PATH after startup must not change what runs.
        shadow_dir = os.path.join(self.tmp, 'shadow')
        os.mkdir(shadow_dir)
        write_exe(shadow_dir, 'echo-stub', f'touch {self.marker}')
        os.environ['PATH'] = shadow_dir + os.pathsep + os.environ['PATH']
        try:
            status, data = self.execute({'binary': 'echo-stub', 'prompt': 'x'})
            self.assertEqual(200, status, data)
            self.assertIn('args:', data['stdout'])
            self.assertFalse(os.path.exists(self.marker))
        finally:
            os.environ['PATH'] = os.environ['PATH'].split(os.pathsep, 1)[1]
            shutil.rmtree(shadow_dir)

    def test_real_path_of_allowed_binary_is_accepted(self):
        status, data = self.execute({'binary': os.path.realpath(self.stub)})
        self.assertEqual(200, status, data)

    def test_refusal_explains_how_to_allow(self):
        _, data = self.execute({'binary': 'sh'})
        self.assertIn('AI_RELAY_ALLOWED_BINARIES', data['error'])

    # --- Execution --------------------------------------------------------

    def test_prompt_goes_to_stdin_and_args_pass_through(self):
        status, data = self.execute({
            'binary': 'echo-stub',
            'args': ['--print', 'two words'],
            'prompt': 'User: hello; rm -rf /',
        })
        self.assertEqual(200, status, data)
        self.assertEqual(0, data['exit_code'])
        self.assertIn('args:--print two words', data['stdout'])
        self.assertIn('User: hello; rm -rf /', data['stdout'])

    def test_timeout_returns_124(self):
        status, data = self.execute({'binary': 'slow-stub', 'timeout': 1})
        self.assertEqual(200, status)
        self.assertEqual(124, data['exit_code'])

    def test_timeout_kills_child_processes_too(self):
        pidfile = os.path.join(self.tmp, 'grandchild.pid')
        status, data = self.execute({
            'binary': 'spawner-stub', 'args': [pidfile], 'timeout': 1,
        })
        self.assertEqual(124, data['exit_code'], data)
        with open(pidfile) as fh:
            pid = int(fh.read().strip())
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        os.kill(pid, 9)
        self.fail('grandchild survived the timeout')

    def test_tool_runs_in_home_directory(self):
        status, data = self.execute({'binary': 'pwd-stub'})
        self.assertEqual(200, status, data)
        self.assertEqual(os.path.realpath(os.path.expanduser('~')),
                         os.path.realpath(data['stdout'].strip()))

    # --- Input validation -------------------------------------------------

    def test_non_string_args_are_rejected(self):
        status, _ = self.execute({'binary': 'echo-stub', 'args': ['ok', 3]})
        self.assertEqual(400, status)

    def test_non_integer_timeout_is_rejected(self):
        status, _ = self.execute({'binary': 'echo-stub', 'timeout': '5'})
        self.assertEqual(400, status)

    def test_missing_binary_is_rejected(self):
        status, _ = self.execute({'prompt': 'x'})
        self.assertEqual(400, status)

    def test_invalid_json_is_rejected(self):
        status, _ = self.execute(b'{not json')
        self.assertEqual(400, status)

    def test_oversized_body_is_rejected(self):
        status, _ = self.execute(b'{}', headers={'Content-Length': str(relay.MAX_BODY_BYTES + 1)})
        self.assertEqual(413, status)


class RelayUnitTest(unittest.TestCase):

    def test_bind_does_not_do_a_reverse_dns_lookup(self):
        import unittest.mock
        with unittest.mock.patch('socket.getfqdn', side_effect=AssertionError('getfqdn called')):
            server = relay.make_server('127.0.0.1', 0, TOKEN, [])
        server.server_close()

    def test_server_refuses_to_start_without_token(self):
        with self.assertRaises(ValueError):
            relay.make_server('127.0.0.1', 0, '', ['claude'])

    def test_main_refuses_to_start_without_token_file(self):
        env = dict(os.environ, AI_RELAY_TOKEN_FILE='/nonexistent/token',
                   AI_RELAY_PORT=str(free_port()))
        result = subprocess.run([sys.executable, os.path.join(RELAY_DIR, 'relay.py')],
                                env=env, capture_output=True, timeout=10)
        self.assertEqual(1, result.returncode)
        self.assertIn(b'no relay token', result.stderr)

    def test_relative_path_entries_are_ignored(self):
        work = tempfile.mkdtemp()
        old_cwd = os.getcwd()
        try:
            write_exe(work, 'planted', 'true')
            os.chdir(work)
            self.assertEqual('', relay.safe_path('.:vendor/bin::'))
            resolved = relay.resolve_allowlist(['planted'], '.')
            self.assertNotIn('planted', resolved)
        finally:
            os.chdir(old_cwd)
            shutil.rmtree(work)

    def test_unknown_or_relative_requests_resolve_to_nothing(self):
        resolved = {'claude': '/opt/bin/claude', '/opt/bin/claude': '/opt/bin/claude'}
        self.assertEqual('/opt/bin/claude', relay.resolve_binary('claude', resolved))
        self.assertEqual('/opt/bin/claude', relay.resolve_binary('/opt/bin/claude', resolved))
        self.assertIsNone(relay.resolve_binary('bin/claude', resolved))
        self.assertIsNone(relay.resolve_binary('/tmp/claude', resolved))
        self.assertIsNone(relay.resolve_binary('', resolved))

    def test_allowlist_merges_defaults_env_and_file(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as fh:
            fh.write('# comment\nmytool  # trailing comment\n\n/opt/x/bin/other\n')
        try:
            allowed = relay.parse_allowlist(' extra1 , ,extra2', fh.name)
        finally:
            os.unlink(fh.name)
        for name in ('claude', 'codex', 'extra1', 'extra2', 'mytool', '/opt/x/bin/other'):
            self.assertIn(name, allowed)
        self.assertNotIn('', allowed)


class StartScriptTest(unittest.TestCase):
    """Runs the real start/stop scripts against a scratch project directory."""

    def setUp(self):
        self.approot = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.approot, '.ddev'))
        shutil.copytree(RELAY_DIR, os.path.join(self.approot, '.ddev', 'cli-relay'))
        self.port = free_port()
        self.env = dict(os.environ, DDEV_APPROOT=self.approot,
                        DDEV_SITENAME=f'relay-test-{self.port}',
                        AI_RELAY_PORT=str(self.port))
        self.env.pop('AI_RELAY_BIND', None)
        if platform.system() != 'Darwin':
            # Off macOS start.sh binds to the DDEV network gateway, which needs
            # DDEV; exercise the override path instead.
            self.env['AI_RELAY_BIND'] = '127.0.0.1'

    def tearDown(self):
        subprocess.run([os.path.join(self.approot, '.ddev', 'cli-relay', 'stop.sh')],
                       env=self.env, capture_output=True)
        shutil.rmtree(self.approot)

    def wait_for_health(self):
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=1)
                conn.request('GET', '/health')
                if conn.getresponse().status == 200:
                    return
            except OSError:
                time.sleep(0.2)
        self.fail('relay did not come up')

    def test_start_creates_private_token_and_binds_loopback(self):
        result = subprocess.run([os.path.join(self.approot, '.ddev', 'cli-relay', 'start.sh')],
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f'127.0.0.1:{self.port}', result.stdout)
        self.assertNotIn('0.0.0.0', result.stdout)

        token_file = os.path.join(self.approot, '.ddev', 'cli-relay', 'token')
        mode = stat.S_IMODE(os.stat(token_file).st_mode)
        self.assertEqual(0o600, mode)
        with open(token_file) as fh:
            token = fh.read().strip()
        self.assertGreaterEqual(len(token), 40)

        self.wait_for_health()

        # The token on disk is the one the running relay accepts.
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('POST', '/execute', body=json.dumps({'binary': 'sh'}),
                     headers={'Authorization': f'Bearer {token}',
                              'Content-Type': 'application/json'})
        self.assertEqual(403, conn.getresponse().status)  # authenticated, then refused by allowlist

    def test_start_refuses_lan_address(self):
        if platform.system() != 'Darwin':
            self.skipTest('loopback bind default applies on macOS')
        lan_ip = self.lan_address()
        if lan_ip is None:
            self.skipTest('no non-loopback address on this machine')
        result = subprocess.run([os.path.join(self.approot, '.ddev', 'cli-relay', 'start.sh')],
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        with self.assertRaises(OSError):
            socket.create_connection((lan_ip, self.port), timeout=2).close()

    @staticmethod
    def lan_address():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(('192.0.2.1', 9))  # TEST-NET-1; no packet is sent
                ip = s.getsockname()[0]
            except OSError:
                return None
        return None if ip.startswith('127.') else ip

    def test_start_fails_loudly_when_port_belongs_to_another_relay(self):
        other = relay.make_server('127.0.0.1', self.port, 'someone-elses-token', [])
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            result = subprocess.run([os.path.join(self.approot, '.ddev', 'cli-relay', 'start.sh')],
                                    env=self.env, capture_output=True, text=True, timeout=30)
        finally:
            other.shutdown()
            other.server_close()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('failed to start', result.stderr)
        self.assertFalse(os.path.exists(f'/tmp/ddev-cli-relay-{self.env["DDEV_SITENAME"]}.pid'))

    def test_start_refuses_old_python(self):
        shim_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, shim_dir)
        shim = os.path.join(shim_dir, 'python3')
        with open(shim, 'w') as fh:
            # Behaves like Python 3.8: fails the version check.
            fh.write('#!/bin/sh\n[ "$1" = --version ] && echo "Python 3.8.18" && exit 0\nexit 1\n')
        os.chmod(shim, 0o755)
        env = dict(self.env, PATH=shim_dir + os.pathsep + self.env['PATH'])
        result = subprocess.run([os.path.join(self.approot, '.ddev', 'cli-relay', 'start.sh')],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('requires Python 3.9 or newer', result.stderr)
        self.assertIn('3.8.18', result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approot, '.ddev', 'cli-relay', 'token')),
                         'Nothing is set up with an unsupported Python.')

    def test_whitespace_only_token_is_replaced(self):
        token_file = os.path.join(self.approot, '.ddev', 'cli-relay', 'token')
        with open(token_file, 'w') as fh:
            fh.write('\n  \n')
        ensure = os.path.join(self.approot, '.ddev', 'cli-relay', 'ensure-token.sh')
        subprocess.run([ensure], env=self.env, check=True, capture_output=True)
        with open(token_file) as fh:
            self.assertGreaterEqual(len(fh.read().strip()), 40)

    def test_token_survives_restart(self):
        ensure = os.path.join(self.approot, '.ddev', 'cli-relay', 'ensure-token.sh')
        subprocess.run([ensure], env=self.env, check=True, capture_output=True)
        token_file = os.path.join(self.approot, '.ddev', 'cli-relay', 'token')
        with open(token_file) as fh:
            first = fh.read()
        subprocess.run([ensure], env=self.env, check=True, capture_output=True)
        with open(token_file) as fh:
            self.assertEqual(first, fh.read())

    def test_token_is_gitignored(self):
        with open(os.path.join(RELAY_DIR, '.gitignore')) as fh:
            self.assertIn('token', fh.read().split())


if __name__ == '__main__':
    unittest.main()
