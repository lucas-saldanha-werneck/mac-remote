#!/usr/bin/env python3
"""Tests for popup-vigia. Nothing here touches the real screen: the popup pictures are drawn with
Pillow, and the mac-remote calls are replaced by fakes. OCR tests need `tesseract` (brew install
tesseract) and are skipped without it, unless CI=true (then a missing tesseract is a failure).

Run: python3 tests/test_popup_vigia.py"""
import importlib.machinery, importlib.util, json, os, shutil, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix='popup-vigia-test-')
os.environ['POPUP_VIGIA_LOGDIR'] = os.path.join(TMP, 'logs')
os.environ['POPUP_VIGIA_STATE'] = os.path.join(TMP, 'state')
os.environ['POPUP_VIGIA_CONFIG'] = os.path.join(TMP, 'config')
for k in ('POPUP_VIGIA_NOTIFY', 'POPUP_VIGIA_NOTIFY_OK', 'POPUP_VIGIA_MR', 'TESSERACT'):
    os.environ.pop(k, None)

_loader = importlib.machinery.SourceFileLoader('popup_vigia', os.path.join(ROOT, 'bin', 'popup-vigia'))
_spec = importlib.util.spec_from_loader('popup_vigia', _loader)
pv = importlib.util.module_from_spec(_spec)
_loader.exec_module(pv)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
FONT = '/System/Library/Fonts/Helvetica.ttc'
CI = os.environ.get('CI') == 'true'
HAVE_OCR = bool(shutil.which('tesseract') or os.path.exists('/opt/homebrew/bin/tesseract')) and Image is not None \
    and os.path.exists(FONT)


def card(lines, buttons, noise=False, size=(400, 300)):
    """A synthetic permission popup: grey card, title, body, one row of buttons. Returns (image path, box)."""
    im = Image.new('RGB', size, (205, 205, 205))
    d = ImageDraw.Draw(im)
    title, body = ImageFont.truetype(FONT, 17), ImageFont.truetype(FONT, 14)
    y = 30
    for i, t in enumerate(lines):
        d.text((20, y), t, fill=(20, 20, 20), font=title if i == 0 else body)
        y += 28 if i == 0 else 22
    by = int(size[1] * 0.78)
    x = 20 if len(buttons) == 2 else 10
    for b in buttons:
        w = int(d.textlength(b, font=body)) + 30
        d.rounded_rectangle((x, by - 12, x + w, by + 14), 8, fill=(240, 240, 240))
        d.text((x + 15, by - 8), b, fill=(20, 20, 20), font=body)
        x += w + 14
    if noise:                                        # stray punctuation to the left of the row, like OCR noise
        d.text((4, by - 8), ',', fill=(60, 60, 60), font=body)
    path = os.path.join(TMP, 'card-%d.png' % len(os.listdir(TMP)))
    im.save(path)
    return path, [0, 0, size[0], size[1]]


@unittest.skipUnless(HAVE_OCR or CI, 'needs tesseract, Pillow and Helvetica')
class ReadButtons(unittest.TestCase):
    def test_local_network_popup_gives_allow(self):
        path, box = card(['Allow "claude" to find devices on local networks?',
                          'Claude Code connects to servers and devices',
                          'on your local network when commands you run',
                          'need to reach them.'], ["Don't Allow", 'Allow'])
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertEqual(botao, 'Allow', text)
        self.assertGreater(alvo[0], 120)             # right of "Don't Allow" (which ends near x=115)
        self.assertGreater(alvo[1], box[3] * 0.5)    # in the lower half
        self.assertIsNone(bloq, text)

    def test_ocr_noise_without_letters_is_ignored(self):
        path, box = card(['Allow "claude" to find devices on local networks?', 'Some explanation text.'],
                         ["Don't Allow", 'Allow'], noise=True)
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertEqual(botao, 'Allow', text)

    def test_keychain_popup_is_blocked(self):
        path, box = card(['Keychain Not Found', 'A keychain cannot be found to store the password.'],
                         ['Cancel', 'Reset To Defaults'])
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertTrue(bloq, text)

    def test_keychain_popup_row_alone_is_blocked(self):
        """Even without the title phrases, a row 'Cancel  Reset To Defaults' blocks (rule a)."""
        path, box = card(['Something happened', 'Please choose.'], ['Cancel', 'Reset To Defaults'])
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertTrue(bloq, text)

    def test_reset_in_body_does_not_block_a_normal_popup(self):
        path, box = card(['Allow "claude" to find devices on local networks?',
                          'Terminal: popup-vigia reset',
                          'Claude Code connects to servers on your network.'], ["Don't Allow", 'Allow'])
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertIn('reset', text.lower())
        self.assertIsNone(bloq, text)
        self.assertEqual(botao, 'Allow', text)

    def test_dont_allow_reset_row_is_not_clicked(self):
        path, box = card(['Some title', 'Some body text here.'], ["Don't Allow", 'Reset'])
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertTrue(bloq, text)

    def test_sentence_quoting_the_words_is_not_the_button_row(self):
        path, box = card(['Notice', "Tap Don't Allow or Allow when asked to continue."], ['Close'])
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertIsNone(botao, text)


class Forbidden(unittest.TestCase):
    def test_keychain_phrases(self):
        for t in ('Keychain Not Found', 'KEYCHAIN  NOT\nFOUND', 'A keychain cannot be found to store', 'a Keychain can not be found'):
            self.assertTrue(pv.frase_keychain(t), t)
        for t in ('Allow "claude" to find devices on local networks?', 'popup-vigia reset', 'my keychain is fine', '', None):
            self.assertFalse(pv.frase_keychain(t), t)

    def test_button_row_rule(self):
        self.assertTrue(pv.linha_de_botoes_proibida(['cancel', 'reset', 'to', 'defaults'], 0.2))   # cancel: any height
        self.assertTrue(pv.linha_de_botoes_proibida(["don't", 'allow', 'delete'], 0.7))
        self.assertTrue(pv.linha_de_botoes_proibida(['reset'], 0.8))
        self.assertFalse(pv.linha_de_botoes_proibida(['run', 'popup-vigia', 'reset', 'to', 'clear', 'locks'], 0.2))
        self.assertFalse(pv.linha_de_botoes_proibida(["don't", 'allow', 'allow'], 0.7))

    def test_forbidden_window_is_never_clicked(self):
        """trata_janela with a window whose text says keychain: it must journal 'bloqueada' and never call click."""
        calls = []
        win = {'id': 77, 'dono': 'UserNotificationCenter', 'pid': 1, 'camada': 0, 'alpha': 1.0,
               'box': [100.0, 100.0, 400.0, 300.0], 'caminho': '/System/x'}
        meta = {'displays_fb': [[0, 0, 1600, 1000]], 'scale': 1.0, 'offset': [0, 0], 'image_size': [1600, 1000],
                'mono': 0}

        def fake_run(cmd, timeout):
            calls.append(cmd)
            return 0, '', ''
        tent = {}
        with mock.patch.object(pv, 'telas', return_value=[[0, 0, 1600, 1000]]), \
             mock.patch.object(pv, 'foto', return_value=(meta, '')), \
             mock.patch.object(pv, 'ler_botoes', return_value=('Keychain Not Found\nCancel Reset To Defaults', 'OK', (300.0, 350.0), 'Keychain phrase in the text')), \
             mock.patch.object(pv, 'run', side_effect=fake_run):
            pv.trata_janela(win, tent, set())
        self.assertFalse([c for c in calls if 'click' in c], calls)
        self.assertEqual(tent['77'], pv.MAX_TENTATIVAS)
        with open(pv.JOURNAL) as f:
            self.assertIn('"fase": "bloqueada"', f.read())


class Wiring(unittest.TestCase):
    def test_mac_remote_is_the_sibling_command(self):
        self.assertEqual(pv.MR, os.path.join(ROOT, 'bin', 'mac-remote'))
        self.assertTrue(os.access(pv.MR, os.X_OK))

    def test_no_notify_by_default_and_message_goes_to_log(self):
        self.assertIsNone(pv.notify_cmd())
        pv.avisa('teste-padrao', 'hello')
        with open(pv.RODADAS) as f:
            self.assertIn('notice teste-padrao: hello', f.read())

    def test_config_file_sets_notify_and_tesseract(self):
        with open(pv.CONF, 'w') as f:
            f.write('# comment\nNOTIFY_CMD=/bin/echo hi\nTESSERACT=/x/tess\n')
        try:
            self.assertEqual(pv.notify_cmd(), '/bin/echo hi')
            self.assertEqual(pv.tesseract_path(), '/x/tess')
        finally:
            os.remove(pv.CONF)

    def test_no_personal_names_in_the_script(self):
        with open(os.path.join(ROOT, 'bin', 'popup-vigia'), encoding='utf-8') as f:
            src = f.read().lower()
        for bad in ('lucas', 'avisa-lucas', '/users/'):
            self.assertNotIn(bad, src)

    def test_status_runs(self):
        with mock.patch('sys.stdout') as out:
            pv.cmd_status()
        self.assertTrue(out.write.called)


if __name__ == '__main__':
    unittest.main(verbosity=2)
