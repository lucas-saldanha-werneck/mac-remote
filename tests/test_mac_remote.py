#!/usr/bin/env python3
"""Tests for mac-remote that never touch the real screen: no VNC connection is opened,
nothing is clicked or typed on the Mac. The VNC client is replaced by fakes.

Run: python3 tests/test_mac_remote.py   (works with or without the real dependencies)"""
import asyncio, contextlib, io, json, os, subprocess, sys, tempfile, types, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix='mac-remote-test-')
os.environ['MAC_REMOTE_HOME'] = os.path.join(TMP, 'data')
os.environ['MAC_REMOTE_CONFIG'] = os.path.join(TMP, 'config')
for k in ('MAC_REMOTE_PASS', 'MAC_REMOTE_PASS_CMD', 'MAC_REMOTE_USER'):
    os.environ.pop(k, None)
os.makedirs(os.environ['MAC_REMOTE_HOME'])

try:
    import asyncvnc  # noqa: F401
    REAL_ASYNCVNC = True
except ImportError:                     # stand-in with the only attribute the code reads at import time
    REAL_ASYNCVNC = False
    fake = types.ModuleType('asyncvnc')
    names = ['Super_L', 'Alt_L', 'Control_L', 'Shift_L', 'Return', 'Escape', 'Tab', 'space', 'BackSpace',
             'Delete', 'Up', 'Down', 'Left', 'Right', 'Home', 'End', 'Page_Up', 'Page_Down'] + \
            [f'F{i}' for i in range(1, 13)] + [chr(c) for c in range(32, 127)] + ['á']
    fake.key_codes = {n: 0xff00 + i for i, n in enumerate(names)}
    sys.modules['asyncvnc'] = fake

sys.path.insert(0, os.path.join(ROOT, 'lib'))
import mac_remote as mr  # noqa: E402

SECRET = 'S3cret-pw'


def write_config(text):
    with open(os.environ['MAC_REMOTE_CONFIG'], 'w') as f:
        f.write(text)


class FakeWriter:
    def __init__(self):
        self.data = b''

    def write(self, b):
        self.data += b

    def close(self):
        pass


class FakeMouse:
    def __init__(self, log):
        self.log = log

    def move(self, x, y):
        self.log.append(('move', x, y))

    @contextlib.contextmanager
    def hold(self, button=0):
        self.log.append(('down', button))
        yield
        self.log.append(('up', button))


class FakeClient:
    def __init__(self):
        self.log = []
        self.writer = FakeWriter()
        self.mouse = FakeMouse(self.log)

    async def drain(self):
        pass


class FakeSession:
    def __init__(self):
        self.c = FakeClient()

    async def settle(self, **kw):
        pass

    async def wake(self):
        pass

    def check(self):
        pass

    async def close(self, grace=0):
        pass


def keydowns(data):
    """Keysyms of the key-down events (RFB KeyEvent, down flag = 1) in written bytes."""
    out = []
    for i in range(0, len(data), 8):
        msg = data[i:i + 8]
        if msg[:2] == b'\x04\x01':
            out.append(int.from_bytes(msg[4:8], 'big'))
    return out


class PasswordSource(unittest.TestCase):
    def setUp(self):
        for k in ('MAC_REMOTE_PASS', 'MAC_REMOTE_PASS_CMD', 'MAC_REMOTE_USER'):
            os.environ.pop(k, None)
        write_config('')

    def test_no_source(self):
        self.assertIsNone(mr.pass_source())
        with self.assertRaises(mr.Fail) as e:
            mr.get_password()
        self.assertIn('MAC_REMOTE_PASS_CMD', str(e.exception))

    def test_command_from_config_file(self):
        write_config(f"# comment\nMAC_REMOTE_PASS_CMD=\"printf '{SECRET}\\n'\"\n")
        self.assertEqual(mr.pass_source(), 'command')
        self.assertEqual(mr.get_password(), SECRET)

    def test_only_the_first_line_is_the_password(self):
        # pass and gopass print the password, then other fields of the entry
        os.environ['MAC_REMOTE_PASS_CMD'] = f"printf '{SECRET}\\nurl: example.com\\nuser: me\\n'"
        self.assertEqual(mr.get_password(), SECRET)

    def test_env_password_wins(self):
        write_config("MAC_REMOTE_PASS_CMD=printf other\n")
        os.environ['MAC_REMOTE_PASS'] = SECRET
        self.assertEqual(mr.pass_source(), 'env')
        self.assertEqual(mr.get_password(), SECRET)

    def test_env_overrides_config_file(self):
        write_config("MAC_REMOTE_PASS_CMD=printf fromfile\nMAC_REMOTE_USER=fileuser\n")
        os.environ['MAC_REMOTE_PASS_CMD'] = 'printf fromenv'
        os.environ['MAC_REMOTE_USER'] = 'envuser'
        self.assertEqual(mr.get_password(), 'fromenv')
        self.assertEqual(mr.mac_user(), 'envuser')

    def test_password_in_config_file_is_ignored(self):
        write_config(f"MAC_REMOTE_PASS={SECRET}\n")
        self.assertIsNone(mr.pass_source())

    def test_user_defaults_to_current_user(self):
        import pwd
        self.assertEqual(mr.mac_user(), pwd.getpwuid(os.getuid()).pw_name)

    def test_failing_command_leaks_nothing(self):
        # The command prints the secret on stdout AND stderr, then fails. Run in a child process
        # so we see the real file descriptors, not just Python's sys.stdout.
        env = dict(os.environ, MAC_REMOTE_PASS_CMD=f'echo {SECRET}; echo {SECRET} >&2; exit 3')
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import mac_remote as m\n'
                'try:\n    m.get_password()\nexcept m.Fail as e:\n    print("FAIL:", e)\n')
        r = subprocess.run([sys.executable, '-c', code, os.path.join(ROOT, 'lib')], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertIn('FAIL: MAC_REMOTE_PASS_CMD failed (exit 3)', r.stdout)
        self.assertNotIn(SECRET, r.stdout + r.stderr)

    def test_empty_output_fails(self):
        os.environ['MAC_REMOTE_PASS_CMD'] = 'true'
        with self.assertRaises(mr.Fail):
            mr.get_password()

    def test_holder_is_not_given_the_password_on_its_command_line(self):
        os.environ['MAC_REMOTE_PASS'] = SECRET
        seen = {}

        class Stop(Exception):
            pass

        def fake_popen(args, **kw):
            seen['args'] = args
            raise Stop()

        a = types.SimpleNamespace(idle_timeout=600)
        with mock.patch.object(mr, 'holder_info', return_value=None), \
                mock.patch.object(mr, 'is_locked', return_value=True), \
                mock.patch.object(mr.subprocess, 'Popen', fake_popen):
            with self.assertRaises(Stop):
                mr.cmd_unlock(a)
        self.assertEqual(seen['args'][-2:], ['_holder', '600'])
        self.assertFalse(any(SECRET in str(x) for x in seen['args']))

    def test_unlock_without_source_refuses_before_starting_holder(self):
        a = types.SimpleNamespace(idle_timeout=600)
        with mock.patch.object(mr, 'holder_info', return_value=None), \
                mock.patch.object(mr, 'is_locked', return_value=True), \
                mock.patch.object(mr.subprocess, 'Popen') as popen:
            with self.assertRaises(mr.Fail):
                mr.cmd_unlock(a)
            popen.assert_not_called()


class Typing(unittest.TestCase):
    def setUp(self):
        os.environ.pop('MAC_REMOTE_PASS', None)

    def test_dead_key_layout_pastes_apostrophe(self):
        with mock.patch.object(mr, 'layout_has_dead_keys', return_value=True):
            self.assertTrue(mr.needs_paste("It's"))
            self.assertFalse(mr.needs_paste('Its'))
        with mock.patch.object(mr, 'layout_has_dead_keys', return_value=False):
            self.assertFalse(mr.needs_paste("It's"))

    def test_emoji_pastes(self):
        with mock.patch.object(mr, 'layout_has_dead_keys', return_value=False):
            self.assertTrue(mr.needs_paste('ok \U0001F600'))

    def test_password_is_never_pasted(self):
        os.environ['MAC_REMOTE_PASS'] = "pa's\U0001F600"
        with mock.patch.object(mr, 'layout_has_dead_keys', return_value=True):
            self.assertFalse(mr.needs_paste("pa's\U0001F600"))

    def test_type_keys(self):
        self.assertEqual(mr.type_keys('a\nb\t'), ['a', 'Return', 'b', 'Tab'])

    def test_parse_combo(self):
        self.assertEqual(mr.parse_combo('cmd+shift+4'), ['Super_L', 'Shift_L', '4'])
        self.assertEqual(mr.parse_combo('ctrl+A'), ['Control_L', 'a'])
        self.assertEqual(mr.parse_combo('f5'), ['F5'])
        self.assertEqual(mr.parse_combo('return'), ['Return'])
        with self.assertRaises(mr.Fail):
            mr.parse_combo('cmd+nosuchkey')


class Input(unittest.TestCase):
    def test_refuses_when_locked(self):
        with mock.patch.object(mr, 'is_locked', return_value=True):
            with self.assertRaises(mr.Fail) as e:
                mr.prepare_input({})
        self.assertIn('LOCKED', str(e.exception))

    def test_refuses_when_someone_is_using_the_mac(self):
        with mock.patch.object(mr, 'is_locked', return_value=False), \
                mock.patch.object(mr, 'user_active', return_value=(True, 3.0)):
            with self.assertRaises(mr.Fail):
                mr.prepare_input({})
            mr.prepare_input({'force': True})          # --force goes through

    def run_input(self, req):
        s = FakeSession()
        with mock.patch.object(mr, 'is_locked', return_value=False), \
                mock.patch.object(mr, 'user_active', return_value=(False, 99.0)), \
                mock.patch.object(mr, 'frontmost', return_value='TextEdit'), \
                mock.patch.object(mr.asyncio, 'sleep', new=mock.AsyncMock()):
            out = asyncio.run(mr.do_input(s, req, one_off=False))
        return s, out

    def test_click(self):
        s, out = self.run_input({'cmd': 'click', 'x': 10, 'y': 20, 'action': 'click'})
        self.assertEqual(s.c.log, [('move', 10, 20), ('down', 0), ('up', 0)])
        self.assertIn('frontmost app: TextEdit', out)

    def test_double_and_right_click(self):
        s, _ = self.run_input({'cmd': 'click', 'x': 1, 'y': 2, 'action': 'double'})
        self.assertEqual([e for e in s.c.log if e[0] == 'down'], [('down', 0), ('down', 0)])
        s, _ = self.run_input({'cmd': 'click', 'x': 1, 'y': 2, 'action': 'right'})
        self.assertIn(('down', 2), s.c.log)

    def test_type_does_not_echo_text(self):
        kc = sys.modules['asyncvnc'].key_codes
        s, out = self.run_input({'cmd': 'type', 'keys': ['h', 'i']})
        self.assertEqual(keydowns(s.c.writer.data), [kc['h'], kc['i']])
        self.assertNotIn('hi', out)

    def test_type_password_prints_nothing(self):
        kc = sys.modules['asyncvnc'].key_codes
        s = FakeSession()
        buf = io.StringIO()
        with mock.patch.object(mr.asyncio, 'sleep', new=mock.AsyncMock()), \
                mock.patch.object(mr, 'is_locked', return_value=True), \
                contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            asyncio.run(mr.type_password(s, 'ab'))
        downs = keydowns(s.c.writer.data)
        self.assertEqual(downs[0], kc['Shift_L'])              # wake
        self.assertEqual(downs[-3:], [kc['a'], kc['b'], kc['Return']])
        self.assertEqual(buf.getvalue(), '')

    # someone unlocks the Mac (Touch ID) while the holder is about to type: the password must not
    # land in whatever app is now in front, and must never be submitted with Return
    def type_with_lock(self, locked_fn, pw='abc'):
        s = FakeSession()
        with mock.patch.object(mr.asyncio, 'sleep', new=mock.AsyncMock()), \
                mock.patch.object(mr, 'is_locked', side_effect=lambda: locked_fn(s)):
            with self.assertRaises(mr.Fail) as e:
                asyncio.run(mr.type_password(s, pw))
        return keydowns(s.c.writer.data), str(e.exception)

    def test_type_password_refuses_when_screen_already_unlocked(self):
        downs, err = self.type_with_lock(lambda s: False)
        self.assertEqual(downs, [])                             # no wake, no BackSpace, no password
        self.assertIn('no longer locked', err)

    def test_type_password_stops_when_unlocked_mid_typing(self):
        kc = sys.modules['asyncvnc'].key_codes
        downs, err = self.type_with_lock(lambda s: kc['a'] not in keydowns(s.c.writer.data))
        self.assertNotIn(kc['b'], downs)
        self.assertNotIn(kc['c'], downs)
        self.assertNotIn(kc['Return'], downs)
        self.assertIn('no longer locked', err)

    def test_type_password_no_return_when_unlocked_before_submit(self):
        kc = sys.modules['asyncvnc'].key_codes
        downs, err = self.type_with_lock(lambda s: kc['c'] not in keydowns(s.c.writer.data))
        self.assertEqual(downs[-3:], [kc['a'], kc['b'], kc['c']])
        self.assertNotIn(kc['Return'], downs)
        self.assertIn('no longer locked', err)


class Coordinates(unittest.TestCase):
    def test_last_shot_to_framebuffer(self):
        with open(mr.LAST_SHOT, 'w') as f:
            json.dump({'offset': [100, 50], 'scale': 0.5}, f)
        self.assertEqual(mr.to_fb(10, 20, raw=False), (120, 90))
        self.assertEqual(mr.to_fb(10, 20, raw=True), (10, 20))

    def test_no_last_shot(self):
        mr.rm(mr.LAST_SHOT)
        with self.assertRaises(mr.Fail):
            mr.to_fb(1, 1, raw=False)

    def test_render_shot(self):
        try:
            import numpy as np
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest('numpy/pillow not installed')
        px = np.zeros((2000, 4000, 4), dtype=np.uint8)
        path = os.path.join(TMP, 'shot.jpg')
        with mock.patch.object(mr, 'is_locked', return_value=False):
            out = mr.render_shot(px, [(0, 0, 3200, 2000)], path, None)
        meta = json.load(open(mr.LAST_SHOT))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(meta['image_size'], [1600, 1000])
        self.assertAlmostEqual(meta['scale'], 0.5)
        self.assertIn('scale 0.5000', out)


class HolderSocket(unittest.TestCase):
    def test_no_socket_means_no_holder(self):
        mr.rm(mr.SOCK)
        self.assertIsNone(mr.holder_call({'cmd': 'ping'}))

    def test_stale_socket_is_cleaned(self):
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(mr.SOCK)
        s.close()                               # file stays, nobody listens
        self.assertIsNone(mr.holder_call({'cmd': 'ping'}))
        self.assertFalse(os.path.exists(mr.SOCK))

    def test_long_home_gets_a_short_socket_path(self):
        # Found live: a long MAC_REMOTE_HOME made the holder die with "AF_UNIX path too long".
        long_home = '/private/tmp/' + 'x' * 120
        p = mr.sock_path(long_home)
        self.assertLessEqual(len(os.fsencode(p)), 100)
        self.assertEqual(p, f'/tmp/mac-remote-{os.getuid()}/holder.sock')
        self.assertEqual(mr.sock_path('/short'), '/short/holder.sock')

    def test_socket_folder_open_to_others_is_refused(self):
        d = tempfile.mkdtemp()
        os.chmod(d, 0o755)
        with mock.patch.object(mr, 'SOCK', os.path.join(d, 'holder.sock')):
            with self.assertRaises(mr.Fail):
                mr.ensure_sock_dir()
            os.chmod(d, 0o700)
            mr.ensure_sock_dir()            # private folder: accepted


class Frontmost(unittest.TestCase):
    """Found live on macOS 27: lsappinfo prints '"Name" ASN:...' instead of '"LSDisplayName"="Name"'."""
    def run_with(self, info):
        outs = iter([types.SimpleNamespace(stdout='ASN:0x0-0x2fb2fb:\n'), types.SimpleNamespace(stdout=info)])
        with mock.patch.object(mr.subprocess, 'run', side_effect=lambda *a, **k: next(outs)):
            return mr.frontmost()

    def test_new_lsappinfo_format(self):
        self.assertEqual(self.run_with('"TextEdit" ASN:0x0-0x2fb2fb: (in front) \n    bundleID=[ NULL ] \n'), 'TextEdit')

    def test_old_lsappinfo_format(self):
        self.assertEqual(self.run_with('"LSDisplayName"="Google Chrome"\n'), 'Google Chrome')

    def test_unknown_format_is_none(self):
        self.assertIsNone(self.run_with('nothing useful\n'))


class FakeItem:
    """Stand-in for NSPasteboardItem: an ordered list of (type, data)."""
    def __init__(self, pairs=None):
        self.pairs = list(pairs or [])

    def types(self):
        return [t for t, _ in self.pairs]

    def dataForType_(self, t):
        return dict(self.pairs).get(t)

    def setData_forType_(self, data, t):
        self.pairs.append((t, data))
        return True


class FakePasteboard:
    """Stand-in for NSPasteboard.generalPasteboard(). The real clipboard is never touched."""
    TEXT = 'public.utf8-plain-text'

    def __init__(self, items):
        self.items = [FakeItem(p) for p in items]
        self.count = 1

    def contents(self):
        return [it.pairs for it in self.items]

    def changeCount(self):
        return self.count

    def pasteboardItems(self):
        return list(self.items)

    def clearContents(self):
        self.items = []
        self.count += 1
        return self.count

    def setString_forType_(self, s, t):
        self.items = [FakeItem([(t, s.encode())])]
        return True

    def writeObjects_(self, objs):
        self.items.extend(objs)
        return True

    def fake_run(self, args, input=None, **kw):
        """pbpaste/pbcopy emulated on this fake (what the old code used)."""
        if args == ['pbpaste']:
            return types.SimpleNamespace(stdout=self.dataForText(), returncode=0)
        if args == ['pbcopy']:
            self.clearContents()
            self.items = [FakeItem([(self.TEXT, input or b'')])]
            return types.SimpleNamespace(stdout=b'', returncode=0)
        raise AssertionError(f'unexpected subprocess call {args}')

    def dataForText(self):
        for it in self.items:
            d = it.dataForType_(self.TEXT)
            if d is not None:
                return d
        return b''


class Findings(unittest.TestCase):
    """Regression tests for the second review round."""

    def setUp(self):
        os.environ.pop('MAC_REMOTE_PASS', None)
        os.environ.pop('MAC_REMOTE_ALLOW_DEAD_KEY_PASSWORD', None)

    # --app must never become AppleScript source
    def test_app_name_is_not_interpolated_into_applescript(self):
        evil = 'Finder" to quit\ndo shell script "touch /tmp/pwned'
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            return types.SimpleNamespace(stdout=b'', returncode=0)

        with mock.patch.object(mr, 'is_locked', return_value=False), \
                mock.patch.object(mr, 'user_active', return_value=(False, 99.0)), \
                mock.patch.object(mr, 'frontmost', return_value=evil), \
                mock.patch.object(mr.subprocess, 'run', fake_run):
            mr.prepare_input({'app': evil})
        osa = [c for c in calls if c[0] == 'osascript']
        self.assertEqual(len(osa), 1)
        scripts = [osa[0][i + 1] for i, x in enumerate(osa[0]) if x == '-e']
        self.assertFalse(any('Finder' in s or 'shell' in s for s in scripts), osa[0])
        self.assertEqual(osa[0][-1], evil)                  # passed as data, in argv

    # a stop request must cancel the unlock before the password is typed
    def test_unlock_types_nothing_after_stop(self):
        h = mr.Holder(600)
        h.s = FakeSession()
        h.stop('signal SIGTERM')
        with mock.patch.object(mr.asyncio, 'sleep', new=mock.AsyncMock()), \
                mock.patch.object(mr, 'is_locked', return_value=True), \
                mock.patch.object(mr, 'layout_has_dead_keys', return_value=False):
            with self.assertRaises(mr.Fail):
                asyncio.run(h.unlock('ab'))
        self.assertEqual(keydowns(h.s.c.writer.data), [])

    def test_stop_during_password_fetch_skips_unlock(self):
        h = mr.Holder(600)

        def fetch():
            h.stop('signal SIGTERM')          # arrives while the password command runs
            return ('u', 'pw')

        unlock = mock.AsyncMock()
        sess = FakeSession()
        buf = io.StringIO()
        with mock.patch.object(mr, 'creds', side_effect=fetch), \
                mock.patch.object(mr, 'open_session', new=mock.AsyncMock(return_value=sess)), \
                mock.patch.object(mr, 'is_locked', return_value=True), \
                mock.patch.object(h, 'unlock', new=unlock), \
                mock.patch.object(mr.asyncio, 'sleep', new=mock.AsyncMock()), \
                contextlib.redirect_stdout(buf):
            rc = asyncio.run(h.run())
        unlock.assert_not_called()
        self.assertEqual(rc, 1)

    def test_unlock_timeout_waits_for_the_holder_to_exit(self):
        events = []

        class P:
            pid = 999999
            returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                events.append('wait')
                self.returncode = -15
                return self.returncode

        p = P()
        clock = iter(range(0, 10**6, 100))
        a = types.SimpleNamespace(idle_timeout=600)
        with mock.patch.object(mr, 'holder_info', return_value=None), \
                mock.patch.object(mr, 'is_locked', return_value=True), \
                mock.patch.object(mr, 'pass_source', return_value='env'), \
                mock.patch.object(mr.subprocess, 'Popen', return_value=p), \
                mock.patch.object(mr.os, 'kill', side_effect=lambda pid, sig: events.append(('kill', sig))), \
                mock.patch.object(mr.time, 'sleep'), \
                mock.patch.object(mr.time, 'time', side_effect=lambda: next(clock)):
            with self.assertRaises(mr.Fail) as e:
                mr.cmd_unlock(a)
        self.assertEqual(events[0], ('kill', mr.signal.SIGTERM))
        self.assertIn('wait', events)
        self.assertIn('holder stopped', str(e.exception))

    # the paste path must keep every clipboard format, and touch nothing when it refuses
    def run_paste(self, pb, locked=False, active=False):
        with mock.patch.object(mr, 'pasteboard', return_value=pb, create=True), \
                mock.patch.object(mr, 'new_pb_item', side_effect=FakeItem, create=True), \
                mock.patch.object(mr.subprocess, 'run', pb.fake_run), \
                mock.patch.object(mr, 'needs_paste', return_value=True), \
                mock.patch.object(mr, 'is_locked', return_value=locked), \
                mock.patch.object(mr, 'user_active', return_value=(active, 3.0)), \
                mock.patch.object(mr, 'run_request', return_value='ok') as rr, \
                mock.patch.object(mr.time, 'sleep'), \
                contextlib.redirect_stdout(io.StringIO()):
            mr.cmd_type(types.SimpleNamespace(text='ok \U0001F600', force=False, app=None))
        return rr

    def test_paste_restores_non_text_clipboard(self):
        original = [[('public.png', b'\x89PNG...'), ('public.tiff', b'II*...')],
                    [('public.file-url', b'file:///a')]]
        pb = FakePasteboard(original)
        rr = self.run_paste(pb)
        rr.assert_called_once()
        self.assertEqual(pb.contents(), original)

    def test_paste_refused_when_locked_leaves_clipboard_alone(self):
        original = [[('public.png', b'\x89PNG...')]]
        pb = FakePasteboard(original)
        with self.assertRaises(mr.Fail):
            self.run_paste(pb, locked=True)
        self.assertEqual(pb.contents(), original)

    def test_paste_refused_when_someone_is_at_the_mac_leaves_clipboard_alone(self):
        original = [[('public.png', b'\x89PNG...')]]
        pb = FakePasteboard(original)
        with self.assertRaises(mr.Fail):
            self.run_paste(pb, active=True)
        self.assertEqual(pb.contents(), original)

    # dead-key characters in the password: refuse instead of risking two wrong passwords
    def test_dead_key_password_is_refused_before_typing(self):
        h = mr.Holder(600)
        h.s = FakeSession()
        with mock.patch.object(mr.asyncio, 'sleep', new=mock.AsyncMock()), \
                mock.patch.object(mr, 'is_locked', return_value=True), \
                mock.patch.object(mr, 'layout_has_dead_keys', return_value=True):
            with self.assertRaises(mr.Fail) as e:
                asyncio.run(h.unlock("pa'ss"))
        self.assertEqual(keydowns(h.s.c.writer.data), [])
        self.assertIn('dead key', str(e.exception))


FAKE_SUDO = r'''#!/bin/bash
# Fake sudo: never runs the real one. Logs its arguments (never what it reads), checks the password
# read with -S against $FAKE_SUDO_PASS, keeps a "ticket" file, and runs only tools from this folder.
# Like real sudo (man sudo, -k): `-k` alone drops the ticket; `-k` with -v or a command checks the
# password but does NOT cache a ticket.
D="$FAKE_DIR"; echo "sudo $*" >> "$D/calls"
S=0; K=0; V=0; N=0
while [ $# -gt 0 ]; do
  case "$1" in
    -S) S=1 ;; -k) K=1 ;; -v) V=1 ;; -n) N=1 ;; -p) shift ;;
    -*) ;; *) break ;;
  esac
  shift
done
if [ $K = 1 ] && [ $V = 0 ] && [ $# -eq 0 ]; then rm -f "$D/ticket"; exit 0; fi
ok=0
[ $K = 0 ] && [ -f "$D/ticket" ] && ok=1
if [ $ok = 0 ] && [ $S = 1 ]; then
  IFS= read -r pw
  [ "$pw" = "$FAKE_SUDO_PASS" ] || exit 1
  ok=1
  [ $K = 0 ] && touch "$D/ticket"
fi
[ $ok = 1 ] || exit 1
[ $# -eq 0 ] && exit 0
exec "$(dirname "$0")/$1" "${@:2}"
'''
FAKE_FDESETUP = r'''#!/bin/bash
D="$FAKE_DIR"; echo "fdesetup $*" >> "$D/calls"
case "$1" in
  isactive) echo "${FAKE_FV:-true}" ;;
  supportsauthrestart) echo "${FAKE_AUTHRESTART:-true}" ;;
  list) echo "${FAKE_FV_USERS},11111111-2222-3333-4444-555555555555" ;;
  authrestart) cat > "$D/authrestart.plist"; exit "${FAKE_AUTHRESTART_RC:-0}" ;;
esac
'''
FAKE_SIMPLE = '#!/bin/bash\necho "$(basename "$0") $*" >> "$FAKE_DIR/calls"\necho "${FAKE_OUT:-}" >&2\n'


def read_text(path):
    if not os.path.exists(path):
        return ''
    with open(path) as f:
        return f.read()


class Restart(unittest.TestCase):
    """`restart` with fake fdesetup, sudo, sysadminctl and shutdown first on PATH. Nothing restarts."""
    def setUp(self):
        import pwd
        self.d = tempfile.mkdtemp(prefix='restart-', dir=TMP)
        for name, body in (('sudo', FAKE_SUDO), ('fdesetup', FAKE_FDESETUP),
                           ('sysadminctl', FAKE_SIMPLE), ('shutdown', FAKE_SIMPLE)):
            p = os.path.join(self.d, name)
            with open(p, 'w') as f:
                f.write(body)
            os.chmod(p, 0o755)
        self.env = mock.patch.dict(os.environ, {
            'PATH': self.d + os.pathsep + os.environ.get('PATH', ''), 'FAKE_DIR': self.d,
            'FAKE_SUDO_PASS': SECRET, 'MAC_REMOTE_PASS_CMD': f"printf '{SECRET}\\n'",
            'FAKE_OUT': 'Automatic login user: you'})
        self.env.start()
        self.me = pwd.getpwuid(os.getuid()).pw_name
        os.environ['FAKE_FV_USERS'] = self.me
        for k in ('MAC_REMOTE_PASS', 'MAC_REMOTE_USER', 'MAC_REMOTE_RESTART_PRE_CMD', 'FAKE_FV', 'FAKE_AUTHRESTART'):
            os.environ.pop(k, None)
        write_config('')
        self.idle = mock.patch.object(mr, 'user_active', return_value=(False, 300.0))
        self.idle.start()
        mr.rm(mr.RESTART_LOG)

    def tearDown(self):
        self.idle.stop()
        self.env.stop()

    def run_cmd(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mr.main(['restart', *args])
        return rc, out.getvalue(), err.getvalue()

    def calls(self):
        return read_text(os.path.join(self.d, 'calls'))

    def restarted(self):
        return 'fdesetup authrestart' in self.calls() or 'shutdown -r' in self.calls()

    def assert_no_leak(self, *texts):
        log = read_text(mr.RESTART_LOG)
        for t in texts + (self.calls(), log):
            self.assertNotIn(SECRET, t)

    def test_needs_yes_or_dry_run(self):
        for args in ([], ['--yes', '--dry-run']):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                mr.main(['restart', *args])
            self.assertIn('--yes', err.getvalue())
        self.assertEqual(self.calls(), '')

    def ticket(self):
        return os.path.exists(os.path.join(self.d, 'ticket'))

    def test_validate_leaves_a_ticket_for_sudo_n(self):
        # Real sudo with -k plus -v checks the password but caches no ticket (man sudo), so a later
        # `sudo -n` would fail. sudo_validate must drop the old ticket in a separate call.
        self.assertTrue(mr.sudo_validate(SECRET))
        self.assertTrue(self.ticket())
        self.assertNotIn('-k -v', self.calls())
        self.assertFalse(mr.sudo_validate('wrong'))
        self.assertFalse(self.ticket(), 'a wrong password does not ride on an old ticket')

    def test_dry_run_checks_everything_and_does_not_restart(self):
        rc, out, err = self.run_cmd('--dry-run')
        self.assertEqual(rc, 0, err)
        self.assertIn('FileVault: on; authenticated restart supported', out)
        self.assertIn(f'accepted by sudo for {self.me}', out)
        self.assertIn('dry-run: everything is ready', out)
        self.assertFalse(self.restarted())
        self.assertIn('sudo -k\nsudo -S -p  -v\n', self.calls())
        self.assertIn(f'FileVault user: {self.me} is enabled', out)
        self.assertFalse(self.ticket(), 'sudo ticket dropped after the dry run')
        self.assert_no_leak(out, err)

    def test_yes_with_filevault_uses_authrestart_with_password_on_stdin(self):
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 0, err)
        self.assertIn('sudo -n fdesetup authrestart -inputplist', self.calls())
        with open(os.path.join(self.d, 'authrestart.plist'), 'rb') as f:
            plist = mr.plistlib.load(f)
        self.assertEqual(plist, {'Username': self.me, 'Password': SECRET})
        self.assertNotIn('shutdown', self.calls())
        self.assertTrue(self.calls().endswith('sudo -k\n'), 'ticket dropped at the end')
        self.assert_no_leak(out, err)

    def test_user_not_in_filevault_is_refused(self):
        os.environ['FAKE_FV_USERS'] = 'someone-else'
        for mode in ('--dry-run', '--yes'):
            rc, out, err = self.run_cmd(mode)
            self.assertEqual(rc, 1)
            self.assertIn('is not a FileVault-enabled user', err)
            self.assertFalse(self.restarted())
            self.assertFalse(self.ticket())

    def test_plist_escapes_special_characters(self):
        tricky = 'a<b>&"c'
        os.environ.update(MAC_REMOTE_PASS_CMD=f"printf '%s\\n' '{tricky}'", FAKE_SUDO_PASS=tricky)
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.d, 'authrestart.plist'), 'rb') as f:
            self.assertEqual(mr.plistlib.load(f)['Password'], tricky)

    def test_filevault_off_uses_shutdown(self):
        os.environ['FAKE_FV'] = 'false'
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 0, err)
        self.assertIn('FileVault: off', out)
        self.assertNotIn('warning', out)
        self.assertIn('sudo -n shutdown -r now', self.calls())
        self.assertNotIn('fdesetup authrestart', self.calls())

    def test_filevault_off_without_autologin_warns(self):
        os.environ.update(FAKE_FV='false', FAKE_OUT='Automatic login is off')
        rc, out, err = self.run_cmd('--dry-run')
        self.assertEqual(rc, 0, err)
        self.assertIn('automatic login looks off', out)

    def test_no_authrestart_support_refuses_before_sudo(self):
        os.environ['FAKE_AUTHRESTART'] = 'false'
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 1)
        self.assertIn('does not support an authenticated restart', err)
        self.assertNotIn('sudo', self.calls())

    def test_wrong_password_does_not_restart(self):
        os.environ['FAKE_SUDO_PASS'] = 'something-else'
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 1)
        self.assertIn('sudo did not accept the password', err)
        self.assertFalse(self.restarted())
        self.assert_no_leak(out, err)

    def test_no_password_source_refuses_before_sudo(self):
        os.environ.pop('MAC_REMOTE_PASS_CMD')
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 1)
        self.assertIn('no password source', err)
        self.assertNotIn('sudo', self.calls())

    def test_other_user_is_refused(self):
        os.environ['MAC_REMOTE_USER'] = self.me + '-other'
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 1)
        self.assertIn('sudo needs the password of the user running mac-remote', err)
        self.assertFalse(self.restarted())

    def test_someone_at_the_mac_needs_force(self):
        with mock.patch.object(mr, 'user_active', return_value=(True, 4.0)):
            rc, out, err = self.run_cmd('--yes')
            self.assertEqual(rc, 1)
            self.assertIn('someone used this Mac 4s ago', err)
            self.assertFalse(self.restarted())
            rc, out, err = self.run_cmd('--yes', '--force')
            self.assertEqual(rc, 0, err)
            self.assertTrue(self.restarted())

    def test_someone_arriving_during_the_pre_command_stops_the_restart(self):
        os.environ['MAC_REMOTE_RESTART_PRE_CMD'] = 'true'
        with mock.patch.object(mr, 'user_active', side_effect=[(False, 300.0), (True, 2.0)]):
            rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 1)
        self.assertIn('someone used this Mac 2s ago', err)
        self.assertFalse(self.restarted())
        self.assertFalse(self.ticket(), 'ticket dropped when the second check refuses')
        with mock.patch.object(mr, 'user_active', side_effect=[(False, 300.0), (True, 2.0)]):
            rc, out, err = self.run_cmd('--yes', '--force')
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.restarted())

    def test_pre_command_runs_first_and_its_failure_stops_the_restart(self):
        marker = os.path.join(self.d, 'pre-ran')
        write_config(f'MAC_REMOTE_RESTART_PRE_CMD=touch {marker}\n')
        rc, out, err = self.run_cmd('--dry-run')
        self.assertIn('pre-restart command: would run', out)
        self.assertFalse(os.path.exists(marker), 'dry run does not run the pre-command')
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.exists(marker))
        self.assertTrue(self.restarted())
        os.remove(os.path.join(self.d, 'calls'))
        os.environ['MAC_REMOTE_RESTART_PRE_CMD'] = 'exit 7'
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 1)
        self.assertIn('MAC_REMOTE_RESTART_PRE_CMD failed (exit 7)', err)
        self.assertFalse(self.restarted())
        self.assertFalse(self.ticket())

    def test_pre_command_runs_without_a_sudo_ticket_and_its_stderr_is_logged(self):
        seen = os.path.join(self.d, 'ticket-seen')
        os.environ['MAC_REMOTE_RESTART_PRE_CMD'] = f'test -f {self.d}/ticket && touch {seen}; true'
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(seen), 'no sudo ticket while the pre-command runs')
        self.assertTrue(self.restarted(), 'sudo re-validated after the pre-command')
        os.environ['MAC_REMOTE_RESTART_PRE_CMD'] = 'echo first >&2; echo disk full >&2; exit 3'
        rc, out, err = self.run_cmd('--yes')
        self.assertEqual(rc, 1)
        self.assertIn('first | disk full', read_text(mr.RESTART_LOG))

    def test_failed_restart_command_is_reported(self):
        os.environ['FAKE_AUTHRESTART_RC'] = '2'
        try:
            rc, out, err = self.run_cmd('--yes')
        finally:
            os.environ.pop('FAKE_AUTHRESTART_RC')
        self.assertEqual(rc, 1)
        self.assertIn('the restart command failed (exit 2)', err)
        self.assertFalse(self.ticket(), 'ticket dropped after a failed restart command')


class Cli(unittest.TestCase):
    def test_help(self):
        r = subprocess.run([os.path.join(ROOT, 'bin', 'mac-remote'), '--help'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in ('unlock', 'shot', 'click', 'lock', 'MAC_REMOTE_PASS_CMD'):
            self.assertIn(word, r.stdout)


if __name__ == '__main__':
    print(f'real asyncvnc: {REAL_ASYNCVNC}')
    unittest.main(verbosity=1)
