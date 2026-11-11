"""Scoped terminology validation for proposed corrections."""
import re
from dalel.engine.hygiene import FixValidator
from dalel.terminology.glossary import source_match,Matcher,approved
from dalel.engine.rules import LOANS

class PolicyValidator(FixValidator):
    def __init__(self,context,terms):
        super().__init__(context=context,approved_glossary=terms);self.terms=terms;self.matcher=Matcher(LOANS)

    def original_issues(self,source,target,issues):
        selected=super().original_issues(source,target,issues)
        return [issue for issue in selected if issue['issue_type']!='inconsistent_term' or
                not self.context.canon_aligned(source,target,issue.get('evidence',''))]

    def requirements(self,source):
        return [{'source_term':r['ru'],'target_term':r['kk'],'scope':r.get('_rule',{}).get('scope'),
                 'allowed_variants':[] if r.get('_rule',{}).get('canon_verification',{}).get('verdict')=='yes' else r.get('_rule',{}).get('allowed_variants',[])} for r in self.terms
                if approved(r, 'ru', 'kk') and source_match(source or '',r['ru'],r.get('_rule')) and not (
                    r.get('_rule',{}).get('status')=='ai_proposed'
                    and re.search('[A-Za-z]',r['kk']) and not re.search('[А-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]',r['kk'])
                    and r['kk'].casefold() not in (source or '').casefold())]

    def check(self,source,before,after,**kwargs):
        errors=super().check(source,before,after,**kwargs)
        if isinstance(after,str):
            for term in self.requirements(source):
                if not any(self.matcher.contains(after,t,'kk') for t in [term['target_term'],*term['allowed_variants']]):
                    errors.append(dict(rule='glossary_rendering',**term))
        return errors
