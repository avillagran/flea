"""User font preferences, with exact backups and conflict-aware restoration. No shell commands."""
import ast
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from xml.sax.saxutils import escape


def root(variable, fallback):
    value = os.environ.get(variable, '')
    return Path(value) if Path(value).is_absolute() else Path.home() / fallback


CONFIG = root('XDG_CONFIG_HOME', '.config')
STATE = root('XDG_STATE_HOME', '.local/state') / 'flea/font-defaults'
TARGETS = [('desktop', 'Desktop interface (GTK / sans-serif)', False),
           ('serif', 'Documents (serif)', False), ('monospace', 'System monospace / Omarchy', True),
           ('alacritty', 'Alacritty', True), ('kitty', 'Kitty', True),
           ('ghostty', 'Ghostty', True), ('foot', 'Foot', True)]


def terminal_path(target):
    name = {'alacritty': 'alacritty.toml', 'kitty': 'kitty.conf', 'foot': 'foot.ini'}.get(target, 'config')
    if target == 'ghostty' and (CONFIG / target / 'config.ghostty').exists():
        name = 'config.ghostty'
    return CONFIG / target / name


def available(target):
    return target in ('desktop', 'serif', 'monospace') or terminal_path(target).exists() or bool(shutil.which(target))


def read(path):
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('The preference file must be a regular file smaller than 1 MiB.')
    with path.open(newline='') as stream:
        return stream.read()


def atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.flea-font-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def key(text, section, name, value):
    """Change an INI key, leaving unrelated sections, sizes and comments intact."""
    lines = text.splitlines(keepends=True)
    current = ''
    found = False
    end = len(lines)
    inside = section == ''
    for i, line in enumerate(lines):
        header = re.match(r'^\s*\[([^]]+)\]', line)
        if header:
            if inside:
                end = i
            current = header[1]
            inside = current == section
        if current == section and re.match(r'^\s*' + re.escape(name) + r'\s*=', line):
            lines[i] = f'{name}={value}\n'
            found = True
    if not found:
        if section and not any(re.match(r'^\s*\[' + re.escape(section) + r'\]', x) for x in lines):
            return text.rstrip() + f'\n[{section}]\n{name}={value}\n'
        lines.insert(end, f'{name}={value}\n')
    return ''.join(lines)


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return '{ ' + ', '.join(json.dumps(k) + ' = ' + toml_value(v) for k, v in value.items()) + ' }'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    return value.isoformat()


def alacritty(text, family):
    before = tomllib.loads(text)
    desired = copy.deepcopy(before)
    font = desired.setdefault('font', {})
    for style in ('normal', 'bold', 'italic', 'bold_italic'):
        font.setdefault(style, {})['family'] = family
    # Preserve every other table verbatim. Reject exotic layouts instead of guessing.
    lines = text.splitlines(keepends=True)
    output = []
    in_font = False
    for line in lines:
        if re.match(r'^\s*\[', line):
            match = re.match(r'^\s*\[(font(?:\.[A-Za-z_]+)?)\]\s*(?:#.*)?$', line.strip())
            in_font = bool(match)
        if not in_font:
            output.append(line)
    result = ''.join(output).rstrip() + '\n\n[font]\n'
    result += ''.join(json.dumps(k) + ' = ' + toml_value(v) + '\n' for k, v in font.items())
    expected = dict(before, font=font)
    if tomllib.loads(result) != expected:
        raise ValueError('This Alacritty layout needs manual editing; no preferences were changed.')
    return result


def changes(target, family):
    if target in ('desktop', 'serif', 'monospace'):
        alias = {'desktop': 'sans-serif', 'serif': 'serif', 'monospace': 'monospace'}[target]
        xml = ('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
               '<fontconfig><match target="pattern"><test name="family" qual="any"><string>' + alias +
               '</string></test><edit name="family" mode="assign_replace" binding="strong"><string>' +
               escape(family) + '</string></edit></match></fontconfig>\n')
        result = {CONFIG / f'fontconfig/conf.d/99-flea-{alias}.conf': xml}
        if target == 'desktop':
            for version in ('3.0', '4.0'):
                path = CONFIG / f'gtk-{version}/settings.ini'
                text = read(path) or ''
                old = re.search(r'^\s*gtk-font-name\s*=.*?\s(\d+(?:\.\d+)?)\s*$', text, re.M)
                result[path] = key(text, 'Settings', 'gtk-font-name', family + ' ' + (old[1] if old else '11'))
        return result
    path = terminal_path(target)
    text = read(path) or ''
    if target == 'alacritty':
        text = alacritty(text, family)
    elif target == 'kitty':
        # A trailing primary setting also overrides an earlier included theme's font.
        text = re.sub(r'^[ \t]*(font_family|bold_font|italic_font|bold_italic_font)[ \t]+.*\n?', '', text, flags=re.M)
        text = text.rstrip() + '\nfont_family family=' + json.dumps(family, ensure_ascii=False) + '\nbold_font auto\nitalic_font auto\nbold_italic_font auto\n'
    elif target == 'ghostty':
        for name in ('font-family', 'font-family-bold', 'font-family-italic', 'font-family-bold-italic'):
            pattern = r'^\s*' + name + r'\s*=.*$'
            replacement = name + ' = ' + json.dumps(family, ensure_ascii=False)
            if re.search(pattern, text, re.M):
                text = re.sub(pattern, lambda match: replacement, text, count=1, flags=re.M)
            elif name == 'font-family':
                text = text.rstrip() + '\n' + replacement + '\n'
    elif target == 'foot':
        section = 'main' if re.search(r'^\s*\[main\]', text, re.M) else ''
        escaped = re.sub(r'([\\,:=-])', r'\\\1', family)
        for name in ('font', 'font-bold', 'font-italic', 'font-bold-italic'):
            old = re.search(r'^\s*' + name + r'\s*=([^\n]*)', text, re.M)
            if old or name == 'font':
                suffix = re.search(r'(?<!\\)[:,].*', old[1]) if old else None
                text = key(text, section, name, escaped + (suffix[0] if suffix else ':size=9'))
    return {path: text}


def setting(action, value=None):
    args = ['gsettings', action, 'org.gnome.desktop.interface', 'font-name']
    if value is not None:
        args.append(value)
    result = subprocess.run(args, text=True, capture_output=True, timeout=3)
    if result.returncode:
        raise ValueError('The desktop font preference could not be updated: ' + result.stderr.strip())
    return result.stdout.strip()


def desktop_setting(family):
    if not shutil.which('gsettings'):
        return None
    try:
        if setting('writable') != 'true':
            return None
        original = setting('get')
    except (ValueError, subprocess.TimeoutExpired):
        return None
    size = re.search(r' (\d+(?:\.\d+)?)' + r"['\"]$", original)
    return dict(original=original, applied=json.dumps(family + ' ' + (size[1] if size else '11'), ensure_ascii=False))


def records():
    result = []
    for target, label, mono in TARGETS:
        journal = STATE / (target + '.json')
        data = json.loads(journal.read_text()) if journal.exists() else {}
        result.append(dict(id=target, label=label, monospace=mono, available=available(target),
                           canRestore=bool(data), family=data.get('family', '')))
    return result


def mutate(verb, target, family):
    journal = STATE / (target + '.json')
    previous = json.loads(journal.read_text()) if journal.exists() else None
    preference = None
    if previous and previous.get('setting'):
        preference = previous['setting']
        if setting('get') != preference['applied']:
            raise ValueError('The desktop font changed outside Flea. Restore it manually to preserve your edits.')
    if previous:
        for entry in previous['files']:
            path = Path(entry['path'])
            if read(path) != entry['applied']:
                raise ValueError('Preferences changed outside Flea. Restore them manually to avoid overwriting your edits.')
    if verb == 'restore':
        if not previous:
            raise ValueError('There is no previous preference saved for this destination.')
        entries = previous['files']
        desired = {Path(x['path']): x['original'] for x in entries}
    else:
        if not available(target):
            raise ValueError('This terminal is not installed or configured.')
        desired = changes(target, family)
        if target == 'desktop':
            updated = desktop_setting(family)
            if preference and not updated:
                raise ValueError('The desktop font settings service is unavailable.')
            if updated:
                if preference:
                    updated['original'] = preference['original']
                preference = updated
        entries = []
        for configured, content in desired.items():
            path = configured.resolve()
            if path.is_relative_to('/usr') or path.is_relative_to('/etc'):
                raise ValueError('The configuration points at a system file; choose a user configuration.')
            original = read(path)
            mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
            entries.append(dict(path=str(path), original=original, applied=content, mode=mode))
        if previous:
            old = {x['path']: x for x in previous['files']}
            if set(old) != {x['path'] for x in entries}:
                raise ValueError('Configuration locations changed. Restore the previous preference first.')
            for entry in entries:
                entry['original'] = old[entry['path']]['original']
                entry['mode'] = old[entry['path']]['mode']
        desired = {Path(x['path']): x['applied'] for x in entries}
    rollback = {path: (read(path), stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600) for path in desired}
    setting_before = setting('get') if preference else None
    try:
        for entry in entries:
            path = Path(entry['path'])
            content = desired[path]
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, content, entry['mode'])
        if preference:
            setting('set', preference['original'] if verb == 'restore' else preference['applied'])
            # Store the canonical GVariant representation used by gsettings get.
            if verb != 'restore':
                actual = setting('get')
                if ast.literal_eval(actual) != ast.literal_eval(preference['applied']):
                    raise ValueError('The desktop settings service did not save the chosen font.')
                preference['applied'] = actual
        if verb == 'restore':
            journal.unlink()
        else:
            atomic(journal, json.dumps(dict(family=family, files=entries, setting=preference)))
    except Exception:
        for path, (content, mode) in rollback.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, content, mode)
        if setting_before is not None:
            try:
                setting('set', setting_before)
            except (ValueError, subprocess.TimeoutExpired):
                pass
        raise


def main():
    verb, target, family = sys.argv[1:]
    if verb != 'status':
        if target not in {x[0] for x in TARGETS} or verb not in ('apply', 'restore'):
            raise ValueError('Choose a supported destination.')
        if verb == 'apply' and (not family or len(family) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in family)):
            raise ValueError('The font family name cannot be used in preferences.')
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(STATE / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            mutate(verb, target, family)
    print(json.dumps(dict(targets=records(), notice='' if verb == 'status' else
                         'Preference saved. Reopen applications or terminals to use it; the Omarchy shell may need restarting. Applications with their own font settings may keep those settings.')))


try:
    main()
except Exception as error:
    print(str(error), file=sys.stderr)
    sys.exit(2)
