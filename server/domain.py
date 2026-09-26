"""Domain rules independent of HTTP and database implementations."""
import copy
import re
from datetime import datetime, timezone

class DomainError(ValueError):
    pass

def empty_state():
    return {'schema': 1, 'refrigerators': [], 'bottles': [], 'events': []}

def string(value, maximum=200):
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise DomainError('入力文字数が不正です')
    return value.strip()

def number(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DomainError('数量が不正です')
    return value

class ConsumptionPolicy:
    @staticmethod
    def unit(volume):
        return 300 if volume == 300 else 180

class InventoryService:
    def apply(self, original, command):
        s = copy.deepcopy(original)
        cid = string(command.get('id', ''), 80)
        if not cid:
            raise DomainError('操作IDがありません')
        if any(e['id'] == cid for e in s['events']):
            return s
        p = command.get('payload', {})
        kind = command.get('type')
        now = datetime.now(timezone.utc).isoformat()
        e = dict(id=cid, type=kind, at=now, actor=command['actor'], before=None, after=None)
        if kind == 'importPersonal':
            from .migration import merge_personal_snapshot
            result=merge_personal_snapshot(s,p.get('snapshot'))
            e.update(name='個人データの移行',**result)
        elif kind == 'addFridge':
            name = string(p.get('name', ''), 80)
            fid = string(p.get('id', ''), 80)
            if not name or not fid or any(f['id'] == fid for f in s['refrigerators']):
                raise DomainError('冷蔵庫名またはIDが不正です')
            s['refrigerators'].append(dict(id=fid, name=name))
            e['name'] = name
        elif kind == 'receive':
            name = string(p.get('name', ''))
            if not name:
                raise DomainError('銘柄を入力してください')
            volume, count = number(p.get('volume'), 1, 10000), number(p.get('count'), 1, 100)
            if not any(f['id'] == p.get('fridgeId') for f in s['refrigerators']):
                raise DomainError('保管する冷蔵庫を選択してください')
            ids = p.get('ids', [])
            if not isinstance(ids, list) or len(ids) != count or any(not isinstance(i,str) or not i or len(i)>80 for i in ids) or len(set(ids)) != count or any(b['id'] in ids for b in s['bottles']):
                raise DomainError('瓶IDが不正です')
            photo = p.get('photo', '')
            if not isinstance(photo,str) or len(photo)>1600000 or (photo and not re.fullmatch(r'data:image/jpeg;base64,[A-Za-z0-9+/=]+',photo)):
                raise DomainError('写真形式が不正です')
            for bid in ids:
                s['bottles'].append(dict(id=bid, name=name, brewery=string(p.get('brewery','')), category=string(p.get('category','')), productionDate=string(p.get('productionDate',''),40), volume=volume, remaining=volume, fridgeId=p['fridgeId'], shelf=string(p.get('shelf',''),80), photo=photo, receivedAt=now, openedAt=None, version=1))
            e.update(name=name, bottleIds=ids, amount=volume*count, count=count)
        else:
            b = next((b for b in s['bottles'] if b['id']==p.get('bottleId')), None)
            if b is None:
                raise DomainError('対象の瓶が見つかりません')
            if b['version'] != p.get('version'):
                raise DomainError('別の操作で在庫が更新されています。最新状態を取得してください')
            e.update(bottleId=b['id'], name=b['name'], before=copy.deepcopy(b))
            if kind == 'consume':
                amount=ConsumptionPolicy.unit(b['volume'])
                if b['remaining']<amount:
                    raise DomainError('残量が不足しています')
                b['remaining']-=amount
                b['openedAt']=b['openedAt'] or now
                e['amount']=amount
            elif kind == 'finish':
                if not 0 < b['remaining'] < ConsumptionPolicy.unit(b['volume']):
                    raise DomainError('端数残量だけ飲み切り登録できます')
                e['amount']=b['remaining']; b['remaining']=0; b['openedAt']=b['openedAt'] or now
            elif kind == 'move':
                if not any(f['id']==p.get('fridgeId') for f in s['refrigerators']):
                    raise DomainError('移動先がありません')
                b['fridgeId']=p['fridgeId']; b['shelf']=string(p.get('shelf',''),80)
            elif kind == 'undo':
                target=next((x for x in s['events'] if x['id']==p.get('eventId')),None)
                if not target or target['type'] not in ('consume','finish','move') or target['bottleId']!=b['id'] or target['after']['version']!=b['version'] or any(x.get('undoOf')==target['id'] for x in s['events']):
                    raise DomainError('後続の操作があるため取り消せません')
                b.update(target['before']); b['version']=p['version']; e['undoOf']=target['id']
            else:
                raise DomainError('未対応の操作です')
            b['version']+=1
            e['after']=copy.deepcopy(b)
        s['events'].append(e)
        return s
