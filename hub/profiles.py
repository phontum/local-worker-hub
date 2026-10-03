"""Private, reviewed check profiles. No command discovery or automatic remediation."""
import hashlib
import json
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from .models import Check, JobRequest
from .settings import CONFIG


class ProjectProfile(BaseModel):
    repo: str
    constraints: str = Field(max_length=12000)
    prerequisites: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    parameter_names: list[str] = Field(default_factory=list)
    groups: dict[str, list[Check]]
    baseline_notes: list[str] = Field(default_factory=list)


def load_profile(repo):
    root = Path(repo).expanduser().resolve(strict=True)
    matches = []
    for path in sorted((CONFIG/'projects').glob('*.json')):
        p = ProjectProfile.model_validate_json(path.read_text())
        if Path(p.repo).expanduser().resolve() == root: matches.append((path,p))
    if not matches:
        try:
            common=subprocess.check_output(['git','-C',str(root),'rev-parse','--path-format=absolute','--git-common-dir'],
                                           text=True,timeout=5,stderr=subprocess.DEVNULL).strip()
            for path in sorted((CONFIG/'projects').glob('*.json')):
                candidate=ProjectProfile.model_validate_json(path.read_text())
                other=subprocess.check_output(['git','-C',candidate.repo,'rev-parse','--path-format=absolute','--git-common-dir'],
                                              text=True,timeout=5,stderr=subprocess.DEVNULL).strip()
                if Path(common).resolve()==Path(other).resolve():matches.append((path,candidate))
        except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired):pass
    if len(matches) != 1: raise ValueError('Expected one project profile for this repository')
    path,p = matches[0]
    sources = {}
    for name in p.source_files:
        target = (root/name).resolve(strict=True)
        if not target.is_relative_to(root) or not target.is_file(): raise ValueError('Invalid profile source path')
        sources[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    digest = hashlib.sha256(json.dumps({'profile':p.model_dump(),'sources':sources},sort_keys=True).encode()).hexdigest()
    return p,digest


def show_profile(repo):
    p,digest = load_profile(repo)
    return p.model_dump() | {'repo':str(Path(repo).expanduser().resolve()),'profile_hash':digest,
                             'profile_ref':digest[:12], 'failure_policy':'continue_independent'}


def expand_profile(request):
    if not (request.profile_hash or request.profile_ref): return request
    p,digest = load_profile(request.repo)
    if digest != (request.profile_hash or digest) or digest[:12] != (request.profile_ref or digest[:12]):
        raise ValueError('Project profile or source guide changed; inspect and review the new reference')
    if set(request.parameters) - set(p.parameter_names): raise ValueError('Unapproved profile parameter')
    by_name = {c.name:c for checks in p.groups.values() for c in checks}
    selected = {}
    def add(c):
        if c.name in selected: return
        selected[c.name] = c
        for name in c.depends_on:
            if name not in by_name: raise ValueError('Unknown profile dependency')
            add(by_name[name])
    for group in request.check_groups:
        if group not in p.groups: raise ValueError('Unknown check group: '+group)
        for c in p.groups[group]: add(c)
    checks = []
    for c in selected.values():
        data = c.model_dump()
        data['environment'].update({name:value for name,value in request.parameters.items() if name in c.required_env})
        checks.append(data)
    # Requests store the exact expanded commands; a supplied alternative is rejected.
    if request.checks and [c.model_dump() for c in request.checks] != checks:
        raise ValueError('Commands do not match the reviewed profile')
    data = request.model_dump()
    data.update(checks=checks, context=p.constraints)
    return JobRequest.model_validate(data)


def seed_profiles():
    """Initialize private storage without discovering or authorizing user projects.

    Profiles contain reviewed commands and belong in private configuration. Existing
    profiles are preserved; create new ones explicitly from the public example.
    """
    directory = CONFIG/'projects'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return [{'profile': path.stem, 'state': 'preserved'}
            for path in sorted(directory.glob('*.json')) if path.is_file()]
