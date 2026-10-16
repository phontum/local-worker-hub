"""Fail-closed experiment checks in a disposable namespace."""
import os
from pathlib import Path
import shutil
import subprocess
import time


class IsolationUnavailable(RuntimeError):
    pass


def command(argv, root, dependencies=()):
    executable = shutil.which('bwrap')
    if not executable:
        raise IsolationUnavailable('Bubblewrap is unavailable')
    root = Path(root).resolve(strict=True)
    args = [executable, '--die-with-parent', '--unshare-all', '--new-session', '--clearenv',
            '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/tmp/home',
            '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/tmp/home']
    for name in ('/usr', '/bin', '/lib', '/lib64'):
        if Path(name).exists():
            args += ['--ro-bind', name, name]
    # Approved Python/Node programs may use user-installed interpreter runtimes.
    # Admit only recognized runtime installation trees, never arbitrary home paths.
    interpreters=[]
    executable_path=shutil.which(str(argv[0]))
    if executable_path:interpreters.append(Path(executable_path).resolve())
    for dependency in dependencies:
        python=Path(dependency)/'bin/python'
        if python.exists():interpreters.append(python.resolve())
    mounts=set()
    for interpreter in interpreters:
        parts=interpreter.parts
        for marker in (('.local','share','uv','python'),('.nvm','versions','node'),('.local','share','mise','installs','node')):
            for index in range(len(parts)-len(marker)):
                if parts[index:index+len(marker)]==marker and len(parts)>index+len(marker):
                    mounts.add(Path(*parts[:index+len(marker)+1]))
    for runtime in sorted(mounts):args += ['--ro-bind',str(runtime),str(runtime)]
    runtime_bins=[str(p/'bin') for p in sorted(mounts)]
    if runtime_bins:
        path_index=args.index('PATH');args[path_index+1]=':'.join([*runtime_bins,'/usr/bin','/bin'])
    args += ['--bind', str(root), str(root), '--chdir', str(root)]
    for dependency in dependencies:
        source = Path(dependency).resolve(strict=True)
        if source.name not in ('.venv', 'venv', 'node_modules') or not source.is_dir():
            raise IsolationUnavailable('Only dependency directories may be mounted')
        args += ['--ro-bind', str(source), str(source), '--ro-bind', str(source), str(root / source.name)]
    return args + ['--', *argv]


def run(argv, root, timeout=60, dependencies=(), cwd='.'):
    base = Path(root).resolve(strict=True)
    working = (base / cwd).resolve(strict=True)
    if not working.is_relative_to(base):
        raise ValueError('Check cwd escapes candidate')
    try:
        args = command(argv, base, dependencies)
        pos=args.index('--chdir');args[pos+1]=str(working)
        started = time.monotonic()
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
        output = result.stdout + result.stderr
        unavailable = result.returncode != 0 and ('bwrap:' in output)
        return {'argv': argv, 'exit_code': result.returncode, 'seconds': time.monotonic()-started,
                'status': 'unsupported' if unavailable else 'passed' if result.returncode == 0 else 'failed',
                'output_tail': output[-12000:]}
    except IsolationUnavailable as exc:
        return {'argv':argv, 'exit_code':None, 'status':'unsupported', 'reason':str(exc)}
    except subprocess.TimeoutExpired:
        return {'argv':argv, 'exit_code':None, 'status':'timed_out'}
