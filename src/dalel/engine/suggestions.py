"""Small mechanical suggestions with a complete source-token validation gate."""
from dalel.formats.messages import parse,validate_suggestion


def one_edit(a,b):
    if a==b or abs(len(a)-len(b))>1:
        return False
    if len(a)==len(b):
        return sum(x!=y for x,y in zip(a,b))==1
    shorter,longer=(a,b) if len(a)<len(b) else (b,a)
    index=next((i for i,(x,y) in enumerate(zip(shorter,longer)) if x!=y),len(shorter))
    return shorter[index:]==longer[index+1:]


def placeholder_name_repair(source,target,escaping=False):
    left,right=parse(source,escaping),parse(target,escaping)
    if left.errors or right.errors:
        return None
    missing,extra=left.names-right.names,right.names-left.names
    if len(missing)!=1 or len(extra)!=1:
        return None
    intended,typo=next(iter(missing)),next(iter(extra))
    if not one_edit(intended,typo):
        return None
    affected=[arg for arg in right.arguments if arg.name==typo]
    if not affected or any(arg.role!='value' for arg in affected):
        return None
    result=target
    for argument in sorted(affected,key=lambda item:item.start,reverse=True):
        position=argument.start+1
        while target[position].isspace():
            position+=1
        result=result[:position]+intended+result[position+len(typo):]
    return result if not validate_suggestion(source,result,escaping=escaping,original_target=target) else None
