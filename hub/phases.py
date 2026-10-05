"""Per-phase model configuration, resolved once instead of scattered through the engine loop."""
import asyncio
from dataclasses import dataclass
from .model_registry import DEFAULT_ALIAS, CONTEXTS, model_name, probe

THINKING_PHASES = ('review', 'diagnose', 'answer_review')

@dataclass(frozen=True)
class PhaseSpec:
    phase: str
    alias: str
    model: str
    context: int
    output_limit: int
    thinking: bool
    steps: int
    tool_rounds: int
    temperature: float
    tool_groups: tuple | None = None

def default_rounds(request, phase):
    """Tool rounds when no profile says otherwise: quick public lookups 4, editing 12, everything else 8."""
    if request.role in ('personal', 'researcher') and not request.verify:
        return 4
    return 12 if phase in ('edit', 'repair') or (request.role == 'editor' and phase == 'work') else 8

def resolve_phase(request, profiles, phase, report_only=False):
    """Phase profile overrides role profile; the request overrides both for context and thinking.

    Mirrors the previously inline engine rules exactly. A profile may name a registered model
    alias; the default is Gemma 4 12B (see benchmarks/RESULTS.md).
    """
    fallback = profiles.get('investigator', {}) if phase == 'investigate' else profiles.get(request.role, {}) if phase in ('work', 'edit') else {}
    profile = profiles.get(phase, fallback)
    context = request.context_limit() if request.execution_preset or request.model_context else int(profile.get('context', 16384))
    if context not in CONTEXTS:
        raise ValueError('Context must be 16384 or 32768')
    thinking = False if report_only else (request.model_thinking if request.model_thinking is not None else
        request.execution_preset == 'extended' or bool(profile.get('thinking', phase in THINKING_PHASES)))
    alias = request.model or str(profile.get('model', DEFAULT_ALIAS))  # a per-request model beats every profile, for all phases of the job
    return PhaseSpec(phase=phase, alias=alias, model=model_name(alias), context=context,
        output_limit=request.model_output or max(256, min(4096, int(profile.get('output', 4096)))), thinking=thinking,
        steps=2 if report_only or request.role == 'validator' else max(2, min(18, int(profile.get('steps', 12)))),
        tool_rounds=max(1, min(12, int(profile.get('tool_rounds', default_rounds(request, phase))))),
        temperature=float(profile.get('temperature', 0.2)),
        tool_groups=tuple(str(g) for g in profile['tool_groups']) if isinstance(profile.get('tool_groups'), list) else None), profile

async def model_support(spec):
    """Capabilities Ollama reports for a non-default model; None when unknown or default (no gating)."""
    if spec.alias == DEFAULT_ALIAS:
        return None
    found = await asyncio.to_thread(probe, spec.model)
    if found.get('installed') is False:
        raise RuntimeError('Local model is not installed: ' + spec.model)
    return set(found.get('capabilities') or []) if found.get('installed') else None
