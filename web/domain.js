import {mergePersonalSnapshot} from './migration.js';
export const emptyState = () => ({schema:1, refrigerators:[], bottles:[], events:[]});
const fail = message => { throw new Error(message); };
export const id = () => crypto.randomUUID();
const text = (v, max=200) => typeof v === 'string' && v.trim().length <= max ? v.trim() : fail('入力文字数が不正です');
const integer = (v,min,max) => Number.isInteger(v) && v>=min && v<=max ? v : fail('数量が不正です');
export class ConsumptionPolicy {
  static unit(volume) { return volume === 300 ? 300 : 180; }
  static label(volume) { return volume === 300 ? '1本' : '1合'; }
}
export class Bottle {
  constructor(data) { Object.assign(this, data); }
  consume(at) {
    const amount = ConsumptionPolicy.unit(this.volume);
    if(this.remaining < amount) fail('残量が不足しています');
    this.remaining -= amount;
    this.openedAt ||= at;
    return amount;
  }
}
export class InventoryService {
  static apply(original, command) {
    const s = structuredClone(original);
    if(s.events.some(e=>e.id===command.id)) return s;
    const {type, payload:p={}} = command;
    const event = {id:command.id, type, at:command.at, actor:command.actor || '個人', before:null, after:null};
    if(!command.id || !command.at) fail('操作IDがありません');
    if(type==='importPersonal') {
      const result=mergePersonalSnapshot(s,p.snapshot);event.name='個人データの移行';event.added=result.added;event.skipped=result.skipped;
    } else if(type==='addFridge') {
      const name=text(p.name,80); if(!name) fail('冷蔵庫名を入力してください');
      if(s.refrigerators.some(f=>f.id===p.id)) fail('冷蔵庫IDが重複しています');
      s.refrigerators.push({id:p.id, name}); event.name=name;
    } else if(type==='receive') {
      const name=text(p.name); if(!name) fail('銘柄を入力してください');
      const volume=integer(p.volume,1,10000), count=integer(p.count,1,100);
      if(!s.refrigerators.some(f=>f.id===p.fridgeId)) fail('保管する冷蔵庫を選択してください');
      if(!Array.isArray(p.ids)||p.ids.length!==count||new Set(p.ids).size!==count||p.ids.some(i=>s.bottles.some(b=>b.id===i))) fail('瓶IDが不正です');
      const photo = p.photo || '';
      if(photo && (!/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(photo) || photo.length>1600000)) fail('写真が大きすぎるか形式が不正です');
      for(const bid of p.ids) s.bottles.push({id:bid,name,brewery:text(p.brewery||''),category:text(p.category||''),productionDate:text(p.productionDate||'',40),volume,remaining:volume,fridgeId:p.fridgeId,shelf:text(p.shelf||'',80),photo,receivedAt:command.at,openedAt:null,version:1});
      event.name=name; event.bottleIds=p.ids; event.amount=volume*count; event.count=count;
    } else {
      const index=s.bottles.findIndex(b=>b.id===p.bottleId); if(index<0) fail('対象の瓶が見つかりません');
      const b=new Bottle(s.bottles[index]);
      if(b.version!==p.version) fail('別の操作で在庫が更新されています。最新状態を取得してください');
      event.bottleId=b.id; event.name=b.name; event.before=structuredClone(b);
      if(type==='consume') event.amount=b.consume(command.at);
      else if(type==='finish') { if(b.remaining<=0 || b.remaining>=ConsumptionPolicy.unit(b.volume)) fail('端数残量だけ飲み切り登録できます'); event.amount=b.remaining; b.remaining=0; b.openedAt ||= command.at; }
      else if(type==='move') { if(!s.refrigerators.some(f=>f.id===p.fridgeId)) fail('移動先がありません'); b.fridgeId=p.fridgeId; b.shelf=text(p.shelf||'',80); }
      else if(type==='undo') {
        const e=s.events.find(e=>e.id===p.eventId);
        if(!e || !['consume','finish','move'].includes(e.type) || e.bottleId!==b.id || e.after.version!==b.version || s.events.some(x=>x.undoOf===e.id)) fail('後続の操作があるため取り消せません');
        Object.assign(b,e.before); b.version=p.version; event.undoOf=e.id;
      } else fail('未対応の操作です');
      b.version++; event.after=structuredClone(b); s.bottles[index]=structuredClone(b);
    }
    event.command=structuredClone(command); s.events.push(event); return s;
  }
}
export function remainingLabel(b) { return b.volume===300 ? (b.remaining ? '1本' : '空瓶') : `${Number((b.remaining/180).toFixed(2))}合`; }
