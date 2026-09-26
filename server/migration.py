"""Validate imported inventory without trusting caller-supplied history/identity."""
import re
from datetime import datetime
from .domain import DomainError, string, number

def validate_snapshot(value):
    def fail():
        raise DomainError('移行データの形式が不正です')
    def ident(v):
        result=string(v,80)
        if not result:fail()
        return result
    def date(v):
        string(v,40)
        try:datetime.fromisoformat(v.replace('Z','+00:00'))
        except ValueError:fail()
        return v
    if not isinstance(value,dict) or value.get('schema')!=1 or not isinstance(value.get('refrigerators'),list) or not isinstance(value.get('bottles'),list):fail()
    if len(value['refrigerators'])>100 or len(value['bottles'])>1000:fail()
    refrigerators=[]
    for f in value['refrigerators']:
        if not isinstance(f,dict):fail()
        fridge=dict(id=ident(f.get('id')),name=string(f.get('name'),80))
        if not fridge['name']:fail()
        refrigerators.append(fridge)
    fids={f['id'] for f in refrigerators}
    if len(fids)!=len(refrigerators):fail()
    bottles=[]
    for b in value['bottles']:
        if not isinstance(b,dict):fail()
        volume=number(b.get('volume'),1,10000);remaining=number(b.get('remaining'),0,volume)
        if b.get('fridgeId') not in fids or (volume==300 and remaining not in (0,300)):fail()
        photo=string(b.get('photo',''),1600000)
        if photo and not re.fullmatch(r'data:image/jpeg;base64,[A-Za-z0-9+/=]+',photo):fail()
        opened=date(b.get('openedAt')) if b.get('openedAt') is not None else None
        if remaining<volume and not opened:fail()
        name=string(b.get('name'))
        if not name:fail()
        bottles.append(dict(id=ident(b.get('id')),name=name,brewery=string(b.get('brewery','')),category=string(b.get('category','')),productionDate=string(b.get('productionDate',''),40),volume=volume,remaining=remaining,fridgeId=b['fridgeId'],shelf=string(b.get('shelf',''),80),photo=photo,receivedAt=date(b.get('receivedAt')),openedAt=opened,version=number(b.get('version'),1,1000000000)))
    if len({b['id'] for b in bottles})!=len(bottles):fail()
    return dict(refrigerators=refrigerators,bottles=bottles)

def merge_personal_snapshot(state,snapshot):
    imported=validate_snapshot(snapshot);added=skipped=0
    for f in imported['refrigerators']:
        existing=next((x for x in state['refrigerators'] if x['id']==f['id']),None)
        if existing is None:state['refrigerators'].append(f)
        elif existing['name']!=f['name']:raise DomainError('同じIDの冷蔵庫名が異なります。移行を中止しました')
    for b in imported['bottles']:
        existing=next((x for x in state['bottles'] if x['id']==b['id']),None)
        if existing is not None:
            if existing.get('importOrigin')!='personal':raise DomainError('瓶IDが既存データと重複しています')
            skipped+=1
        else:
            state['bottles'].append(dict(b,version=1,importOrigin='personal'));added+=1
    return dict(added=added,skipped=skipped)
