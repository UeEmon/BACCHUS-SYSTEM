import {emptyState,InventoryService} from './domain.js';
export class LocalRepository {
  constructor(key='personal') { this.key=key; }
  async open() {
    if(this.db) return this.db;
    this.db=await new Promise((resolve,reject)=>{ const q=indexedDB.open('bacchus',1); q.onupgradeneeded=()=>q.result.createObjectStore('states'); q.onsuccess=()=>resolve(q.result); q.onerror=()=>reject(q.error); });
    return this.db;
  }
  async read() { const db=await this.open(); return new Promise((resolve,reject)=>{const q=db.transaction('states').objectStore('states').get(this.key);q.onsuccess=()=>resolve(q.result||emptyState());q.onerror=()=>reject(q.error);}); }
  async change(fn) { const db=await this.open(); return new Promise((resolve,reject)=>{const tx=db.transaction('states','readwrite'), store=tx.objectStore('states'), q=store.get(this.key); let result; q.onsuccess=()=>{try{ result=fn(q.result||emptyState()); store.put(result,this.key); }catch(e){reject(e);tx.abort();}};tx.oncomplete=()=>resolve(result);tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('保存を中止しました'));}); }
  execute(command) { return this.change(s=>InventoryService.apply(s,command)); }
}
export class ApiClient {
  constructor(token) {this.token=token;}
  async request(path, options={}) {
    const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','Authorization':`Bearer ${this.token}`,...options.headers},signal:AbortSignal.timeout(65000)});
    const data=await response.json(); if(!response.ok) throw new Error(data.error||'サーバーに接続できません'); return data;
  }
}
export class ServerRepository {
  constructor(api,workspace) {this.api=api;this.cache=new LocalRepository(`shared:${workspace}`);this.syncError='';}
  project(doc) {try{return (doc.pending||[]).reduce((state,c)=>InventoryService.apply(state,c),doc.base||emptyState());}catch(e){this.syncError=e.message;return doc.preview||doc.base||emptyState();}}
  async pendingCount() {return ((await this.cache.read()).pending||[]).length;}
  async flush() {
    if(this.flushing)return this.flushing;
    this.flushing=this.flushQueue().finally(()=>{this.flushing=null;});return this.flushing;
  }
  async flushQueue() {
    this.syncError='';
    while(true){
      const doc=await this.cache.read(), first=doc.pending?.[0];if(!first)return;
      const base=await this.api.request('/api/commands',{method:'POST',body:JSON.stringify(first)});
      await this.cache.change(current=>({...current,base,pending:(current.pending||[]).filter(c=>c.id!==first.id)}));
    }
  }
  async read() {
    if(navigator.onLine){try{await this.flush();const base=await this.api.request('/api/state');await this.cache.change(doc=>({...doc,base}));}catch(e){this.syncError=e.message;}}
    return this.project(await this.cache.read());
  }
  async execute(command) {
    // Validate before enqueue; persist the stable ID before any network request.
    await this.cache.change(doc=>{const preview=InventoryService.apply(this.project(doc),command);if(this.syncError.includes('更新されています') && (doc.pending||[]).length)throw new Error('保留操作を同期するか、競合を解決してください。');return {...doc,preview,pending:[...(doc.pending||[]),command]};});
    if(navigator.onLine){try{await this.flush();}catch(e){this.syncError=e.message;}}
    return this.project(await this.cache.read());
  }
  async discardPending() {const base=await this.api.request('/api/state');await this.cache.change(()=>({base,pending:[]}));this.syncError='';return base;}
}
export class RecognitionService {
  constructor(api) {this.api=api;}
  recognize(photo) {return this.api.request('/api/recognize',{method:'POST',body:JSON.stringify({photo})});}
}
