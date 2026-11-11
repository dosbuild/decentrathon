from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IssueType = Literal['untranslated','same_as_source','placeholder_mismatch','plural_mismatch',
                    'glossary_violation','length_overflow','machine_translation_suspect',
                    'inconsistent_term','suffix_harmony','orthography','wrong_language']


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    key: str = Field(min_length=1)
    issue_type: IssueType
    severity: Literal['high','medium','low']
    source: str | None
    target: str | None
    evidence: str = Field(min_length=1,max_length=300)
    suggestion: str | None = None
    confidence: float = Field(ge=0,le=1)
    reviewed_by_human: Literal[False] = False

    @model_validator(mode='after')
    def evidence_contract(self):
        quotes = re.findall('«([^«»]*)»',self.evidence)
        values = [v for v in (self.source,self.target,self.suggestion) if isinstance(v,str)]
        if not quotes or any(not q or not any(q in v for v in values) for q in quotes):
            raise ValueError('Evidence must quote verbatim input/suggestion fragments')
        if not re.search('[А-Яа-яЁё]',self.evidence):
            raise ValueError('Evidence must explain the defect in Russian')
        if self.suggestion is not None and self.suggestion==self.target:
            raise ValueError('Suggestion must differ from target')
        return self


def finding_order(finding):
    return ({'high':0,'medium':1,'low':2}[finding.severity],finding.key,finding.issue_type)


class ReviewedFinding(Finding):
    """Read an exported report; human provenance remains a separate record."""
    reviewed_by_human: bool = False
