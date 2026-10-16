"""Reversible integration control; never restore legacy uninstall backups."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from .settings import CONFIG, STATE, PROJECT

START = '<!-- LOCAL-WORKER:START -->'
END = '<!-- LOCAL-WORKER:END -->'


def control_path():
    return CONFIG / 'integration-control.json'


def state():
    try:
        value = json.loads(control_path().read_text())
        if not isinstance(value, dict) or not isinstance(value.get('enabled'), bool):
            raise ValueError('Invalid integration state')
        return value
    except FileNotFoundError:
        return {'enabled': True, 'phase': 'enabled', 'components': [], 'residual': []}
    except (OSError, ValueError):
        return {'enabled': False, 'phase': 'disabled', 'components': [], 'residual': ['Unreadable integration state']}


def enabled():
    return state()['enabled']


def require_enabled():
    if not enabled():
        raise RuntimeError('Local Worker is switched off. Run local-worker on; use a fresh frontier session.')


def atomic(path, content, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing symlink configuration: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.worker-switch-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save(value):
    atomic(control_path(), json.dumps(value, indent=2) + '\n')


@contextlib.contextmanager
def locked():
    from .skills.coding.execution.control import switch_lock
    with switch_lock(CONFIG,exclusive=True):yield


def owned(value):
    text = json.dumps(value)
    return bool(re.search(r'(?:local-worker|hub\.mcp_adapter|hub\.cli|local_worker)', text))


def roots(home):
    found = {PROJECT.resolve()}
    for p in (CONFIG / 'projects').glob('*.json'):
        try:
            found.add(Path(json.loads(p.read_text())['repo']).expanduser().resolve())
        except (OSError, ValueError, KeyError):
            pass
    try:
        data = json.loads((home / '.claude.json').read_text())
        found.update(Path(p).resolve() for p in data.get('projects', {}))
    except (OSError, ValueError):
        pass
    for p in state().get('roots', []):
        found.add(Path(p).resolve())
    try:
        import tomllib
        data = tomllib.loads((home / '.codex/config.toml').read_text())
        found.update(Path(p).resolve() for p in data.get('projects', {}))
    except (OSError, ValueError):
        pass
    return sorted(p for p in found if p.is_dir() and p != Path('/'))


def inventory(home):
    names = {home / '.codex/config.toml', home / '.codex/hooks.json', home / '.claude.json',
             home / '.claude/settings.json', home / '.claude/settings.local.json',
             home / '.codex/AGENTS.md', home / '.claude/CLAUDE.md'}
    for root in roots(home):
        for base in (root, *[p for p in root.parents if p != Path('/') and p != home]):
            names.update(base / p for p in ('AGENTS.md', 'CLAUDE.md', '.mcp.json', '.codex/config.toml',
                '.codex/hooks.json', '.claude/settings.json', '.claude/settings.local.json'))
        # Nested instruction/config files are bounded to the registered project, excluding caches.
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '.venv', 'venv', 'dist', 'legacy')
                       and not (Path(base) / d).is_symlink()]
            for name in files:
                path = Path(base) / name
                if name in ('AGENTS.md', 'CLAUDE.md', '.mcp.json', 'hooks.json'):
                    names.add(path)
            if len(names) > 2000:
                raise ValueError('Integration inventory exceeds 2000 files; register narrower projects')
    return sorted(p for p in names if p.is_file() and not p.is_symlink())


def json_components(data, path, trail=()):
    edits = []
    if not isinstance(data, dict):
        return edits
    for key in list(data):
        value = data[key]
        if key in ('mcpServers', 'mcp_servers', 'enabledPlugins') and isinstance(value, dict):
            for name in list(value):
                if 'local-worker' in name or owned(value[name]):
                    edits.append({'path': str(path), 'kind': 'json', 'trail': list(trail + (key, name)), 'value': value.pop(name)})
        elif key == 'hooks' and isinstance(value, dict):
            for event, groups in list(value.items()):
                if not isinstance(groups, list):
                    continue
                for group in list(groups):
                    if not isinstance(group, dict):
                        continue
                    hooks = group.get('hooks', [])
                    removed = [h for h in hooks if owned(h)]
                    if removed:
                        edits.append({'path': str(path), 'kind': 'hook', 'trail': list(trail + (key, event)),
                                      'group': {k: v for k, v in group.items() if k != 'hooks'}, 'value': removed})
                        group['hooks'] = [h for h in hooks if not owned(h)]
                        if not group['hooks']:
                            groups.remove(group)
        elif isinstance(value, dict):
            edits.extend(json_components(value, path, trail + (key,)))
    return edits


def detach(path):
    text = path.read_text()
    records, residual = [], []
    if path.suffix == '.json':
        data = json.loads(text)
        records = json_components(data, path)
        after = json.dumps(data, indent=2) + '\n' if records else text
    elif path.suffix == '.toml':
        # Preserve exact TOML spelling/comments: remove complete owned top-level sections.
        spans = list(re.finditer(r'^\[(?:[^\n]+)\]\s*$', text, re.M))
        after = text
        for i in reversed(range(len(spans))):
            match = spans[i]
            finish = spans[i + 1].start() if i + 1 < len(spans) else len(text)
            section = text[match.start():finish]
            header = match.group()
            if re.search(r'mcp_servers\.(?:"?local-worker"?)\]', header):
                records.append({'path': str(path), 'kind': 'text', 'value': section})
                after = after[:match.start()] + after[finish:]
        if 'local-worker' in after or 'local_worker' in after:
            residual.append(str(path) + ': unrecognized TOML integration')
    else:
        after = text
        pattern = re.compile(re.escape(START) + r'.*?' + re.escape(END) + r'\n?', re.S)
        for match in list(pattern.finditer(text))[::-1]:
            records.append({'path': str(path), 'kind': 'text', 'value': match.group()})
            after = after[:match.start()] + after[match.end():]
        # Migrate only the exact installer-generated legacy workflow; backups are not an off state.
        from .install import WORKFLOW, LEGACY_WORKFLOW
        legacy=LEGACY_WORKFLOW
        match=next((w for w in (WORKFLOW,legacy) if w in after),None)
        if match:
            records.append({'path': str(path), 'kind': 'text', 'value': START + '\n' + match + '\n' + END + '\n'})
            after = after.replace(match, '', 1)
        # Project source/docs contain mentions; report active instructions, not documentation examples.
        if re.search(r'(?im)^.*(?:delegate|use local-worker|run local-worker|prefer the MCP tools).*$', after):
            residual.append(str(path) + ': unmanaged delegation instructions')
    if records:
        for r in records:
            r['mode'] = path.stat().st_mode & 0o777
        return after, records, residual
    return text, [], residual


def systemctl(*args):
    done = subprocess.run(['systemctl', '--user', *args, 'local-worker-hub.service'], capture_output=True, text=True, timeout=20)
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or 'systemd command failed')


def switch_off(home=None):
    home = Path(home or Path.home())
    with locked():
        value = state()
        value.update(enabled=False, phase='disabling', updated=time.time())
        value.setdefault('components', [])
        save(value)  # Gate first, even if a config edit later fails.
        errors = []
        try:
            paths = inventory(home)
        except (OSError, ValueError) as exc:
            paths = []; errors.append(str(exc))
        for path in paths:
            try:
                after, records, residual = detach(path)
                errors.extend(residual)
                if records:
                    value['components'].extend(records)
                    save(value)  # Journal before atomic mutation.
                    atomic(path, after, path.stat().st_mode & 0o777)
            except (OSError, ValueError) as exc:
                errors.append(str(path) + ': ' + str(exc))
        from .store import Store
        store = Store()
        with store.connect() as db:
            db.execute("UPDATE jobs SET state='cancelled',ended=? WHERE state='queued'", (time.time(),))
            running = db.execute("SELECT id FROM jobs WHERE state='running'").fetchone()
        try:
            systemctl('disable')
            if not running:
                systemctl('stop')
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
        value.update(phase='disabling' if running else 'disabled', draining_job=running['id'] if running else None,
                     residual=sorted(set(errors)))
        save(value)
        return value


def restore_component(record):
    path = Path(record['path'])
    text = path.read_text() if path.exists() else ''
    if record['kind'] == 'text':
        if record['value'] in text:
            return
        if (path.suffix == '.toml' and 'local-worker' in text) or (path.suffix=='.md' and START in text):
            raise ValueError('Conflicting integration: ' + str(path))
        atomic(path, text + ('\n' if text and not text.endswith('\n') else '') + record['value'], record['mode'])
        return
    data = json.loads(text or '{}')
    node = data
    for key in record['trail'][:-1]:
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise ValueError('Conflicting config structure: ' + str(path))
    key = record['trail'][-1]
    if record['kind'] == 'json':
        if key in node and node[key] != record['value']:
            raise ValueError('Conflicting integration: ' + str(path))
        node[key] = record['value']
    else:
        groups = node.setdefault(key, [])
        group = next((g for g in groups if {k:v for k,v in g.items() if k != 'hooks'} == record['group']), None)
        if group is None:
            group = dict(record['group'], hooks=[]);groups.append(group)
        for hook in record['value']:
            if hook not in group['hooks']:
                group['hooks'].append(hook)
    atomic(path, json.dumps(data, indent=2) + '\n', record['mode'])


def switch_on(home=None):
    with locked():
        value = state()
        if value['enabled'] and value['phase'] == 'enabled':
            return value
        value.update(enabled=False, phase='enabling', residual=[])
        save(value)
        try:
            # Validate all merges in a private staging directory before restoring any client config.
            with tempfile.TemporaryDirectory(prefix='worker-switch-preflight-',dir=CONFIG) as tmp:
                staged={}
                for record in value.get('components', []):
                    original=Path(record['path'])
                    if original.is_symlink():raise ValueError('Refusing linked config: '+str(original))
                    if str(original) not in staged:
                        target=Path(tmp)/(str(len(staged))+original.suffix)
                        if original.exists():target.write_text(original.read_text())
                        staged[str(original)]=target
                    restore_component({**record,'path':str(staged[str(original)])})
            for record in value.get('components', []):
                restore_component(record)
            # Startup is permitted in enabling, while public ingress remains gated.
            systemctl('enable', '--now')
            from .client import connect
            client = connect(start=True,allow_enabling=True);client.close()
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            value.update(phase='disabled', residual=[str(exc)])
            save(value)
            return value
        value.update(enabled=True, phase='enabled', components=[], draining_job=None, residual=[], updated=time.time())
        save(value)
        return value


def status():
    value = state()
    try:
        p = subprocess.run(['systemctl', '--user', 'is-active', 'local-worker-hub.service'], capture_output=True, text=True, timeout=5)
        value['service'] = p.stdout.strip() or 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        value['service'] = 'unavailable'
    value['fresh_session_required'] = True
    return value
