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

    def _wide_card(self, extra, bg_text_x=330):
        """The dialog is narrower than the window box: background text sits to the right of the button row
        (15/11: 'Don't Allow Allow Restart rela' from the Claude app behind a translucent popup)."""
        path, _ = card(['"Grok Bot.app" would like to', 'access files in your Desktop folder.'], ["Don't Allow", 'Allow'],
                       size=(300, 300))
        base = Image.open(path)
        im = Image.new('RGB', (520, 300), (40, 40, 44))
        im.paste(base, (0, 0))
        d = ImageDraw.Draw(im)
        by = int(300 * 0.78)
        d.text((bg_text_x, by - 8), extra, fill=(230, 230, 230), font=ImageFont.truetype(FONT, 14))
        im.save(path)
        return path, [0, 0, 520, 300]

    def test_background_words_beside_the_dialog_do_not_hide_the_buttons(self):
        path, box = self._wide_card('Restart relay listener')
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertIn('Restart', text)
        self.assertEqual(botao, 'Allow', text)
        self.assertLess(alvo[0], 300)                # the click lands on the dialog's Allow, not on the background
        self.assertIsNone(bloq, text)

    def test_forbidden_word_in_the_background_row_still_blocks(self):
        path, box = self._wide_card('Reset')
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertTrue(bloq, text)

    def test_word_glued_to_allow_is_not_the_pair(self):
        path, box = card(['Some title', 'Some body text.'], ["Don't Allow", 'Allow Once'])
        text, botao, alvo, bloq = pv.ler_botoes(path, box)
        self.assertIsNone(botao, text)

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


class UnlockLock(unittest.TestCase):
    def test_connection_drop_expires(self):
        self.assertEqual(pv.trava_transitoria("2026 mac-remote: unlock failed: VNC connect/login failed after 4 tries"),
                         "connection drop")

    def test_timeout_expires(self):
        self.assertEqual(pv.trava_transitoria("2026-11-15 01:01:38 timeout of 75s exceeded"), "timeout")

    def test_refused_stays_locked(self):
        self.assertIsNone(pv.trava_transitoria("connect/login failed: login refused"))
        self.assertIsNone(pv.trava_transitoria("timeout of 75s exceeded; password refused"))

    def test_empty_is_transient_other_text_stays_locked(self):
        self.assertEqual(pv.trava_transitoria(""), "unknown")
        self.assertEqual(pv.trava_transitoria("  \n"), "unknown")
        self.assertIsNone(pv.trava_transitoria("login refused"))

    def test_grava_trava_atomic_never_empty(self):
        d = tempfile.mkdtemp()
        old = pv.ST
        pv.ST = d
        try:
            pv.grava_trava("UNLOCK-FAILED", "2026 boom\n")
            with open(d + "/UNLOCK-FAILED") as f:
                self.assertEqual(f.read(), "2026 boom\n")
            self.assertEqual(os.listdir(d), ["UNLOCK-FAILED"])
        finally:
            pv.ST = old
            shutil.rmtree(d)
        self.assertIsNone(pv.trava_transitoria("something else"))


class Notices(unittest.TestCase):
    # Real-looking OCR dump of the whole window box (15/11): background text, the dialog, the button row.
    DUMP = ("& This compu {5 Projects - ~ dou fazer isso.\n"
            "\u201cGrok Bot.app\u201d would like to access files in your Desktop folder.\n"
            "Don't Allow Allow Restart rela 14 finished")

    def test_unsupported_window_portuguese(self):
        t = pv.monta('nao-suportada', self.DUMP, lang='pt')
        self.assertEqual(t.split('\n')[0], 'Popup no Mac: "Grok Bot" quer acessar a pasta Desktop.')
        self.assertEqual(len(t.split('\n')), 3)
        for junk in ('compu', 'Restart', 'Projects', 'OCR'):
            self.assertNotIn(junk, t)

    def test_unsupported_window_english(self):
        t = pv.monta('nao-suportada', self.DUMP, lang='en')
        self.assertEqual(t.split('\n')[0], 'Popup on the Mac: "Grok Bot" wants to access files in your Desktop folder.')
        self.assertNotIn('Restart', t)

    def test_allow_to_phrase_and_unknown_request(self):
        t = pv.monta('nao-suportada', 'Allow \u201cclaude\u201d to find devices on local networks?\nDon\u2019t Allow Allow', lang='pt')
        self.assertIn('"claude" quer encontrar dispositivos na rede local', t)
        t = pv.monta('nao-suportada', '\u201cFoo\u201d would like to fly the moon.\nDon\'t Allow Allow', lang='pt')
        self.assertIn('"Foo" pede: fly the moon', t)

    def test_nothing_clean_found_gives_generic_phrase(self):
        t = pv.monta('nao-suportada', 'garbage ~ | ?? lorem\nrandom words here', lang='pt')
        self.assertTrue(t.startswith('Popup no Mac: uma janela de permiss\u00e3o.'))
        self.assertNotIn('garbage', t)

    def test_lock_notice(self):
        t = pv.monta('trava', lang='pt', motivo=pv.MSGS['trava-UNLOCK-FAILED']['pt'])
        self.assertEqual(t.split('\n')[0], 'Parei de vigiar o Mac: n\u00e3o consegui desbloquear a tela.')
        self.assertIn('`popup-vigia reset`', t)
        self.assertNotIn('UNLOCK-FAILED', t)
        self.assertIn('could not unlock the screen', pv.monta('trava', lang='en', motivo=pv.MSGS['trava-UNLOCK-FAILED']['en']))

    def test_every_notice_has_both_languages_and_at_most_3_lines(self):
        kw = dict(n=3, erro='x', pasta='/p', motivo='m', mr='/m', min=4, what='w')
        for k, v in pv.MSGS.items():
            self.assertEqual(set(v), {'en', 'pt'}, k)
            for lg in ('en', 'pt'):
                t = v[lg].format(**kw)
                self.assertLessEqual(len(t.split('\n')), 3, k)
                self.assertNotIn('OCR', t)

    def test_language_setting(self):
        self.assertEqual(pv.idioma(), 'en')
        with open(pv.CONF, 'w') as f:
            f.write('LANG=pt\n')
        try:
            self.assertEqual(pv.idioma(), 'pt')
            with mock.patch.dict(os.environ, {'POPUP_VIGIA_LANG': 'en'}):
                self.assertEqual(pv.idioma(), 'en')
        finally:
            os.remove(pv.CONF)


class RealOcrNotices(unittest.TestCase):
    # Every distinct OCR text of a request popup in the real click journal (cliques.jsonl, 15/11): the request
    # sentence wraps and OCR glues words from the window behind the translucent dialog to each line.
    REAL = [
        "ideas ar ra re ct ny a gr ier mc mye i iene me new \u2014 Sno ohh\nPark an idea\nNo ideas parked.\nAssumptions z\n\u201cgato\u201d wants to access files\nmanaged by \u201cDropbox\u201d.\nDon't Allow Allow",
        "ideas ar ra re ct ny a gr ier mc mye i iene me new \u2014 Sno ohh\nPark an idea\nNo ideas parked.\nAssumptions z\n\u201cgato2\u201d wants to access files\nmanaged by \u201cDropbox\u201d.\nDon't Allow Allow",
        'ideas ce mr ct fh el Dr "te em me aA me = pe wo ~ Se nm hy\nPark an idea\nNo ideas parked.\nAssumptions\ncea .\n\u201cpython3.14\u201d wants to access\nfiles managed by \u201cDropbox\u201d.\nDon\'t Allow Allow',
        'ideas ar ra re ct ny a gr ier mc mye i iene me new \u2014 Sno ohh\nPark an idea\nNo ideas parked.\nAssumptions z\n\u201cgato4" wants to access files\nmanaged by \u201cDropbox\u201d.\nDon\'t Allow Allow',
        "; = & inis compu\nsem confirmagao.\nm timeout . Nao mexi neles. (4 Projects - ~\nssao Al-Tools ( local_571f@d05 ) [) Remote Co\nirmagao che\next >\nse resumo.\nrectors >\nAllow \u201cGrok Bot.app\u201d to access\nyour music, video activity, and Hie\nroungd task:\npuna shall\u2019c media library in Apple Music? g\nStart relay |\nDon't Allow Allow as\n12 finished\n[Saude - enmg 09-11 >\ncklist 3 times >",
        "& inis compu\n[Saude - enmg 09-11 > (5 Projects - ~\ncklist 3 times > {J Remote Cor\ntava parad i ext >\n) plano.\nando o ouvi rectors >\nAllow \u201cGrok Bot.app\u201d to access\nyour music, video activity, and\nmedia library in Apple Music? ground task\nAck RC-off\nound shell c Don't Allow Allow\n13 finished\nyython3",
        "& This compu\n{5 Projects - ~\ndou fazer isso. ({) Remote Cor\nd39ce4), 8\next >\n|, 86 h para ( Zs\n31691), 85\niectors >\n. Allow \u201cGrok Bot.app\u201d to access\n| off . Mar your music, video activity, and\nnA segundo| media library in Apple Music? ground task\nRestart rel\nDon't Allow Allow \u2014 :\n14 finished\nround >\njundo plano.\namin ( TRIC \\} Alankhiim nadiann navn",
        "& This compu\n{5 Projects - ~\ndou fazer isso. ({) Remote Cor\nd39ce4), 8\nt>\n|, 86 h para ( ie\n31691), 85\niectors >\n. Allow \u201cGrok Bot.app\u201d to access\n| off . Mar your music, video activity, and\nnA segundo| media library in Apple Music? ground task\nRestart rel\nDon't Allow Allow \u2014 :\n14 finished\nround >\njundo plano.\namin ( TRIC \\} Alankhiim nadiann navn",
        '& This compu\n{5 Projects - ~\ndou fazer isso. (J Remote Co:\nd39ce4), 8 ?\nt >\n|, 86 h para r=" iia\n31691), 85\niectors >\n. \u201cGrok Bot.app\u201d would like to\noff .Mar access files in your Desktop\nnsegundo| folder. ground task:\nDon\'t Allow Allow Restart rela\n14 finished\nround >\njundo plano.\namin ( TRIC \\} Alankhiim nadiann navn',
        "& inis compu\n{5 Projects - ~\ndou fazer isso. ({) Remote Cor\nd39ce4), 8 ?\next >\n|, 86 h para CQ\n31691), 85\nrectors >\n\u201cGrok Bot.app\u201d wants to access\n| off . Mar files managed by \u201ciCloud Drive\u201d.\na segundo ground task:\nDon't Allow Allow Restart rela\n14 finished\nround >\njundo plano.",
        "& This compu\n{5 Projects - ~\ndou fazer isso. (J Remote Co:\nd39ce4), 8 ?\nt >\n|, 86 h para = Re\n31691), 85\niectors >\n. \u201cGrok Bot.app\u201d would like to\noff Mar access files in your Documents\nnsegundo| folder. ground task:\nDon't Allow Allow Restart rela\n14 finished\nround >\njundo plano.\namin ( TRIC \\} Alankhiim nadiann navn",
        "& This compu\n{5 Projects - ~\ndou fazer isso. (J Remote Co:\nd39ce4),8 ?\nt >\n|, 86 h para ron Re\n31691), 85\niectors >\n. \u201cGrok Bot.app\u201d would like to\n| off .Mar access files in your Downloads\nnsegundo| folder. ground task:\nDon't Allow Allow Restart rela\n14 finished\nround >\njundo plano.\namin ( TRIC \\} Alankhiim nadiann navn",
    ]
    PT = [
        '"gato" quer acessar arquivos do Dropbox',
        '"gato2" quer acessar arquivos do Dropbox',
        '"python3.14" quer acessar arquivos do Dropbox',
        '"gato4" quer acessar arquivos do Dropbox',
        '"Grok Bot" quer acessar suas m\u00fasicas e v\u00eddeos',
        '"Grok Bot" quer acessar suas m\u00fasicas e v\u00eddeos',
        '"Grok Bot" quer acessar suas m\u00fasicas e v\u00eddeos',
        '"Grok Bot" quer acessar suas m\u00fasicas e v\u00eddeos',
        '"Grok Bot" quer acessar a pasta Desktop',
        '"Grok Bot" quer acessar arquivos do iCloud Drive',
        '"Grok Bot" quer acessar a pasta Documents',
        '"Grok Bot" quer acessar a pasta Downloads',
    ]
    EN = [
        '"gato" wants to access files in Dropbox',
        '"gato2" wants to access files in Dropbox',
        '"python3.14" wants to access files in Dropbox',
        '"gato4" wants to access files in Dropbox',
        '"Grok Bot" wants to access your music and videos',
        '"Grok Bot" wants to access your music and videos',
        '"Grok Bot" wants to access your music and videos',
        '"Grok Bot" wants to access your music and videos',
        '"Grok Bot" wants to access files in your Desktop folder',
        '"Grok Bot" wants to access files in iCloud Drive',
        '"Grok Bot" wants to access files in your Documents folder',
        '"Grok Bot" wants to access files in your Downloads folder',
    ]

    def test_real_texts_give_clean_notices(self):
        self.assertEqual(len(self.REAL), len(self.PT))
        for texto, pt, en in zip(self.REAL, self.PT, self.EN):
            self.assertEqual(pv.monta('nao-suportada', texto, lang='pt').split('\n')[0], 'Popup no Mac: %s.' % pt)
            self.assertEqual(pv.monta('nao-suportada', texto, lang='en').split('\n')[0], 'Popup on the Mac: %s.' % en)

    def test_junk_at_the_start_of_a_wrapped_line_is_dropped(self):
        t = '\u201cFoo\u201d would like to fly\n| off . Mar the moon. ground task\nDon\'t Allow Allow'
        self.assertIn('"Foo" pede: fly the moon.', pv.monta('nao-suportada', t, lang='pt'))
        self.assertIn('"Foo" wants to fly the moon.', pv.monta('nao-suportada', t, lang='en'))

    def test_unknown_request_with_junk_gives_generic_phrase(self):
        t = '\u201cFoo\u201d would like to fly | {x} moon.\nDon\'t Allow Allow'
        self.assertTrue(pv.monta('nao-suportada', t, lang='pt').startswith('Popup no Mac: uma janela de permiss\u00e3o.'))


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
