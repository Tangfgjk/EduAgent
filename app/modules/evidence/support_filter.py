from typing import Literal
Assistance=Literal['all','unassisted_practice','assisted','independent']


def matches(fact,mode):
    if mode=='all':return True
    p=fact.payload
    if mode=='independent':
        return (fact.kind=='answer' and p.get('stage')=='checkpoint' and p.get('assessment_exposure')=='fresh'
            or fact.kind=='activity_contribution' and p.get('action')=='verify' and p.get('independent') is True)
    if mode=='unassisted_practice':return fact.kind=='answer' and p.get('stage')=='practice' and p.get('hints_used',0)==0
    return p.get('stage')=='practice' and p.get('hints_used',0)>0 or fact.kind=='activity_contribution' and p.get('action')!='verify'
