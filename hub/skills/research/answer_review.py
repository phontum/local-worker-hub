"""Mechanical review guards complement, rather than replace, semantic review."""
import re


def validate_assessment(assessment, task, evidence):
    numbers={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10}
    pattern=r'\bexactly\s+(\d+|'+ '|'.join(numbers)+r')\s+(bullet(?:\s+point)?s?|numbered\s+(?:items?|points?))\b'
    constraints=re.findall(pattern,task,re.I)
    bullets={int(n) if n.isdigit() else numbers[n.lower()] for n,k in constraints if k.lower().startswith('bullet')}
    # A common model slip: correct list items but numbered instead of bullets.
    # Normalize only that unambiguous case, preserving all wording and evidence.
    if len(bullets)==1 and all(k.lower().startswith('bullet') for _,k in constraints) and '```' not in assessment.findings:
        expected=next(iter(bullets))
        listed=re.findall(r'^\s*\d+[.)]\s+\S',assessment.findings,re.M)
        if len(listed)==expected and not re.search(r'^\s*[-*+]\s+\S',assessment.findings,re.M):
            assessment.findings=re.sub(r'(?m)^(\s*)\d+[.)](\s+)',r'\1-\2',assessment.findings)
        elif not listed and not re.search(r'^\s*[-*+]\s+\S',assessment.findings,re.M):
            paragraphs=re.split(r'\n\s*\n',assessment.findings.strip())
            if len(paragraphs)==1:paragraphs=assessment.findings.strip().splitlines()
            if len(paragraphs)==expected and all('\n' not in p and p.strip() for p in paragraphs):
                assessment.findings='\n\n'.join('- '+p for p in paragraphs)
    sources={'answer':assessment.findings,**evidence}
    issues=[]
    for item in assessment.requirements:
        if item.status!='met':continue
        if not item.evidence_refs:
            # A missing structured reference need not hide a literal quote the
            # model already supplied in its evidence explanation. Infer only
            # references whose quoted text is actually observed.
            from hub.models import EvidenceReference
            named=re.findall(r'\bR\d+\b',item.evidence)
            candidates=named or ['answer']
            quoted=re.findall(r"'([^'\n]+)'|\"([^\"\n]+)\"",item.evidence)
            for left,right in quoted:
                quote=left or right
                if len(quote)<3:continue
                source=next((name for name in candidates if quote in sources.get(name,'')),None)
                if source and len(item.evidence_refs)<8:item.evidence_refs.append(EvidenceReference(source=source,quote=quote))
        # Small models often compress non-contiguous quotes with ellipses.
        # Expand them into literal references only when EVERY segment exists.
        # Persisted supporting quotes remain exact observed substrings.
        normalized=[]
        for ref in item.evidence_refs:
            if ref.source in ('findings','final_answer'):ref=ref.model_copy(update={'source':'answer'})
            source=sources.get(ref.source,'')
            if ref.quote not in source:
                # Line wrapping is not a factual discrepancy. Match identical
                # tokens, then save the literal observed substring, never a paraphrase.
                tokens=ref.quote.split()
                if tokens:
                    match=re.search(r'\s+'.join(re.escape(t) for t in tokens),source)
                    if match:ref=ref.model_copy(update={'quote':match.group(0)})
            pieces=[p.strip() for p in re.split(r'\.{3}|\u2026',ref.quote) if p.strip()]
            if ref.quote not in source and len(pieces)>1 and all(len(p)>=8 and p in source for p in pieces):
                normalized.extend(ref.model_copy(update={'quote':piece}) for piece in pieces)
            else:normalized.append(ref)
        if len(normalized)<=8:item.evidence_refs=normalized
        invalid=not item.evidence_refs or any(
            ref.source not in sources or ref.quote not in sources[ref.source]
            for ref in item.evidence_refs)
        if invalid:
            item.status='unknown'
            item.evidence=item.evidence[:1700]+' [Mechanical verification: exact supporting quote missing from observed evidence or final answer.]'
            issues.append(item.requirement)
    # Common explicit output counts are objective, so do not trust a checklist
    # assertion about them. This intentionally has no domain-specific rules.
    for count,kind in constraints:
        expected=int(count) if count.isdigit() else numbers[count.lower()]
        prefix=r'^\s*[-*+]\s+\S' if kind.lower().startswith('bullet') else r'^\s*\d+[.)]\s+\S'
        actual=len(re.findall(prefix,assessment.findings,re.M))
        if actual!=expected:
            from hub.models import RequirementAssessment
            message=f'Exactly {expected} {kind}; final answer contains {actual}.'
            matches=[r for r in assessment.requirements if re.search(pattern,r.requirement,re.I)]
            for item in matches:item.status='unmet';item.evidence=message
            if not matches and len(assessment.requirements)<50:assessment.requirements.append(RequirementAssessment(requirement=message,status='unmet',evidence=message,evidence_refs=[]))
            assessment.status='PARTIAL'
            issues.append(message)
    if any(r.status!='met' for r in assessment.requirements) and assessment.status=='COMPLETE':assessment.status='PARTIAL'
    if issues:assessment.risks+='\nUnverified requirements: '+'; '.join(issues)
    return assessment
