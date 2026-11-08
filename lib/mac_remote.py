#!/usr/bin/env python3
"""mac-remote: see and control this Mac through its own Screen Sharing (VNC),
including when the screen is locked.

Key fact: macOS re-locks the screen about 1 s after the VNC session that unlocked it
disconnects. So `unlock` starts a detached HOLDER process that keeps ONE VNC connection
open and serves shot/click/move/type/keys over a Unix socket (mode 600, same uid only).

The Mac password comes from MAC_REMOTE_PASS_CMD (a command that prints it, e.g. a
password manager or the macOS Keychain) or from the MAC_REMOTE_PASS environment variable.
It is fetched only when a new VNC login is needed, and is never printed, logged, written
to disk, pasted, or put on a command line."""
import argparse, asyncio, fcntl, getpass, json, os, re, signal, socket, struct, subprocess, sys, time

HOME = os.path.expanduser(os.environ.get('MAC_REMOTE_HOME') or os.path.join(
    os.environ.get('XDG_DATA_HOME') or '~/.local/share', 'mac-remote'))
CONFIG = os.path.expanduser(os.environ.get('MAC_REMOTE_CONFIG') or os.path.join(
    os.environ.get('XDG_CONFIG_HOME') or '~/.config', 'mac-remote', 'config'))
STATE = os.path.join(HOME, 'state.json')            # {"last_input": t} of OUR injected input
LAST_SHOT = os.path.join(HOME, 'last-shot.json')
SHOT_DIR = os.path.join(HOME, 'shots')
SOCK = os.path.join(HOME, 'holder.sock')
HOLDER_JSON = os.path.join(HOME, 'holder.json')
HOLDER_LOG = os.path.join(HOME, 'holder.log')
HOLDER_LOCK = os.path.join(HOME, 'holder.lock')
HOST, PORT = '127.0.0.1', 5900   # local Screen Sharing only, on purpose
PASS_CMD_TIMEOUT = 120    # seconds; a password manager may wait for Touch ID
ACTIVE_SECS = 20          # local input newer than this = someone is at the Mac
DEFAULT_IDLE = 600        # holder exits (-> macOS re-locks) after this many idle seconds
MAX_SIDE = 1600
CLOSE_GRACE = 1.0
KEY_GAP = 0.06
KEY_HOLD = 0.04
WAKE_WAIT = 1.0
REFRESH_PERIOD = 0.4

KEYMAP = {'cmd': 'Super_L', 'command': 'Super_L', 'super': 'Super_L',
          'opt': 'Alt_L', 'option': 'Alt_L', 'alt': 'Alt_L',
          'ctrl': 'Control_L', 'control': 'Control_L', 'shift': 'Shift_L',
          'return': 'Return', 'enter': 'Return', 'esc': 'Escape', 'escape': 'Escape',
          'tab': 'Tab', 'space': 'space', 'delete': 'BackSpace', 'backspace': 'BackSpace',
          'del': 'Delete', 'fwddelete': 'Delete', 'up': 'Up', 'down': 'Down',
          'left': 'Left', 'right': 'Right', 'home': 'Home', 'end': 'End',
          'pageup': 'Page_Up', 'pagedown': 'Page_Down'}


class Fail(Exception):
    pass


# ---------- local state (no credentials needed) ----------
def is_locked():
    import Quartz
    d = Quartz.CGSessionCopyCurrentDictionary() or {}
    return bool(d.get('CGSSessionScreenIsLocked'))


def hid_idle():
    out = subprocess.run(['ioreg', '-c', 'IOHIDSystem'], capture_output=True, text=True).stdout
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) / 1e9 if m else None


def write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def rm(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def our_last_input():
    st = read_json(STATE) or {}
    return st.get('last_input', 0)


def mark_our_input():
    write_json(STATE, {'last_input': time.time()})


def local_input_newer_than_ours(idle):
    """True if the latest HID event is newer than our latest injected input (2 s slack)."""
    return idle < time.time() - our_last_input() - 2


def user_active():
    """(True, idle) if there was local input that was NOT ours in the last ACTIVE_SECS."""
    idle = hid_idle()
    if idle is None or idle >= ACTIVE_SECS:
        return False, idle
    return local_input_newer_than_ours(idle), idle


def display_rects():
    try:
        import Quartz
        err, ids, n = Quartz.CGGetActiveDisplayList(16, None, None)
        res = []
        for i in ids[:n]:
            m = Quartz.CGDisplayCopyDisplayMode(i)
            b = Quartz.CGDisplayBounds(i)
            res.append({'id': int(i), 'main': bool(Quartz.CGDisplayIsMain(i)),
                        'points': [int(b.origin.x), int(b.origin.y), int(b.size.width), int(b.size.height)],
                        'pixels': [int(Quartz.CGDisplayModeGetPixelWidth(m)), int(Quartz.CGDisplayModeGetPixelHeight(m))]})
        return res
    except Exception as e:
        return [{'error': str(e)}]


def frontmost():
    try:
        asn = subprocess.run(['lsappinfo', 'front'], capture_output=True, text=True, timeout=3).stdout.strip()
        out = subprocess.run(['lsappinfo', 'info', '-only', 'name', asn], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r'"LSDisplayName"="([^"]*)"', out)
        return m.group(1) if m else None
    except Exception:
        return None


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


# ---------- configuration and credentials ----------
CONFIG_KEYS = ('MAC_REMOTE_USER', 'MAC_REMOTE_PASS_CMD')   # MAC_REMOTE_PASS is env-only, never a file


def load_config(path=None):
    """KEY=VALUE lines from the config file. Environment variables win over the file."""
    cfg = {}
    try:
        with open(path or CONFIG) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                    v = v[1:-1]
                if k in CONFIG_KEYS:
                    cfg[k] = v
    except FileNotFoundError:
        pass
    for k in CONFIG_KEYS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    return cfg


def mac_user():
    return load_config().get('MAC_REMOTE_USER') or getpass.getuser()


def pass_source():
    """Where the password would come from, without fetching it: 'env', 'command' or None."""
    if os.environ.get('MAC_REMOTE_PASS'):
        return 'env'
    if load_config().get('MAC_REMOTE_PASS_CMD'):
        return 'command'
    return None


def get_password():
    """Return the Mac password. Never print it, log it or pass it on a command line.
    The command string itself holds no secret; its stdout is captured, its stderr is discarded
    so nothing it prints can reach Claude's transcript."""
    p = os.environ.get('MAC_REMOTE_PASS')
    if p:
        return p
    cmd = load_config().get('MAC_REMOTE_PASS_CMD')
    if not cmd:
        raise Fail('no password source: set MAC_REMOTE_PASS_CMD (in the environment or in '
                   f'{CONFIG}) to a command that prints the Mac password, or set MAC_REMOTE_PASS')
    try:
        r = subprocess.run(cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=PASS_CMD_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise Fail(f'MAC_REMOTE_PASS_CMD did not finish within {PASS_CMD_TIMEOUT}s')
    if r.returncode != 0:
        raise Fail(f'MAC_REMOTE_PASS_CMD failed (exit {r.returncode}); run it yourself in a terminal '
                   'to see why (its output is hidden here on purpose)')
    p = r.stdout.decode('utf-8', 'replace').rstrip('\r\n')
    if not p:
        raise Fail('MAC_REMOTE_PASS_CMD printed nothing')
    return p


def creds():
    return mac_user(), get_password()


# ---------- VNC ----------
async def connect(cred=None):
    import asyncvnc
    u, p = cred or creds()
    last = None
    # The server sometimes drops a login (IncompleteReadError after it sat idle);
    # retry a few times, but never on PermissionError (wrong password -> avoid account lockout).
    for attempt in range(4):
        w = None
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(HOST, PORT, limit=2**22), 5)
            return await asyncio.wait_for(asyncvnc.Client.create(r, w, u, p, None), 12)
        except PermissionError as e:
            raise Fail(f'VNC login refused: {e} (check MAC_REMOTE_USER and the password source; '
                       'do not retry in a loop, failed logins can lock the account)')
        except Exception as e:
            last = e
            print(f'(VNC login attempt {attempt+1} failed: {type(e).__name__}; retrying)', file=sys.stderr, flush=True)
            if w is not None:
                try: w.close()
                except Exception: pass
            await asyncio.sleep(1.5)
    raise Fail(f'VNC connect/login failed after 4 tries: {type(last).__name__} (is Screen Sharing on at {HOST}:{PORT}?)')


async def _read_int(reader, n):
    return int.from_bytes(await reader.readexactly(n), 'big')


class Session:
    """One VNC connection with a single never-cancelled reader task and a refresher that keeps
    asking for incremental framebuffer updates. Input only lands reliably while the client is
    receiving updates. The reader parses server messages itself because
    asyncvnc swaps RFB types 2 (Bell) and 3 (ServerCutText), which would desync a long session."""

    def __init__(self, c):
        self.c = c
        self.updates = 0
        self.last_update = 0.0
        self.dead = None
        self.evt = asyncio.Event()
        self.tasks = []

    def start(self):
        self.tasks = [asyncio.ensure_future(self._reader()), asyncio.ensure_future(self._refresher())]
        return self

    async def _reader(self):
        r = self.c.reader
        try:
            while True:
                t = await _read_int(r, 1)
                if t == 0:                                   # FramebufferUpdate
                    await r.readexactly(1)
                    for _ in range(await _read_int(r, 2)):
                        await self.c.video.read()
                    self.updates += 1
                    self.last_update = time.time()
                    self.evt.set()
                elif t == 1:                                 # SetColourMapEntries
                    await r.readexactly(3)
                    n = await _read_int(r, 2)
                    await r.readexactly(6 * n)
                elif t == 2:                                 # Bell
                    pass
                elif t == 3:                                 # ServerCutText (clipboard) - discarded
                    await r.readexactly(3)
                    await r.readexactly(await _read_int(r, 4))
                else:
                    raise ValueError(f'unknown server message type {t}')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.dead = f'{type(e).__name__}: {e}'[:200] or 'closed'

    async def _refresher(self):
        while self.dead is None:
            try:
                self.c.video.refresh()
                await self.c.drain()
            except Exception as e:
                self.dead = f'{type(e).__name__}: {e}'[:200]
                return
            self.evt.clear()
            try:
                await asyncio.wait_for(self.evt.wait(), 2.0)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(REFRESH_PERIOD)

    def check(self):
        if self.dead:
            raise Fail(f'VNC connection dropped ({self.dead})')

    async def settle(self, quiet=0.5, maxt=4.0, first=False):
        """Wait until the framebuffer is current: an update arrived and then went quiet. An
        incremental request is only answered when something changed, so with a full frame
        already in memory, 'no update within 1 s' means the screen is unchanged."""
        start = time.time()
        n0 = self.updates
        self.c.video.refresh()
        await self.c.drain()
        limit = 8.0 if first else maxt
        while time.time() - start < limit:
            self.check()
            have = self.c.video.data is not None
            if self.updates > n0 and have and time.time() - self.last_update > quiet:
                break
            if self.updates == n0 and have and not first and time.time() - start > 1.0:
                break
            await asyncio.sleep(0.05)
        self.check()

    def pixels(self):
        v = self.c.video
        return v.as_rgba().copy(), [(s.x, s.y, s.width, s.height) for s in v.detect_screens()]

    async def wake(self):
        """One-off sessions: full frame, Shift tap, short wait (first keys of a session got lost)."""
        await self.settle(first=True)
        await press(self.c, 'Shift_L')
        await asyncio.sleep(WAKE_WAIT)

    async def close(self, grace=CLOSE_GRACE):
        try:
            await self.c.drain()
            if grace:
                await asyncio.sleep(grace)   # let the server apply the last events before hanging up
        except Exception:
            pass
        try:
            self.c.writer.close()
        except Exception:
            pass
        for t in self.tasks:
            t.cancel()


async def open_session(first=True, cred=None):
    s = Session(await connect(cred)).start()
    if first:
        await s.settle(first=True)
    return s


async def press(c, *keys):
    """Key down(s), short hold, key up(s). Back-to-back down/up (asyncvnc's press) gets dropped."""
    import asyncvnc
    codes = [asyncvnc.key_codes[k].to_bytes(4, 'big') for k in keys]
    for code in codes:
        c.writer.write(b'\x04\x01\x00\x00' + code)
    await c.drain()
    await asyncio.sleep(KEY_HOLD)
    for code in reversed(codes):
        c.writer.write(b'\x04\x00\x00\x00' + code)
    await c.drain()


async def mouse_click(c, button=0):
    with c.mouse.hold(button):
        await c.drain()
        await asyncio.sleep(KEY_HOLD)
    await c.drain()


async def type_password(s, pw):
    c = s.c
    await press(c, 'Shift_L')                    # wake the lock screen (proven)
    await asyncio.sleep(1.5)
    for _ in range(len(pw) + 8):                 # clear leftovers of an earlier, partial attempt
        await press(c, 'BackSpace')
        await asyncio.sleep(0.03)
    await asyncio.sleep(0.4)
    for ch in pw:
        await press(c, ch)
        await asyncio.sleep(KEY_GAP)
    await asyncio.sleep(0.4)
    await press(c, 'Return')


# ---------- shot rendering ----------
def render_shot(px, screens, path, display):
    from PIL import Image
    img = Image.fromarray(px).convert('RGB')
    fbw, fbh = img.size
    ox, oy = 0, 0
    if display is not None:
        if not (0 <= display < len(screens)):
            raise Fail(f'--display {display}: only {len(screens)} display(s) found: {screens}')
        x, y, w, h = screens[display]
        img = img.crop((x, y, x + w, y + h)); ox, oy = x, y
    elif screens:  # crop to the union of displays (drops empty framebuffer margins)
        x0 = min(s[0] for s in screens); y0 = min(s[1] for s in screens)
        x1 = max(s[0] + s[2] for s in screens); y1 = max(s[1] + s[3] for s in screens)
        img = img.crop((x0, y0, x1, y1)); ox, oy = x0, y0
    scale = min(1.0, MAX_SIDE / max(img.size))
    if scale < 1:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.abspath(path) if path else os.path.join(SHOT_DIR, time.strftime('shot-%Y%m%d-%H%M%S.jpg'))
    img.save(path, 'JPEG', quality=82)
    meta = {'path': path, 'scale': scale, 'offset': [ox, oy], 'image_size': list(img.size),
            'framebuffer': [fbw, fbh], 'displays_fb': screens, 'display': display, 'time': time.time(),
            'locked': is_locked()}
    with open(LAST_SHOT, 'w') as f:
        json.dump(meta, f)
    lines = [path, f"image {img.width}x{img.height}, scale {scale:.4f}, offset {ox},{oy}; locked: {'yes' if meta['locked'] else 'no'}"]
    for i, s in enumerate(screens):
        lines.append(f'display {i}: framebuffer rect x={s[0]} y={s[1]} w={s[2]} h={s[3]}'
                     + (f' -> image rect x={round((s[0]-ox)*scale)} y={round((s[1]-oy)*scale)} w={round(s[2]*scale)} h={round(s[3]*scale)}'
                        if display is None else ''))
    lines.append("click X Y uses this image's pixel coordinates")
    return '\n'.join(lines)


# ---------- actions shared by the holder and one-off sessions ----------
def prepare_input(r):
    if is_locked():
        raise Fail('screen is LOCKED: refusing to send input. Run `mac-remote unlock` first.')
    act, idle = user_active()
    if act and not r.get('force'):
        raise Fail(f'someone seems to be using the Mac (last local input {idle:.0f}s ago). '
                   'Refusing so we do not fight over mouse/keyboard. Use --force only if they agreed.')
    app = r.get('app')
    if app:
        subprocess.run(['osascript', '-e', f'tell application "{app}" to activate'], capture_output=True, timeout=10)
        for _ in range(20):
            if frontmost() == app:
                break
            time.sleep(0.1)
        if frontmost() != app:
            raise Fail(f'--app {app}: could not bring it to the front (frontmost is {frontmost()!r}); nothing sent')


async def do_input(s, r, one_off):
    prepare_input(r)
    if one_off:
        await s.wake()
    else:
        await s.settle(maxt=1.5)
    if r.get('app') and frontmost() != r['app']:
        raise Fail(f"--app {r['app']}: lost the front to {frontmost()!r}; nothing sent")
    front = frontmost()
    c = s.c
    k = r['cmd']
    if k in ('click', 'move'):
        x, y, action = r['x'], r['y'], r.get('action', 'move')
        c.mouse.move(x, y)
        await c.drain()
        await asyncio.sleep(0.15)
        if action == 'click':
            await mouse_click(c)
        elif action == 'right':
            await mouse_click(c, 2)
        elif action == 'double':
            await mouse_click(c); await asyncio.sleep(0.06); await mouse_click(c)
        msg = f'{action} at framebuffer ({x},{y})' if action != 'move' else f'moved to framebuffer ({x},{y})'
    elif k == 'keys':
        for keys in r['combos']:
            await press(c, *keys)
            await asyncio.sleep(0.15)
        msg = 'sent: ' + ', '.join('+'.join(ks) for ks in r['combos'])
    elif k == 'type':
        for key in r['keys']:
            await press(c, key)
            await asyncio.sleep(KEY_GAP)
        msg = 'typed text (length not shown)'
    else:
        raise Fail(f'unknown input command {k}')
    await c.drain()
    if not one_off:
        await asyncio.sleep(0.3)      # let the server apply the last events
    mark_our_input()
    s.check()
    return msg + f'; frontmost app: {front}'


async def do_shot(s, r):
    await s.settle()
    px, screens = s.pixels()
    return render_shot(px, screens, r.get('path'), r.get('display'))


# ---------- holder (detached process that keeps the session) ----------
def peer_uid(sock):
    """macOS LOCAL_PEERCRED -> struct xucred {u_int version; uid_t uid; short ngroups; gid_t groups[16]}."""
    try:
        buf = sock.getsockopt(0, 0x001, 76)       # SOL_LOCAL, LOCAL_PEERCRED, sizeof(struct xucred)
        return struct.unpack_from('Ii', buf)[1]
    except Exception:
        return None


class Holder:
    def __init__(self, idle_timeout):
        self.idle_timeout = float(idle_timeout)
        self.pid = os.getpid()
        self.started_at = time.time()
        self.phase = 'connecting'
        self.error = None
        self.held_at = None
        self.last_command_at = None
        self.last_local_at = None       # last local (not ours) input seen while holding
        self.local_active = False
        self.stop_reason = None
        self.stop_evt = asyncio.Event()
        self.req_lock = asyncio.Lock()
        self.s = None

    def log(self, m):
        print(f"{time.strftime('%F %T')} [holder {self.pid}] {m}", flush=True)

    def info(self):
        now = time.time()
        ref = self.idle_ref()
        return {'pid': self.pid, 'phase': self.phase, 'error': self.error, 'started_at': self.started_at,
                'held_at': self.held_at, 'last_command_at': self.last_command_at,
                'last_local_input_at': self.last_local_at, 'local_active': self.local_active,
                'idle_timeout': self.idle_timeout,
                'idle_for': (now - ref) if ref else None,
                'exits_in': (self.idle_timeout - (now - ref)) if ref else None}

    def idle_ref(self):
        vals = [v for v in (self.held_at, self.last_command_at, self.last_local_at) if v]
        return max(vals) if vals else None

    def save(self):
        write_json(HOLDER_JSON, self.info())

    def stop(self, reason):
        if not self.stop_reason:
            self.stop_reason = reason
        self.stop_evt.set()

    async def handle(self, reader, writer):
        try:
            uid = peer_uid(writer.get_extra_info('socket'))
            if uid != os.getuid():
                self.log(f'rejected a socket client with uid {uid}')
                return
            line = await asyncio.wait_for(reader.readline(), 10)
            r = json.loads(line)
            cmd = r.get('cmd')
            try:
                if cmd == 'ping':
                    resp = {'ok': True, 'info': self.info()}
                elif cmd == 'config':
                    self.idle_timeout = float(r['idle_timeout'])
                    self.last_command_at = time.time()
                    self.log(f'idle-timeout set to {self.idle_timeout:.0f}s')
                    self.save()
                    resp = {'ok': True, 'info': self.info()}
                elif cmd == 'stop':
                    self.stop(r.get('reason', 'stop requested'))
                    resp = {'ok': True}
                elif cmd in ('shot', 'click', 'move', 'keys', 'type'):
                    if self.phase != 'held':
                        raise Fail(f'holder is not ready (phase {self.phase})')
                    async with self.req_lock:
                        self.last_command_at = time.time()
                        out = await (do_shot(self.s, r) if cmd == 'shot' else do_input(self.s, r, one_off=False))
                        self.last_command_at = time.time()
                        self.save()
                    self.log(f'{cmd} ok')
                    resp = {'ok': True, 'out': out}
                else:
                    raise Fail(f'unknown request {cmd!r}')
            except Fail as e:
                resp = {'ok': False, 'error': str(e)}
                self.log(f'{cmd} refused: {e}')
            except Exception as e:
                resp = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
                self.log(f'{cmd} error: {type(e).__name__}: {e}')
            writer.write((json.dumps(resp) + '\n').encode())
            await writer.drain()
        except Exception as e:
            self.log(f'socket client error: {type(e).__name__}')
        finally:
            try: writer.close()
            except Exception: pass

    async def unlock(self, pw):
        import asyncvnc
        if any(ch not in asyncvnc.key_codes for ch in pw):
            raise Fail('the Mac password has a character VNC cannot type; unlock not attempted')
        for attempt in (1, 2):
            self.phase = f'unlocking (try {attempt})'
            self.log(self.phase)
            await type_password(self.s, pw)
            for _ in range(20):
                await asyncio.sleep(0.5)
                self.s.check()
                if not is_locked():
                    return
            self.log(f'still locked after try {attempt}')
        raise Fail('typed the password twice but the screen is still locked (wrong password? lock screen not focused?)')

    async def watch(self):
        """Safety loop: exit (-> macOS re-locks) on idle timeout, dropped VNC, or screen locked."""
        last_save = 0
        while not self.stop_evt.is_set():
            await asyncio.sleep(1.0)
            if self.s.dead:
                self.stop(f'VNC connection dropped ({self.s.dead})'); break
            if is_locked():
                self.stop('screen got locked by something else (local lock / sleep)'); break
            now = time.time()
            idle = hid_idle()
            if idle is not None and local_input_newer_than_ours(idle) and (not self.held_at or now - idle > self.held_at + 2):
                at = now - idle
                if not self.local_active and idle < ACTIVE_SECS:
                    self.local_active = True
                    self.log(f'LOCAL ACTIVITY detected (HID input {idle:.1f}s ago, not ours): staying connected; '
                             f'idle-timeout now counts from his last input')
                self.last_local_at = max(self.last_local_at or 0, at)
            if self.local_active and idle is not None and idle >= ACTIVE_SECS:
                self.local_active = False
                self.log(f'local activity stopped (idle {idle:.0f}s)')
            ref = self.idle_ref()
            if ref and now - ref > self.idle_timeout:
                self.stop(f'idle timeout ({self.idle_timeout:.0f}s without commands or local input)'); break
            if now - last_save > 5:
                self.save(); last_save = now

    async def run(self):
        lockf = open(HOLDER_LOCK, 'w')
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.log('another holder is running; exiting')
            return 3
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            loop.add_signal_handler(sig, self.stop, f'signal {signal.Signals(sig).name}')
        rm(SOCK)
        old = os.umask(0o177)
        try:
            server = await asyncio.start_unix_server(self.handle, path=SOCK)
        finally:
            os.umask(old)
        os.chmod(SOCK, 0o600)
        ino = os.stat(SOCK).st_ino
        self.save()
        self.log(f'started (idle-timeout {self.idle_timeout:.0f}s)')
        rc = 0
        cred = None
        try:
            cred = creds()            # fetched once, here, and dropped right after the unlock
            self.s = await open_session(first=True, cred=cred)
            self.log('VNC connected, framebuffer warm')
            if not is_locked():
                self.phase = 'not-needed'
                self.stop('screen was already unlocked; nothing to hold')
            else:
                await self.unlock(cred[1])
                cred = None
                os.environ.pop('MAC_REMOTE_PASS', None)
                self.held_at = time.time()
                mark_our_input()
                self.phase = 'held'
                self.save()
                self.log('UNLOCKED and holding the session')
                await self.watch()
        except Fail as e:
            self.phase, self.error, rc = 'failed', str(e), 1
            self.log(f'FAILED: {e}')
            self.stop(f'failed: {e}')
        except Exception as e:
            self.phase, self.error, rc = 'failed', f'{type(e).__name__}: {e}', 1
            self.log(f'FAILED: {type(e).__name__}: {e}')
            self.stop('failed')
        finally:
            cred = None
            os.environ.pop('MAC_REMOTE_PASS', None)
            if self.phase == 'failed':
                await asyncio.sleep(2.5)          # let the caller read the failure over the socket
            server.close()
            try:
                if os.stat(SOCK).st_ino == ino:
                    rm(SOCK)
            except FileNotFoundError:
                pass
            if self.s:
                await self.s.close(grace=0.3)
            rm(HOLDER_JSON)
            self.log(f'EXIT: {self.stop_reason}; disconnected (macOS re-locks ~1 s after this if it unlocked)')
            lockf.close()
        return rc


def holder_main(idle_timeout):
    return asyncio.run(Holder(idle_timeout).run())


# ---------- talking to the holder ----------
def holder_call(req, timeout=10.0):
    """Send one request. Returns the response dict, or None if no holder is listening."""
    if not os.path.exists(SOCK):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCK)
    except (ConnectionRefusedError, FileNotFoundError):
        s.close()
        h = read_json(HOLDER_JSON)
        if not h or not pid_alive(h.get('pid', -1)):
            rm(SOCK); rm(HOLDER_JSON)          # stale leftovers of a dead holder
        return None
    try:
        s.sendall((json.dumps(req) + '\n').encode())
        buf = b''
        while not buf.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        raise Fail(f'holder did not answer {req.get("cmd")} within {timeout:.0f}s')
    finally:
        s.close()
    if not buf:
        raise Fail('holder closed the connection without answering')
    return json.loads(buf)


def holder_info():
    try:
        r = holder_call({'cmd': 'ping'}, timeout=5)
    except Fail:
        return None
    return r['info'] if r and r.get('ok') else None


def via_holder(req, timeout):
    """Run req through the holder if there is one. Returns output text, or None (no holder)."""
    r = holder_call(req, timeout)
    if r is None:
        return None
    if not r.get('ok'):
        raise Fail(r.get('error', 'holder error'))
    return r['out']


def wait_holder_exit(pid, secs):
    end = time.time() + secs
    while time.time() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return not pid_alive(pid)


# ---------- commands ----------
def fmt_t(t):
    return time.strftime('%H:%M:%S', time.localtime(t)) if t else '-'


def cmd_status(a):
    locked = is_locked()
    idle = hid_idle()
    print(f"locked: {'yes' if locked else 'no'}")
    print(f"idle: {idle:.0f}s since last input" if idle is not None else 'idle: unknown')
    act, _ = user_active()
    if not locked:
        print(f"user active at the Mac: {'probably YES' if act else 'no'} (threshold {ACTIVE_SECS}s)")
    h = holder_info()
    if h:
        print(f"holder: RUNNING pid {h['pid']}, phase {h['phase']}, since {fmt_t(h['held_at'] or h['started_at'])}, "
              f"last command {fmt_t(h['last_command_at'])}, idle {h['idle_for'] or 0:.0f}s, "
              f"idle-timeout {h['idle_timeout']:.0f}s (exits in {max(0, h['exits_in'] or 0):.0f}s)"
              + (', LOCAL ACTIVITY seen' if h.get('last_local_input_at') else ''))
    else:
        print('holder: none (no VNC session held)')
        try:
            last = [l for l in open(HOLDER_LOG).read().splitlines() if 'EXIT:' in l][-1:]
            if last:
                print('last holder: ' + last[0])
        except Exception:
            pass
    src = pass_source()
    print(f"VNC user: {mac_user()}; password source: "
          + ({'env': 'MAC_REMOTE_PASS environment variable', 'command': 'MAC_REMOTE_PASS_CMD'}.get(src)
             or f'NONE (set MAC_REMOTE_PASS_CMD; see {CONFIG})')
          + ' (not fetched by status)')
    for d in display_rects():
        print('display:', json.dumps(d))
    return 0


def cmd_unlock(a):
    h = holder_info()
    if h:
        if h['phase'] == 'held' and not is_locked():
            holder_call({'cmd': 'config', 'idle_timeout': a.idle_timeout})
            print(f"already unlocked and held (holder pid {h['pid']}); idle-timeout re-armed: {a.idle_timeout}s")
            return 0
        holder_call({'cmd': 'stop', 'reason': 'replaced by a new unlock'})
        wait_holder_exit(h['pid'], 8)
    if not is_locked():
        print('already unlocked and not by mac-remote: nothing to hold (macOS only re-locks when the VNC '
              'session that unlocked it disconnects)')
        return 0
    if pass_source() is None:
        get_password()           # raises the 'no password source' message; fetches nothing
    if a.idle_timeout <= 0:
        raise Fail('--idle-timeout must be > 0')
    with open(HOLDER_LOG, 'a') as log:    # the child keeps its own copy of the descriptor
        p = subprocess.Popen([sys.executable, os.path.abspath(__file__), '_holder', str(a.idle_timeout)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=dict(os.environ),
                             start_new_session=True, close_fds=True)
    t0 = time.time()
    phase = None
    while time.time() - t0 < 120:
        time.sleep(0.5)
        h = holder_info()
        if h:
            if h['phase'] != phase:
                phase = h['phase']
            if phase == 'held':
                if is_locked():
                    raise Fail('holder says held but the screen is locked; run `mac-remote lock` and retry')
                print(f"unlocked in {time.time()-t0:.0f}s; session HELD by holder pid {h['pid']} "
                      f"(idle-timeout {a.idle_timeout}s). Finish with: mac-remote lock")
                return 0
            if phase == 'failed':
                wait_holder_exit(p.pid, 10)
                raise Fail(f"unlock failed: {h['error']} (screen locked: {'yes' if is_locked() else 'no'}). "
                           f"Take a shot to see; do not retry in a loop (account lockout).")
        if p.poll() is not None:
            if p.returncode == 0 and not is_locked():
                print('already unlocked; no holder needed')
                return 0
            raise Fail(f'holder exited (rc {p.returncode}) before unlocking; see {HOLDER_LOG}')
    try:
        os.kill(p.pid, signal.SIGTERM)
    except Exception:
        pass
    raise Fail(f'unlock timed out after 120s (last phase {phase}); holder stopped. See {HOLDER_LOG}')


async def vnc_lock_keys():
    s = await open_session(first=False)
    try:
        await s.wake()
        await press(s.c, 'Control_L', 'Super_L', 'q')
    finally:
        await s.close()


def wait_locked(secs):
    end = time.time() + secs
    while time.time() < end:
        if is_locked():
            return True
        time.sleep(0.25)
    return is_locked()


def cmd_lock(a):
    h = holder_info()
    if not is_locked():
        act, idle = user_active()
        if act and not a.force:
            raise Fail(f'someone seems to be using the Mac (last local input {idle:.0f}s ago): not locking on them. '
                       'Use --force only if they agreed.')
    if h:
        t0 = time.time()
        holder_call({'cmd': 'stop', 'reason': 'lock command'})
        gone = wait_holder_exit(h['pid'], 6)
        if wait_locked(5):
            print(f"locked: holder disconnected and macOS re-locked by itself ({time.time()-t0:.1f}s)"
                  + ('' if gone else f"; WARNING holder pid {h['pid']} still alive"))
            return 0
        print('holder disconnected but the screen is still unlocked; falling back to Ctrl+Cmd+Q', file=sys.stderr)
    if is_locked():
        print('already locked')
        return 0
    try:
        asyncio.run(vnc_lock_keys())
        if wait_locked(4):
            print('locked (Ctrl+Cmd+Q via VNC)')
            return 0
    except Fail as e:
        print(f'warning: {e}; trying pmset', file=sys.stderr)
    subprocess.run(['pmset', 'displaysleepnow'])
    if wait_locked(4):
        print('locked (pmset displaysleepnow)')
        return 0
    raise Fail('could not lock the screen (holder disconnect, VNC Ctrl+Cmd+Q and pmset all failed)')


def to_fb(x, y, raw):
    if raw:
        return int(x), int(y)
    s = read_json(LAST_SHOT)
    if not s:
        raise Fail('no last shot: run `mac-remote shot` first, or pass --raw for framebuffer coordinates')
    return int(round(s['offset'][0] + x / s['scale'])), int(round(s['offset'][1] + y / s['scale']))


def parse_combo(combo):
    import asyncvnc
    keys = []
    for part in combo.split('+'):
        p = part.strip()
        k = KEYMAP.get(p.lower(), p)
        if len(k) == 1 and k.isalpha():
            k = k.lower()
        elif re.fullmatch(r'[fF]\d{1,2}', k):
            k = k.upper()
        if k not in asyncvnc.key_codes:
            raise Fail(f'unknown key "{p}" in "{combo}" (examples: cmd+tab, ctrl+shift+4, return, esc, f5)')
        keys.append(k)
    return keys


DEAD_KEY_CHARS = "'\"`~^"   # dead keys on US-International / ABNT layouts


def layout_has_dead_keys():
    """True when the selected keyboard layout turns ' " ` ~ ^ into dead keys (e.g. USInternational-PC).
    Then the char alone waits for the next key and can vanish ("It's" came out as "Its")."""
    try:
        out = subprocess.run(['defaults', 'read', os.path.expanduser('~/Library/Preferences/com.apple.HIToolbox.plist'),
                              'AppleSelectedInputSources'], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return False
    return bool(re.search(r'International|ABNT|Brazilian|US Extended|USExtended', out))


def needs_paste(text):
    """Type by pasting when keystrokes would lose characters: dead-key chars on a dead-key layout
    (Chrome dropped them over VNC: "It's" -> "Its") or chars VNC can't send (emoji)."""
    import asyncvnc
    if os.environ.get('MAC_REMOTE_PASS') and text == os.environ['MAC_REMOTE_PASS']:
        return False                     # a secret never goes through the clipboard
    if any(ch not in asyncvnc.key_codes and ch not in '\n\t' for ch in text):
        return True
    return layout_has_dead_keys() and any(ch in DEAD_KEY_CHARS for ch in text)


def type_keys(text):
    import asyncvnc
    keys = []
    for ch in text:
        k = 'Return' if ch == '\n' else 'Tab' if ch == '\t' else ch
        if k not in asyncvnc.key_codes:
            raise Fail(f'cannot type character {ch!r}')
        keys.append(k)
    return keys


def run_request(req, timeout):
    """Through the holder when there is one, else a one-off connection."""
    out = via_holder(req, timeout)
    if out is not None:
        return out
    async def go():
        s = await open_session(first=(req['cmd'] == 'shot'))
        try:
            return await (do_shot(s, req) if req['cmd'] == 'shot' else do_input(s, req, one_off=True))
        finally:
            await s.close(grace=0 if req['cmd'] == 'shot' else CLOSE_GRACE)
    return asyncio.run(go())


def cmd_shot(a):
    print(run_request({'cmd': 'shot', 'path': os.path.abspath(a.path) if a.path else None, 'display': a.display}, 30))
    return 0


def input_req(a, **kw):
    if is_locked():
        raise Fail('screen is LOCKED: refusing to send input. Run `mac-remote unlock` first.')
    return dict(force=a.force, app=a.app, **kw)


def cmd_mouse(a, cmd):
    x, y = to_fb(a.x, a.y, a.raw)
    action = 'move' if cmd == 'move' else 'right' if a.right else 'double' if a.double else 'click'
    print(run_request(input_req(a, cmd=cmd, x=x, y=y, action=action), 30))
    return 0


def cmd_keys(a):
    combos = [parse_combo(c) for c in a.combo]
    print(run_request(input_req(a, cmd='keys', combos=combos), 30 + 0.5 * len(combos)))
    return 0


def cmd_type(a):
    if needs_paste(a.text):
        # Paste through the clipboard, then give the old clipboard back.
        old = subprocess.run(['pbpaste'], capture_output=True).stdout
        subprocess.run(['pbcopy'], input=a.text.encode(), check=True)
        try:
            run_request(input_req(a, cmd='keys', combos=[parse_combo('cmd+v')]), 30)
            time.sleep(0.6)                                  # let the app read the clipboard
        finally:
            subprocess.run(['pbcopy'], input=old)
        print('typed text by paste (length not shown); clipboard restored')
        return 0
    keys = type_keys(a.text)
    print(run_request(input_req(a, cmd='type', keys=keys), 30 + 0.2 * len(keys)))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['_holder']:
        os.umask(0o077)
        return holder_main(argv[1])
    p = argparse.ArgumentParser(prog='mac-remote', description=(
        'See and control this Mac via its own Screen Sharing (VNC), also when the screen is locked.\n'
        'Typical flow: status -> unlock -> shot -> (look at image) -> click/type/keys -> shot -> lock.\n'
        'macOS re-locks ~1 s after the VNC session that unlocked it disconnects, so `unlock` starts a\n'
        'detached holder that keeps ONE connection open; the other commands go through it.'),
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=(
        'Safety: click/move/type/keys refuse when the screen is locked (run unlock) and when someone\n'
        f'used the Mac in the last {ACTIVE_SECS}s (unless --force). The holder exits (macOS re-locks)\n'
        'after --idle-timeout s without commands AND without local input, if VNC drops, or on SIGTERM.\n'
        'First run: `mac-remote setup` (virtualenv with the Python dependencies).\n'
        f'Password: MAC_REMOTE_PASS_CMD (command that prints it) or MAC_REMOTE_PASS; user: MAC_REMOTE_USER\n'
        f'(default: current user). Config file: {CONFIG}\n'
        f'Files: {HOME}/{{holder.sock,holder.json,holder.log,last-shot.json,shots/}}'))
    sp = p.add_subparsers(dest='cmd', required=True, metavar='COMMAND')
    sp.add_parser('status', help='locked?, idle seconds, holder, displays (no credentials needed)')
    u = sp.add_parser('unlock', help='unlock and HOLD the session (detached holder)')
    u.add_argument('--idle-timeout', '--auto-lock', dest='idle_timeout', type=int, default=DEFAULT_IDLE, metavar='SECONDS',
                   help=f'holder exits (screen re-locks) after N s without commands or local input (default {DEFAULT_IDLE}). '
                        'If already held, re-arms it.')
    lk = sp.add_parser('lock', help='release the held session (macOS re-locks); fallback Ctrl+Cmd+Q, pmset')
    lk.add_argument('--force', action='store_true', help='lock even if someone seems to be using the Mac')
    s = sp.add_parser('shot', help='screenshot (JPG, max 1600 px); prints path and display rects')
    s.add_argument('path', nargs='?', help=f'output .jpg (default {SHOT_DIR}/shot-*.jpg)')
    s.add_argument('--display', type=int, metavar='N', help='only display N (index as printed)')
    for name, hlp in (('click', 'click at X Y (pixels of the last shot image)'), ('move', 'move the pointer to X Y')):
        c = sp.add_parser(name, help=hlp)
        c.add_argument('x', type=float); c.add_argument('y', type=float)
        c.add_argument('--raw', action='store_true', help='X Y are framebuffer coordinates, not last-shot image pixels')
        c.add_argument('--force', action='store_true', help='send even if someone seems to be using the Mac')
        c.add_argument('--app', metavar='NAME', help='activate this app first; refuse if it is not frontmost')
        if name == 'click':
            g = c.add_mutually_exclusive_group()
            g.add_argument('--right', action='store_true'); g.add_argument('--double', action='store_true')
        else:
            c.set_defaults(right=False, double=False)
    t = sp.add_parser('type', help='type text (NOT for passwords/secrets)')
    t.add_argument('text'); t.add_argument('--force', action='store_true')
    t.add_argument('--app', metavar='NAME', help='activate this app first; refuse if it is not frontmost (recommended)')
    k = sp.add_parser('keys', help='key combos, e.g. cmd+tab, ctrl+tab, cmd+shift+4, return, esc')
    k.add_argument('combo', nargs='+'); k.add_argument('--force', action='store_true')
    k.add_argument('--app', metavar='NAME', help='activate this app first; refuse if it is not frontmost (recommended)')
    a = p.parse_args(argv)
    os.umask(0o077)                       # shots and state may show private screen content
    os.makedirs(HOME, mode=0o700, exist_ok=True)
    os.chmod(HOME, 0o700)
    try:
        return {'status': cmd_status, 'unlock': cmd_unlock, 'lock': cmd_lock, 'shot': cmd_shot,
                'click': lambda a: cmd_mouse(a, 'click'), 'move': lambda a: cmd_mouse(a, 'move'),
                'type': cmd_type, 'keys': cmd_keys}[a.cmd](a)
    except Fail as e:
        print(f'mac-remote: {e}', file=sys.stderr)
        return 1
    except ModuleNotFoundError as e:
        print(f'mac-remote: missing Python module {e.name!r}. Run `mac-remote setup` once '
              '(creates a virtualenv with asyncvnc, pillow and pyobjc-framework-Quartz).', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
