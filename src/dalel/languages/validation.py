"""Shared correction contracts without borrowing another locale's morphology."""
from dalel.languages.identify import detect,base
from dalel.formats.messages import parse,validate_suggestion,target_layout_problems
from dalel.engine.hygiene import LINE_REFLOW


class LocaleFixValidator:
    def __init__(self,source_locale,target_locale,values=(),*,escaping=False):
        self.source_locale=source_locale;self.target_locale=target_locale;self.escaping=escaping
        self.orthography=None
        if base(target_locale)=='uz' and 'cyrl' not in target_locale.casefold():
            from dalel.languages.uzbek import Orthography
            self.orthography=Orthography(values,escaping)

    def original_issues(self,source,target,issues):
        return [issue for issue in issues if not
                (base(self.target_locale)=='uz' and issue['issue_type']=='suffix_harmony')]

    def check(self,source,before,after,*,evidence='',key=None):
        if not isinstance(after,str) or after.startswith('requires_code_change'):return []
        errors=[];reflow=bool(LINE_REFLOW.search(evidence))
        if after!=before and after==source:errors.append({'rule':'source_identical_fix'})
        errors.extend({'rule':'protected_structure','detail':error} for error in
            validate_suggestion(source or '',after,original_target=before,escaping=self.escaping,allow_line_reflow=reflow))
        errors.extend({'rule':'target_layout','detail':error} for error in
            target_layout_problems(before,after,source=source,allow_line_reflow=reflow))
        if detect(source or '',after,self.source_locale,self.target_locale,escaping=self.escaping):
            errors.append({'rule':'wrong_target_language'})
        if self.orthography:
            message=parse(after,self.escaping)
            for method in (self.orthography.edits,self.orthography.lexical_edits):
                _,edits=method(source or '',after,message)
                if edits:errors.append({'rule':'uzbek_orthography','edits':edits})
        return errors
