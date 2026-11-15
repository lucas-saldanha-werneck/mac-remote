#!/usr/bin/env python3
"""Tests for carga-vigia, its hook and mac-remote's heavy-Mac courtesy. Nothing here measures the real Mac,
sends a message or touches the screen: the notice command is a fake script.

Run: python3 tests/test_carga_vigia.py"""
from unittest import mock
import contextlib, importlib.machinery, importlib.util, io, json, os, shutil, stat, subprocess, sys, tempfile, time, types, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix='carga-vigia-test-')
os.environ['CARGA_VIGIA_LOGDIR'] = os.path.join(TMP, 'logs')
os.environ['CARGA_VIGIA_STATE'] = os.path.join(TMP, 'state')
os.environ['CARGA_VIGIA_CONFIG'] = os.path.join(TMP, 'config')
for k in ('CARGA_VIGIA_NOTIFY', 'CARGA_VIGIA_LANG', 'CARGA_VIGIA_SEM_AVISO'):
    os.environ.pop(k, None)
ST = os.environ['CARGA_VIGIA_STATE']
FAKE = os.path.join(TMP, 'fake-avisa')          # records each message it receives, one file per call
SENT = os.path.join(TMP, 'sent')
with open(FAKE, 'w') as f:
    f.write('#!/bin/sh\nmkdir -p "%s"; printf "%%s" "$1" > "%s/$(date +%%s%%N)-$$"\n' % (SENT, SENT))
os.chmod(FAKE, 0o755)

_l = importlib.machinery.SourceFileLoader('carga_vigia', os.path.join(ROOT, 'bin', 'carga-vigia'))
_s = importlib.util.spec_from_loader('carga_vigia', _l)
cv = importlib.util.module_from_spec(_s)
_l.exec_module(cv)

# mac_remote reads its folders at import time: load a private copy under its own module name, with its own
# env, then put the env back, so test_mac_remote.py (same process under `discover`) is not disturbed.
_saved = {k: os.environ.get(k) for k in ('MAC_REMOTE_HOME', 'MAC_REMOTE_CONFIG')}
os.environ['MAC_REMOTE_HOME'] = os.path.join(TMP, 'mr')
os.environ['MAC_REMOTE_CONFIG'] = os.path.join(TMP, 'mrconfig')
os.makedirs(os.environ['MAC_REMOTE_HOME'])
_fake_added = False
try:
    import asyncvnc  # noqa: F401
except ImportError:
    fake = types.ModuleType('asyncvnc')
    fake.key_codes = {}
    sys.modules['asyncvnc'] = fake
    _fake_added = True
_ms = importlib.util.spec_from_file_location('mac_remote_carga', os.path.join(ROOT, 'lib', 'mac_remote.py'))
mr = importlib.util.module_from_spec(_ms)
_ms.loader.exec_module(mr)
if _fake_added:
    del sys.modules['asyncvnc']
for _k, _v in _saved.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v

CFG = cv.load_config()
CFG['NOTIFY_CMD'] = FAKE
HOOK = os.path.join(ROOT, 'hooks', 'carga-hook.py')


def meas(load, t, cpu=None, free=2000, sw=(1000, 6000)):
    return {'ts': t, 't': cv.stamp(t), 'load': [load, load, load], 'mem_free_mb': free, 'swap_used_mb': sw[0],
            'swap_total_mb': sw[1], 'cpu_top': cpu or [{'app': 'Dropbox', 'cpu': 130.0, 'mb': 400, 'n': 2}],
            'mem_top': [{'app': 'Google Chrome', 'mb': 1500, 'n': 9}]}


def _read(p):
    with open(p) as f:
        return f.read()


def _jl(p):
    with open(p) as f:
        return json.load(f)


def sent():
    return sorted(_read(os.path.join(SENT, n)) for n in os.listdir(SENT)) if os.path.isdir(SENT) else []


class Base(unittest.TestCase):
    def setUp(self):
        for d in (ST, SENT, os.environ['CARGA_VIGIA_LOGDIR']):
            shutil.rmtree(d, ignore_errors=True)


class Deteccao(Base):
    def test_three_in_a_row_make_it_heavy(self):
        t = 1_000_000
        self.assertEqual(cv.passo(meas(50, t), CFG), [])
        self.assertEqual(cv.passo(meas(50, t + 60), CFG), [])
        self.assertFalse(os.path.exists(ST + '/pesado'))
        acts = cv.passo(meas(107, t + 120), CFG)
        self.assertEqual([a[0] for a in acts], ['pesado'])
        p = _jl(ST + '/pesado')
        self.assertEqual((p['inicio'], p['carga']), (t + 120, 107))
        self.assertEqual(p['culpados'][0]['app'], 'Dropbox')

    def test_a_dip_resets_the_count(self):
        t = 1_000_000
        for i, l in enumerate((50, 50, 30, 50, 50)):
            cv.passo(meas(l, t + 60 * i), CFG)
        self.assertFalse(os.path.exists(ST + '/pesado'))
        cv.passo(meas(50, t + 300), CFG)
        self.assertTrue(os.path.exists(ST + '/pesado'))

    def test_exactly_40_is_not_above(self):
        for i in range(5):
            cv.passo(meas(40, 1_000_000 + 60 * i), CFG)
        self.assertFalse(os.path.exists(ST + '/pesado'))

    def test_thresholds_come_from_the_config(self):
        cfg = dict(CFG, LOAD_ON=10.0, SAMPLES=1)
        self.assertEqual(len(cv.passo(meas(11, 1_000_000), cfg)), 1)
        with open(os.environ['CARGA_VIGIA_CONFIG'], 'w') as f:
            f.write('LOAD_ON=12\nLOAD_OFF=3\nSAMPLES=2\nLANG=pt\n')
        try:
            c = cv.load_config()
            self.assertEqual((c['LOAD_ON'], c['LOAD_OFF'], c['SAMPLES'], c['LANG']), (12.0, 3.0, 2, 'pt'))
        finally:
            os.remove(os.environ['CARGA_VIGIA_CONFIG'])
        self.assertEqual((CFG['LOAD_ON'], CFG['LOAD_OFF'], CFG['SAMPLES'], CFG['ALERT_EVERY']), (40.0, 25.0, 3, 1800))


class Histerese(Base):
    def heavy(self, t=1_000_000):
        for i in range(3):
            acts = cv.passo(meas(80, t + 60 * i), CFG)
        return acts

    def test_between_25_and_40_keeps_it_heavy(self):
        self.heavy()
        for i in range(6):
            cv.passo(meas(30, 2_000_000 + 60 * i), CFG)
        self.assertTrue(os.path.exists(ST + '/pesado'))

    def test_three_below_25_clear_it_and_a_dip_resets(self):
        self.heavy()
        cv.passo(meas(20, 2_000_000), CFG)
        cv.passo(meas(20, 2_000_060), CFG)
        cv.passo(meas(30, 2_000_120), CFG)       # not below 25: the count restarts
        cv.passo(meas(20, 2_000_180), CFG)
        cv.passo(meas(20, 2_000_240), CFG)
        self.assertTrue(os.path.exists(ST + '/pesado'))
        acts = cv.passo(meas(20, 2_000_300), CFG)
        self.assertEqual([a[0] for a in acts], ['normal'])
        self.assertFalse(os.path.exists(ST + '/pesado'))

    def test_peak_is_tracked(self):
        self.heavy()
        cv.passo(meas(147, 2_000_000), CFG)
        cv.passo(meas(60, 2_000_060), CFG)
        self.assertEqual(_jl(ST + '/pesado')['pico'], 147)


class Aviso(Base):
    def info(self, inicio=1_000_000, fim=None, **kw):
        d = {'inicio': inicio, 'carga': 107, 'pico': 120, 'culpados': [{'app': 'Dropbox', 'info': '130% CPU'}, {'app': 'Google Chrome', 'info': '1.5 GB'}]}
        if fim:
            d['fim'] = fim
        return dict(d, **kw)

    def test_notice_text_pt_is_three_lines(self):
        cfg = dict(CFG, LANG='pt')
        self.assertTrue(cv.avisa('pesado', self.info(), cfg, now=1_000_000))
        (m,) = sent()
        self.assertEqual(m.split('\n'), ['Mac pesado: carga 107.', 'Quem pesa: Dropbox (130% CPU), Google Chrome (1.5 GB).',
                                        'Robôs vão perguntar antes de agir.'])

    def test_english_by_default(self):
        cv.avisa('pesado', self.info(), CFG, now=1_000_000)
        self.assertTrue(sent()[0].startswith('Heavy Mac: load 107.'))

    def test_cooldown_30_min(self):
        self.assertTrue(cv.avisa('pesado', self.info(), CFG, now=1_000_000))
        self.assertFalse(cv.avisa('pesado', self.info(), CFG, now=1_000_000 + 1799))
        self.assertTrue(cv.avisa('pesado', self.info(), CFG, now=1_000_000 + 1801))
        self.assertEqual(len(sent()), 2)

    def test_failed_send_is_retried_after_5_min_not_at_once(self):
        cfg = dict(CFG, NOTIFY_CMD='/usr/bin/false')
        self.assertFalse(cv.avisa('pesado', self.info(), cfg, now=1_000_000))
        self.assertFalse(cv.avisa('pesado', self.info(), CFG, now=1_000_100))      # inside the 5 min
        self.assertTrue(cv.avisa('pesado', self.info(), CFG, now=1_000_400))

    def test_back_to_normal_only_after_10_min(self):
        cfg = dict(CFG, LANG='pt')
        self.assertFalse(cv.avisa('normal', self.info(fim=1_000_000 + 600), cfg))   # exactly 10 min: not more
        self.assertEqual(sent(), [])
        self.assertTrue(cv.avisa('normal', self.info(fim=1_000_000 + 1200), cfg))
        self.assertEqual(sent(), ['Mac voltou ao normal depois de 20 min (pico de carga 120).'])

    def test_full_cycle_one_alert_and_one_recovery(self):
        t = 3_000_000
        for i in range(3):
            for tipo, info in cv.passo(meas(90, t + 60 * i), CFG):
                cv.avisa(tipo, info, CFG, now=t + 60 * i)
        for i in range(3, 20):                       # stays heavy for ~20 min: no second notice
            cv.passo(meas(90, t + 60 * i), CFG)
        for i in range(20, 23):
            for tipo, info in cv.passo(meas(10, t + 60 * i), CFG):
                cv.avisa(tipo, info, CFG, now=t + 60 * i)
        self.assertEqual(sorted(m[:20] for m in sent()), ['Heavy Mac: load 90.\n', 'Mac back to normal a'])
        self.assertEqual(len(sent()), 2)


class Agrupamento(unittest.TestCase):
    PS = """ 90.0 200000 /Applications/Dropbox.app/Contents/MacOS/Dropbox
 40.0 100000 /Applications/Dropbox.app/Contents/PlugIns/DropboxFileProvider.appex/Contents/MacOS/DropboxFileProvider
 25.0  30000 /System/Library/CoreServices/fileproviderd
 12.0  20000 /System/Library/PrivateFrameworks/FPCKService.framework/FPCKService
 30.0 300000 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
 20.0 150000 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper
  5.0  50000 node
  6.0  60000 node
"""

    def test_apps_are_grouped_and_daemons_follow_a_high_dropbox(self):
        apps = cv.agrupa(cv.parse_ps(self.PS))
        self.assertAlmostEqual(apps['Dropbox']['cpu'], 90 + 40 + 25 + 12)
        self.assertEqual(apps['Dropbox']['n'], 4)
        self.assertNotIn('fileproviderd', apps)
        self.assertEqual(apps['Google Chrome']['n'], 2)
        self.assertAlmostEqual(apps['Google Chrome']['cpu'], 50)
        self.assertEqual((apps['node']['n'], apps['node']['cpu']), (2, 11.0))
        self.assertEqual(cv.top(apps, 'cpu')[0]['app'], 'Dropbox')
        self.assertEqual(cv.top(apps, 'mb', 1)[0]['app'], 'Google Chrome')

    def test_daemon_stays_apart_when_dropbox_is_low_or_daemon_is_low(self):
        low = cv.agrupa(cv.parse_ps(" 5.0 1000 /Applications/Dropbox.app/Contents/MacOS/Dropbox\n 80.0 1000 /System/x/fileproviderd\n"))
        self.assertIn('fileproviderd', low)
        self.assertEqual(low['Dropbox']['cpu'], 5.0)
        quiet = cv.agrupa(cv.parse_ps(" 90.0 1000 /Applications/Dropbox.app/Contents/MacOS/Dropbox\n 2.0 1000 /System/x/fileproviderd\n"))
        self.assertIn('fileproviderd', quiet)

    def test_top_limited_to_five(self):
        apps = cv.agrupa(cv.parse_ps("".join(" %d.0 100 /usr/bin/p%d\n" % (i, i) for i in range(1, 10))))
        self.assertEqual(len(cv.top(apps, 'cpu')), 5)

    def test_culprits_summary(self):
        m = meas(100, 1, cpu=[{'app': 'Dropbox', 'cpu': 130.0, 'mb': 400, 'n': 2}, {'app': 'node', 'cpu': 5.0, 'mb': 90, 'n': 27}], free=159, sw=(7900, 9000))
        self.assertEqual(cv.culpados(m), [('Dropbox', '130% CPU'), ('Google Chrome', '1.5 GB')])
        m['mem_free_mb'], m['swap_used_mb'] = 3000, 100
        self.assertEqual(cv.culpados(m), [('Dropbox', '130% CPU')])


class Registro(Base):
    def test_pruning_keeps_7_days(self):
        os.makedirs(os.environ['CARGA_VIGIA_LOGDIR'])
        now = time.time()
        with open(os.environ['CARGA_VIGIA_LOGDIR'] + '/carga.jsonl', 'w') as f:
            f.write(json.dumps({'ts': now - 8 * 86400}) + '\n' + json.dumps({'ts': now - 6 * 86400}) + '\n')
        cv.poda(CFG, now)
        self.assertEqual(len(_read(os.environ['CARGA_VIGIA_LOGDIR'] + '/carga.jsonl').splitlines()), 1)

    def test_real_measurement_has_the_fields(self):
        m = cv.coleta()
        for k in ('t', 'load', 'mem_free_mb', 'swap_used_mb', 'swap_total_mb', 'cpu_top', 'mem_top'):
            self.assertIn(k, m)
        self.assertEqual(len(m['load']), 3)
        self.assertLessEqual(len(m['cpu_top']), 5)


class Hook(Base):
    def run_hook(self, sid='s1', payload=None):
        p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload or {'session_id': sid}).encode(),
                           capture_output=True, env=dict(os.environ))
        self.assertEqual(p.returncode, 0)
        return p.stdout.decode()

    def heavy(self, inicio):
        os.makedirs(ST, exist_ok=True)
        with open(ST + '/pesado', 'w') as f:
            json.dump({'inicio': inicio, 'carga': 107, 'culpados': [{'app': 'Dropbox', 'info': '130% CPU'}]}, f)

    def test_silent_when_not_heavy(self):
        self.assertEqual(self.run_hook(), '')

    def test_context_once_per_session_and_episode(self):
        t0 = 1_700_000_000
        self.heavy(t0)
        out = json.loads(self.run_hook('s1'))['hookSpecificOutput']
        self.assertEqual(out['hookEventName'], 'PreToolUse')
        self.assertIn('Mac pesado (carga 107, culpado Dropbox (130% CPU) desde ', out['additionalContext'])
        self.assertIn('AskUserQuestion', out['additionalContext'])
        self.assertNotIn('permissionDecision', json.dumps(out))      # never blocks
        self.assertEqual(self.run_hook('s1'), '')                     # same session, same episode
        self.assertNotEqual(self.run_hook('s2'), '')                  # another session is told
        self.heavy(t0 + 5000)                                         # new episode: told again
        self.assertNotEqual(self.run_hook('s1'), '')
        self.assertEqual(self.run_hook('s1'), '')

    def test_garbage_stdin_never_fails(self):
        self.heavy(1_700_000_000)
        p = subprocess.run([sys.executable, HOOK], input=b'not json', capture_output=True, env=dict(os.environ))
        self.assertEqual(p.returncode, 0)

    def test_fast_enough(self):
        self.heavy(1_700_000_000)
        self.run_hook('warm')
        # the 50 ms target is for the hook's own work (Python start-up aside), so time it in-process
        spec = importlib.util.spec_from_file_location('carga_hook', HOOK)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.heavy(1_700_000_001)
        sys.stdin = io.StringIO(json.dumps({'session_id': 'fast'}))
        t = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            hook.main()
        sys.stdin = sys.__stdin__
        self.assertLess(time.perf_counter() - t, 0.05)

    def test_hooks_json_is_valid(self):
        d = _jl(os.path.join(ROOT, 'hooks', 'hooks.json'))
        (g,) = d['hooks']['PreToolUse']
        self.assertTrue(g['matcher'].startswith('Bash'))
        self.assertIn('carga-hook.py', g['hooks'][0]['command'])


class MacRemote(Base):
    def args(self, wait=None):
        return types.SimpleNamespace(wait_load=wait)

    def heavy(self):
        os.makedirs(ST, exist_ok=True)
        with open(ST + '/pesado', 'w') as f:
            json.dump({'inicio': 1_700_000_000, 'carga': 90, 'culpados': [{'app': 'Dropbox', 'info': 'x'}]}, f)

    def test_no_file_no_output(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            mr.espera_carga(self.args())
        self.assertEqual(err.getvalue(), '')

    def test_warns_in_one_line_and_goes_on(self):
        self.heavy()
        err, calls = io.StringIO(), []
        with contextlib.redirect_stderr(err):
            mr.espera_carga(self.args(), sleep=calls.append)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn('heavy Mac (load 90, Dropbox', err.getvalue())
        self.assertEqual(calls, [])

    def test_wait_ends_when_the_file_goes_away(self):
        self.heavy()
        clock = {'t': 0.0}

        def sleep(s):
            clock['t'] += s
            if clock['t'] >= 15:
                os.remove(ST + '/pesado')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            mr.espera_carga(self.args(300), sleep=sleep, now=lambda: clock['t'])
        self.assertEqual(clock['t'], 15)
        self.assertNotIn('still heavy', err.getvalue())

    def test_wait_gives_up_at_the_limit_and_goes_on(self):
        self.heavy()
        clock = {'t': 0.0}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            mr.espera_carga(self.args(12), sleep=lambda s: clock.update(t=clock['t'] + s), now=lambda: clock['t'])
        self.assertEqual(clock['t'], 12)
        self.assertIn('still heavy after the wait; going on', err.getvalue())

    def test_option_parses_with_and_without_value(self):
        for argv, want in ((['shot'], None), (['shot', '--wait-load'], 300), (['shot', '--wait-load', '20'], 20)):
            with mock.patch.object(mr, 'cmd_shot', return_value=0) as c, mock.patch.object(mr, 'espera_carga') as e:
                self.assertEqual(mr.main(argv), 0)
                self.assertEqual(getattr(e.call_args[0][0], 'wait_load', None), want)

    def test_lock_and_status_do_not_wait(self):
        with mock.patch.object(mr, 'cmd_lock', return_value=0), mock.patch.object(mr, 'espera_carga') as e:
            mr.main(['lock'])
        e.assert_not_called()


if __name__ == '__main__':
    unittest.main()
