#!/usr/bin/env python3
"""Exercise shipped preference actions in private user roots. Never write host preferences."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET

BIN = os.environ.get('FLEA_BIN', str(Path('target/debug/flea').resolve()))
with tempfile.TemporaryDirectory(prefix='flea-defaults-') as scratch:
    root = Path(scratch)
    env = dict(os.environ, GSETTINGS_BACKEND='keyfile', HOME=scratch, XDG_CONFIG_HOME=str(root / 'config'),
               XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'), XDG_CACHE_HOME=str(root / 'cache'))
    def run(*args, ok=True):
        out = subprocess.run([BIN, '--font', *args], env=env, text=True, capture_output=True)
        assert (out.returncode == 0) == ok, out.stdout + out.stderr
        return json.loads(out.stdout)
    def put(name, text):
        path = root / 'config' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path
    mono = subprocess.check_output(['fc-match', 'monospace', '-f', '%{file}'], text=True)
    proportional = subprocess.check_output(['fc-match', 'sans-serif', '-f', '%{file}'], text=True)
    fixture = root / 'download.ttf'
    fixture.write_bytes(Path(mono).read_bytes())
    run('apply', str(fixture), '0', 'monospace', ok=False)
    info = run('install', str(fixture))
    face = info['faces'][0]
    assert face['monospace']
    family = face['family']
    originals = {
        'alacritty/alacritty.toml': '# keep\n[window]\nopacity=0.8\n[font]\nsize=17\nnormal={family="Old",style="Regular"}\n[font.offset]\nx=2\ny=3\n',
        'kitty/kitty.conf': 'font_size 17\ninclude theme.conf\nfont_family Old\n',
        'ghostty/config': 'font-size = 17\nfont-family = Old\nfont-family = SymbolFallback\nbackground = #123456\n',
        'foot/foot.ini': 'font=Old:size=17\n[scrollback]\nlines=2000\n',
        'gtk-3.0/settings.ini': '[Settings]\ngtk-font-name=Old 17\ngtk-theme-name=Example\n',
    }
    paths = {name: put(name, text) for name, text in originals.items()}
    # Omarchy's existing fonts.conf is loaded after conf.d. The chosen alias must still win.
    old_family = subprocess.check_output(['fc-match', 'serif', '-f', '%{family[0]}'], text=True)
    from xml.sax.saxutils import escape
    old_xml = '<fontconfig><match target="pattern"><test name="family" qual="any"><string>monospace</string></test><edit name="family" mode="prepend_first" binding="strong"><string>' + escape(old_family) + '</string></edit></match></fontconfig>'
    existing_fontconfig = put('fontconfig/fonts.conf', old_xml)
    before = run('defaults')
    assert len(before['targets']) == 7
    for target in ('desktop', 'serif', 'monospace', 'alacritty', 'kitty', 'ghostty', 'foot'):
        result = run('apply', str(fixture), str(face['index']), target)
        assert next(x for x in result['targets'] if x['id'] == target)['canRestore']
        run('apply', str(fixture), str(face['index']), target)
        if target == 'ghostty':
            assert 'font-family = SymbolFallback' in paths['ghostty/config'].read_text()
        if target == 'desktop':
            if subprocess.run(['gsettings', 'writable', 'org.gnome.desktop.interface', 'font-name'], env=env, capture_output=True).stdout.strip() == b'true':
                current = subprocess.check_output(['gsettings', 'get', 'org.gnome.desktop.interface', 'font-name'], env=env, text=True)
                assert family in current
        if target == 'alacritty':
            config = tomllib.loads(paths['alacritty/alacritty.toml'].read_text())
            assert config['font']['size'] == 17 and config['font']['offset']['x'] == 2
            assert config['font']['normal']['family'] == family and config['window']['opacity'] == .8
        if target in ('desktop', 'serif', 'monospace'):
            alias = {'desktop': 'sans-serif', 'serif': 'serif', 'monospace': 'monospace'}[target]
            xml = root / f'config/fontconfig/conf.d/99-flea-{alias}.conf'
            ET.fromstring(xml.read_text())
            match = subprocess.check_output(['fc-match', alias, '-f', '%{family[0]}'], env=env, text=True)
            assert match == family, (alias, match, family)
        run('restore', target)
    assert existing_fontconfig.read_text() == old_xml
    for name, path in paths.items():
        assert path.read_text() == originals[name], name
    assert not (root / 'config/gtk-4.0/settings.ini').exists()
    # Refuse external edits, and preserve the original backup on repeated applies.
    run('apply', str(fixture), '0', 'kitty')
    kitty = paths['kitty/kitty.conf']
    applied = kitty.read_text()
    kitty.write_text(applied + '# external edit\n')
    run('restore', 'kitty', ok=False)
    run('apply', str(fixture), '0', 'kitty', ok=False)
    assert kitty.read_text().endswith('# external edit\n')
    kitty.write_text(applied)
    run('restore', 'kitty')
    assert kitty.read_text() == originals['kitty/kitty.conf']
    # A system font may be selected without copying it into Flea's font directory.
    assert run('info', mono)['desktopAvailable']
    run('apply', mono, '0', 'monospace')
    run('restore', 'monospace')
    if not run('info', proportional)['faces'][0]['monospace']:
        run('apply', proportional, '0', 'kitty', ok=False)
    run('apply', mono, '0', 'unknown', ok=False)
    # Dotfile symlinks survive apply/restore, and the real target receives the setting.
    real = root / 'dotfiles/kitty.conf'
    real.parent.mkdir()
    real.write_text(originals['kitty/kitty.conf'])
    kitty.unlink()
    kitty.symlink_to(real)
    run('apply', mono, '0', 'kitty')
    assert kitty.is_symlink() and family in real.read_text()
    run('restore', 'kitty')
    assert kitty.is_symlink() and real.read_text() == originals['kitty/kitty.conf']
    # Exercise escaping with real terminal parsers when installed, without starting a terminal.
    special = 'Flea "Demo" & <Sans> \\ Regular'
    helper = str(Path('src/fontdefaults.py').resolve())
    for target in ('desktop', 'alacritty', 'kitty', 'ghostty', 'foot'):
        out = subprocess.run([sys.executable, '-I', helper, 'apply', target, special], env=env, text=True, capture_output=True)
        assert out.returncode == 0, out.stderr
        if target == 'desktop':
            ET.fromstring((root / 'config/fontconfig/conf.d/99-flea-sans-serif.conf').read_text())
        elif target == 'alacritty':
            assert tomllib.loads(paths['alacritty/alacritty.toml'].read_text())['font']['normal']['family'] == special
        elif target == 'kitty' and shutil.which('kitty'):
            code = 'from kitty.config import load_config; import sys; bad=[]; o=load_config(sys.argv[1], accumulate_bad_lines=bad); assert not bad, bad; print(o.font_family)'
            native = subprocess.run(['kitty', '+runpy', code, str(kitty)], env=env, text=True, capture_output=True)
            assert native.returncode == 0, native.stderr
        elif target == 'ghostty' and shutil.which('ghostty'):
            native = subprocess.run(['ghostty', '+show-config'], env=env, text=True, capture_output=True)
            assert native.returncode == 0 and 'error' not in native.stderr.lower(), native.stderr
            assert 'SymbolFallback' in native.stdout
        elif target == 'foot' and shutil.which('foot'):
            native = subprocess.run(['foot', '--check-config', '--config=' + str(paths['foot/foot.ini'])], env=env, text=True, capture_output=True)
            assert native.returncode == 0, native.stderr
        out = subprocess.run([sys.executable, '-I', helper, 'restore', target, ''], env=env, text=True, capture_output=True)
        assert out.returncode == 0, out.stderr
    # A failing desktop settings service must roll back all file changes too.
    stub_dir = root / 'bin'
    stub_dir.mkdir()
    stub = stub_dir / 'gsettings'
    stub.write_text("#!/usr/bin/env python3\nimport sys\na=sys.argv[1]\nif a=='writable': print('true')\nelif a=='get': print(\"'Original 17'\")\nelse: sys.exit(2)\n")
    stub.chmod(0o700)
    env['PATH'] = str(stub_dir) + os.pathsep + env['PATH']
    run('apply', mono, '0', 'desktop', ok=False)
    assert paths['gtk-3.0/settings.ini'].read_text() == originals['gtk-3.0/settings.ini']
    assert not (root / 'config/fontconfig/conf.d/99-flea-sans-serif.conf').exists()
    assert not (root / 'state/flea/font-defaults/desktop.json').exists()
print('PASS font defaults: seven destinations, real fontconfig resolution, sizes, restore, conflicts, symlinks and installed-face validation')
