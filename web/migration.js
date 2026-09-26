// Snapshot transfer boundary. Validation never trusts serialized class instances.
export function validateSnapshot(input) {
  const fail=()=>{throw new Error('移行データの形式が不正です');};
  const str=(v,n=200)=>typeof v==='string'&&v.length<=n?v:fail();
  const ident=v=>{const s=str(v,80);return s?s:fail();};
  const num=(v,min,max)=>Number.isInteger(v)&&v>=min&&v<=max?v:fail();
  const date=v=>{str(v,40);return Number.isFinite(Date.parse(v))?v:fail();};
  if(!input||input.schema!==1||!Array.isArray(input.refrigerators)||!Array.isArray(input.bottles)||input.refrigerators.length>100||input.bottles.length>1000)fail();
  const refrigerators=input.refrigerators.map(f=>({id:ident(f.id),name:str(f.name,80)}));
  if(refrigerators.some(f=>!f.name.trim())||new Set(refrigerators.map(f=>f.id)).size!==refrigerators.length)fail();
  const bottles=input.bottles.map(b=>{
    const volume=num(b.volume,1,10000),remaining=num(b.remaining,0,volume),fridgeId=ident(b.fridgeId);
    if(!refrigerators.some(f=>f.id===fridgeId)||(volume===300&&![0,300].includes(remaining)))fail();
    const photo=str(b.photo||'',1600000);if(photo&&!/^data:image\/jpeg;base64,[A-Za-z0-9+/=]+$/.test(photo))fail();
    const openedAt=b.openedAt===null?null:date(b.openedAt);if(remaining<volume&&!openedAt)fail();
    const name=str(b.name);if(!name.trim())fail();
    return {id:ident(b.id),name,brewery:str(b.brewery||''),category:str(b.category||''),productionDate:str(b.productionDate||'',40),volume,remaining,fridgeId,shelf:str(b.shelf||'',80),photo,receivedAt:date(b.receivedAt),openedAt,version:num(b.version,1,1000000000)};
  });
  if(new Set(bottles.map(b=>b.id)).size!==bottles.length)fail();
  return {schema:1,refrigerators,bottles};
}
export function mergePersonalSnapshot(state,snapshot){
  const imported=validateSnapshot(snapshot);let added=0,skipped=0;
  for(const f of imported.refrigerators){const existing=state.refrigerators.find(x=>x.id===f.id);if(!existing)state.refrigerators.push(f);else if(existing.name!==f.name)throw new Error('同じIDの冷蔵庫名が異なります。移行を中止しました');}
  for(const b of imported.bottles){const existing=state.bottles.find(x=>x.id===b.id);if(existing){if(existing.importOrigin!=='personal')throw new Error('瓶IDが既存データと重複しています');skipped++;continue;}state.bottles.push({...b,version:1,importOrigin:'personal'});added++;}
  return {added,skipped};
}
