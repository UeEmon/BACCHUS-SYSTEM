import {InventoryService,ConsumptionPolicy,remainingLabel,emptyState,id} from './domain.js';
import {validateSnapshot} from './migration.js';
import {LocalRepository,ServerRepository,ApiClient,RecognitionService} from './repositories.js';
import {createAdmin,ROLES} from './admin.js';
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state=emptyState(), repo=new LocalRepository(), mode='personal', actor='個人', role='', tenantName='', photo='', busy=false, moving=null;
let api=new ApiClient(sessionStorage.getItem('bacchus-token')||'');
let toastTimer;
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,7000);}
function options(selected='',all=false){return `${all?'<option value="">すべての冷蔵庫</option>':''}${state.refrigerators.map(f=>`<option value="${esc(f.id)}" ${f.id===selected?'selected':''}>${esc(f.name)}</option>`).join('')}`;}
// Fridge layout is the tenant administrator's in shared mode; on a personal device it is the owner's.
const canManageFridges=()=>mode==='personal'||role==='tenant_admin';
function location(b){return `${state.refrigerators.find(f=>f.id===b.fridgeId)?.name||(b.fridgeId?'削除済みの冷蔵庫':'未設定')}${b.shelf?' / '+b.shelf:''}`;}
function date(value){return new Date(value).toLocaleString('ja-JP',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
async function render(){
  $('#bottleCount').textContent=state.bottles.filter(b=>b.remaining>0).length;
  const pending=repo instanceof ServerRepository?await repo.pendingCount():0;
  $('#modeLabel').textContent=mode==='shared'?`共用・${tenantName||'サーバー保存'}${ROLES[role]?`・${ROLES[role]}`:''}${pending?`（${pending}件保留）`:''}`:'個人用・端末保存';
  $('#syncStatus').textContent=mode==='shared'?`${pending}件の保留操作。${repo.syncError||'同期済みの在庫と端末の保留分を表示します。'}`:'個人用は端末に保存しています。';
  const selected=$('#fridgeFilter').value;$('#fridgeFilter').innerHTML=options(selected,true);
  const query=$('#search').value.toLowerCase(),fridge=$('#fridgeFilter').value;
  const bottles=state.bottles.filter(b=>(b.remaining>0||$('#showEmpty').checked)&&(!fridge||b.fridgeId===fridge)&&`${b.name} ${b.brewery}`.toLowerCase().includes(query));
  $('#bottles').innerHTML=bottles.map(b=>`<article class="card"><div class="bottleTop">${b.photo?`<img src="${esc(b.photo)}" alt="${esc(b.name)}のラベル">`:'<div class="placeholder" aria-hidden="true">🍶</div>'}<div><span class="badge">${b.volume} ml · ${b.openedAt?'開封済み':'未開封'}</span><h3>${esc(b.name)}</h3><div class="meta">${esc(b.brewery||'酒蔵未登録')}<br>${esc(location(b))}</div></div></div><p class="remaining">${remainingLabel(b)} <small>残量 ${b.remaining} ml</small></p><progress value="${b.remaining}" max="${b.volume}" aria-label="残量"></progress><div class="actions"><button data-consume="${esc(b.id)}" ${b.remaining===0?'disabled':''}>${b.remaining>0&&b.remaining<ConsumptionPolicy.unit(b.volume)?'残りを飲み切る':ConsumptionPolicy.label(b.volume)+'消費'}</button><button data-move="${esc(b.id)}" class="quiet">移動</button></div></article>`).join('')||'<div class="empty"><strong>日本酒を迎えましょう</strong>冷蔵庫を追加して、最初の一本を登録。<br>検索中の場合は条件を変更してください。</div>';
  $('#fridgeList').innerHTML=state.refrigerators.map(f=>{const bs=state.bottles.filter(b=>b.fridgeId===f.id&&b.remaining>0);return `<article class="card"><span class="badge">冷蔵庫</span><h3>${esc(f.name)}</h3><p>${bs.length}本 <span class="muted">／ ${bs.reduce((n,b)=>n+b.remaining,0).toLocaleString()} ml</span></p><button class="secondary full" data-filter="${esc(f.id)}">この冷蔵庫を見る</button>${canManageFridges()?`<div class="actions"><button class="quiet" data-rename-fridge="${esc(f.id)}">名前を変更</button><button class="quiet" data-delete-fridge="${esc(f.id)}">削除</button></div>`:''}</article>`;}).join('')||(canManageFridges()?'<div class="empty">「＋ 冷蔵庫」から保管場所を追加してください。</div>':'<div class="empty">冷蔵庫はテナント管理者が追加します。</div>');
  $('#addFridge').hidden=!canManageFridges();$('#migrateSection').hidden=mode==='shared'&&role!=='tenant_admin';
  const names={receive:'入荷',consume:'消費',finish:'飲み切り',move:'移動',undo:'取り消し',addFridge:'冷蔵庫追加',renameFridge:'冷蔵庫名変更',deleteFridge:'冷蔵庫削除',importPersonal:'個人データ移行'};
  $('#eventList').innerHTML=state.events.slice().reverse().slice(0,200).map(e=>{const b=state.bottles.find(b=>b.id===e.bottleId);const undo=['consume','finish','move'].includes(e.type)&&b?.version===e.after?.version&&!state.events.some(x=>x.undoOf===e.id);return `<div class="event"><div><small>${esc(date(e.at))} · ${esc(e.actor)}</small><p><b>${names[e.type]||esc(e.type)}</b> ${esc(e.name)} ${e.amount?`· ${e.amount}ml`:''}${e.type==='importPersonal'?` · ${e.added}本追加／${e.skipped}本移行済み`:''}</p>${e.type==='move'?`<small>${esc(location(e.before))} → ${esc(location(e.after))}</small>`:''}</div>${undo?`<button class="quiet" data-undo="${esc(e.id)}">取消</button>`:''}</div>`;}).join('')||'<div class="empty">履歴はまだありません。</div>';
}
async function guard(fn){if(busy)return;busy=true;document.body.setAttribute('aria-busy','true');try{await fn();}catch(e){toast(e.message||'操作できませんでした');}finally{busy=false;document.body.removeAttribute('aria-busy');}}
async function execute(type,payload){const command={id:id(),type,payload,actor,at:new Date().toISOString()};state=await repo.execute(command);await render();toast(repo.syncError?`端末に保留しました。${repo.syncError}`:mode==='shared'&&!navigator.onLine?'通信復旧後に同期します':'記録しました');}
function tab(name){for(const n of ['inventory','fridges','history'])$('#'+n).hidden=n!==name;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b.dataset.tab===name));}
function openRegister(){if(!state.refrigerators.length){if(!canManageFridges()){toast('冷蔵庫がありません。テナント管理者に追加を依頼してください');return;}toast('先に冷蔵庫を追加してください');$('#fridgeDialog').showModal();return;}$('#registerForm').reset();photo='';$('#photoInput').value='';$('#photoPreview').hidden=true;$('#recognize').disabled=true;$('#aiStatus').textContent='';$('#unitHint').textContent='消費は1合（180ml）単位';$('#registerForm [name=fridgeId]').innerHTML=options(localStorage.getItem('lastFridge')||state.refrigerators[0].id);$('#registerForm [name=shelf]').value=localStorage.getItem('lastShelf')||'';$('#registerDialog').showModal();}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.close)$('#'+b.dataset.close).close();if(b.dataset.tab)tab(b.dataset.tab);if(b.dataset.filter){$('#fridgeFilter').value=b.dataset.filter;tab('inventory');render();}
if(b.dataset.consume)guard(async()=>{const bottle=state.bottles.find(x=>x.id===b.dataset.consume);const type=bottle.remaining<ConsumptionPolicy.unit(bottle.volume)?'finish':'consume';await execute(type,{bottleId:bottle.id,version:bottle.version});});
if(b.dataset.move){moving=state.bottles.find(x=>x.id===b.dataset.move);const form=$('#moveForm');form.elements.bottleId.value=moving.id;form.elements.version.value=moving.version;form.elements.fridgeId.innerHTML=options(moving.fridgeId);form.elements.shelf.value=moving.shelf;$('#moveDialog').showModal();}
if(b.dataset.renameFridge)guard(async()=>{const f=state.refrigerators.find(x=>x.id===b.dataset.renameFridge),name=prompt('新しい冷蔵庫名',f.name);if(name===null||name.trim()===f.name)return;await execute('renameFridge',{id:f.id,name});});
if(b.dataset.deleteFridge)guard(async()=>{const f=state.refrigerators.find(x=>x.id===b.dataset.deleteFridge);if(!confirm(`冷蔵庫「${f.name}」を削除します。空瓶の記録は残ります。よろしいですか？`))return;await execute('deleteFridge',{id:f.id});});
if(b.dataset.undo)guard(async()=>{const event=state.events.find(x=>x.id===b.dataset.undo),bottle=state.bottles.find(x=>x.id===event.bottleId);await execute('undo',{bottleId:bottle.id,version:bottle.version,eventId:event.id});});});
$('#registerButton').onclick=openRegister;$('#addFridge').onclick=()=>$('#fridgeDialog').showModal();
for(const el of ['search','fridgeFilter','showEmpty'])$('#'+el).addEventListener('input',()=>render());
$('#refresh').onclick=()=>guard(async()=>{state=await repo.read();await render();if(repo.syncError)toast(repo.syncError);});
$('#fridgeForm').onsubmit=e=>{e.preventDefault();guard(async()=>{await execute('addFridge',{id:id(),name:e.target.elements.name.value});e.target.reset();$('#fridgeDialog').close();});};
$('#registerForm').onsubmit=e=>{e.preventDefault();guard(async()=>{const p=Object.fromEntries(new FormData(e.target));p.volume=Number(p.volume);p.count=Number(p.count);p.ids=Array.from({length:p.count},id);p.photo=photo;await execute('receive',p);localStorage.setItem('lastFridge',p.fridgeId);localStorage.setItem('lastShelf',p.shelf);$('#registerDialog').close();tab('inventory');});};
$('#moveForm').onsubmit=e=>{e.preventDefault();guard(async()=>{const p=Object.fromEntries(new FormData(e.target));p.version=Number(p.version);await execute('move',p);$('#moveDialog').close();});};
$('#registerForm [name=volume]').oninput=e=>$('#unitHint').textContent=Number(e.target.value)===300?'消費は1本（300ml）単位':'消費は1合（180ml）単位。端数は「残りを飲み切る」で記録できます。';
async function resizePhoto(file){
  if(!file||file.size>20000000)throw new Error('20MB以下の画像を選択してください');
  const url=URL.createObjectURL(file);try{const img=new Image();img.src=url;await img.decode();const scale=Math.min(1,1280/Math.max(img.width,img.height));const canvas=document.createElement('canvas');canvas.width=Math.round(img.width*scale);canvas.height=Math.round(img.height*scale);canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);const data=canvas.toDataURL('image/jpeg',.8);if(data.length>1600000)throw new Error('写真が大きすぎます。近づいて撮り直してください');return data;}finally{URL.revokeObjectURL(url);}}
$('#photoInput').onchange=e=>guard(async()=>{if(!e.target.files[0])return;photo=await resizePhoto(e.target.files[0]);$('#photoPreview').src=photo;$('#photoPreview').hidden=false;$('#recognize').disabled=false;$('#aiStatus').textContent='写真を確認してAI解析、または手入力してください。';});
$('#recognize').onclick=()=>guard(async()=>{if(!api.token)throw new Error('「設定」でテナントの利用者としてログインするとAIを使えます。写真を保存して手入力登録もできます。');$('#recognize').disabled=true;$('#aiStatus').textContent='ラベルを読み取っています…';try{const result=await new RecognitionService(api).recognize(photo);for(const key of ['name','brewery','category','productionDate','volume']){if(result[key]!==null&&result[key]!==undefined&&result[key]!=='')$('#registerForm').elements[key].value=result[key];}if(result.volume===null)$('#registerForm').elements.volume.value='';$('#registerForm [name=volume]').dispatchEvent(new Event('input'));$('#aiStatus').textContent=`読み取り完了（${result.provider}・${result.model}）。銘柄・容量・製造年月を確認してください。${result.notes||''} 読み取りが不正確な場合は「設定」でより高性能なモデルを選べます。`;}catch(e){$('#aiStatus').textContent=e.message;throw e;}finally{$('#recognize').disabled=false;}});
// Sign-in by password, SSO or a fixed operator token. The session lives in this tab only.
let session=JSON.parse(sessionStorage.getItem('bacchus-session')||'null');
const admin=createAdmin({$,esc,guard,toast,api:()=>api});
function showSession(){const s=session;role=s?.role||'';tenantName=s?.tenant?.name||'';$('#loggedOut').hidden=Boolean(s);$('#loggedIn').hidden=!s;
  $('#sessionInfo').textContent=s?`${s.user}${s.email?`（${s.email}）`:''}・${ROLES[role]||role}${tenantName?`・${tenantName}`:''}としてログイン中`:'';
  $('#openAdmin').hidden=!['system_admin','tenant_admin'].includes(role);$('#openAdmin').textContent=role==='system_admin'?'テナント管理':'テナントの管理（利用者・ログイン方式・AI利用量・監査ログ）';
  $('#openPassword').hidden=s?.source!=='session';$('#openSecurity').hidden=s?.source!=='session';$('#aiForm').hidden=role!=='tenant_admin';}
async function useShared(){if(!session?.workspace)throw new Error('共用の在庫を使うには、テナントの利用者でログインしてください');localStorage.setItem('bacchus-workspace',session.workspace);repo=new ServerRepository(api,session.workspace);mode='shared';actor=session.user;localStorage.setItem('bacchus-mode',mode);state=await repo.read();}
async function usePersonal(){repo=new LocalRepository();mode='personal';actor='個人';localStorage.setItem('bacchus-mode',mode);state=await repo.read();}
async function signedIn(token){const client=new ApiClient(token),info=await client.request('/api/session');api=client;session=info;sessionStorage.setItem('bacchus-token',token);sessionStorage.setItem('bacchus-session',JSON.stringify(info));showSession();$('#settingsDialog').close();
  if(info.mustChangePassword){openPassword(true);return;}
  if(info.mfaSetupRequired){await openSecurity(true);return;}
  if(info.workspace)await useShared();else await usePersonal();await render();toast(`${info.user}としてログインしました`);}
async function signOut(){try{await api.request('/api/auth/logout',{method:'POST',body:'{}'});}catch{}api=new ApiClient('');session=null;sessionStorage.removeItem('bacchus-token');sessionStorage.removeItem('bacchus-session');showSession();await usePersonal();await render();}
function openPassword(forced){$('#passwordForm').reset();$('#passwordReason').textContent=forced?'初回ログインのため、一時パスワードを新しいパスワードに変更してください。':'';$('#passwordCancel').hidden=forced;$('#passwordDialog').showModal();}
$('#settingsButton').onclick=()=>{$('#storageMode').value=mode;$('#aiKey').value='';showSession();$('#settingsDialog').showModal();if(role==='tenant_admin')loadAiSettings();};
let challenge='';
function showMfaStep(on){$('#loginForm').hidden=on;$('#mfaForm').hidden=!on;if(on){$('#mfaCode').value='';$('#mfaCode').focus();}}
$('#loginForm').onsubmit=e=>{e.preventDefault();guard(async()=>{const result=await new ApiClient('').request('/api/auth/login',{method:'POST',body:JSON.stringify({email:$('#loginEmail').value,password:$('#loginPassword').value})});$('#loginPassword').value='';
  if(result.mfaRequired){challenge=result.challenge;showMfaStep(true);return;}await signedIn(result.session);});};
$('#mfaForm').onsubmit=e=>{e.preventDefault();guard(async()=>{try{const result=await new ApiClient('').request('/api/auth/mfa/verify',{method:'POST',body:JSON.stringify({challenge,code:$('#mfaCode').value})});challenge='';showMfaStep(false);await signedIn(result.session);}catch(error){if(/もう一度ログイン|有効期限/.test(error.message))showMfaStep(false);throw error;}});};
$('#mfaCancel').onclick=()=>{challenge='';showMfaStep(false);};
$('#ssoLogin').onclick=()=>guard(async()=>{const email=$('#loginEmail').value.trim();if(!email)throw new Error('メールアドレスを入力してください');const result=await new ApiClient('').request('/api/auth/sso/start',{method:'POST',body:JSON.stringify({email})});location.assign(result.url);});
$('#tokenForm').onsubmit=e=>{e.preventDefault();guard(async()=>{await signedIn($('#token').value.trim());$('#token').value='';});};
$('#logout').onclick=()=>guard(async()=>{await signOut();toast('ログアウトしました');});
$('#openPassword').onclick=()=>openPassword(false);
$('#passwordCancel').onclick=()=>$('#passwordDialog').close();
// A required change cannot be dismissed: the server refuses everything else until it is done.
$('#passwordDialog').addEventListener('cancel',e=>{if(session?.mustChangePassword)e.preventDefault();});
$('#passwordForm').onsubmit=e=>{e.preventDefault();const f=Object.fromEntries(new FormData(e.target));guard(async()=>{if(f.newPassword!==f.confirm)throw new Error('確認用のパスワードが一致しません');await api.request('/api/auth/password',{method:'POST',body:JSON.stringify({currentPassword:f.currentPassword,newPassword:f.newPassword})});const forced=session.mustChangePassword;$('#passwordDialog').close();toast('パスワードを変更しました');if(forced)await signedIn(api.token);});};
$('#openAdmin').onclick=()=>admin.open(role==='system_admin'?'system':'tenant');
// Multi-factor sign-in and the phones registered for biometric sign-in.
let securityForced=false;
async function openSecurity(forced){securityForced=forced;$('#securityReason').hidden=!forced;$('#mfaSetup').hidden=true;$('#recoveryCodes').hidden=true;$('#settingsDialog').close();await loadSecurity();$('#securityDialog').showModal();}
async function loadSecurity(){const mfa=await api.request('/api/auth/mfa');
  $('#mfaStatus').textContent=mfa.enabled?`設定済み（回復コード残り${mfa.recoveryCodesLeft}個）${mfa.required?'。このアカウントでは必須です。':''}`:mfa.required?'未設定（このアカウントでは必須です）':'未設定（任意）';
  $('#mfaStart').hidden=mfa.enabled;$('#mfaRegenerate').hidden=!mfa.enabled;$('#mfaDisable').hidden=!mfa.enabled||mfa.required;
  $('#deviceSection').hidden=securityForced;if(securityForced)return;
  const devices=await api.request('/api/auth/devices');
  $('#deviceList').innerHTML=devices.map(d=>`<li><div><b>${esc(d.name)}</b> <span class="badge">${esc(d.platform)}</span><br><small class="muted">登録 ${esc(new Date(d.createdAt).toLocaleString('ja-JP'))}${d.lastUsedAt?` ・ 最終利用 ${esc(new Date(d.lastUsedAt).toLocaleString('ja-JP'))}`:''}</small></div><div class="actions"><button class="quiet" data-revoke-device="${esc(d.id)}" data-name="${esc(d.name)}">登録解除</button></div></li>`).join('')||'<li class="muted">登録された端末はありません。専用アプリでログインすると登録できます。</li>';}
function showRecoveryCodes(codes){$('#recoveryCodes').hidden=false;$('#recoveryCodes').innerHTML=`<p><b>回復コード</b>：認証アプリを使えないときに、確認コードの代わりに1回ずつ使えます。今だけ表示します。安全な場所に保管してください。</p><pre class="codes">${codes.map(esc).join('\n')}</pre><button type="button" id="copyCodes" class="secondary full">コピー</button>`;}
$('#openSecurity').onclick=()=>guard(()=>openSecurity(false));
$('#securityClose').onclick=()=>guard(async()=>{if(securityForced&&!(await api.request('/api/auth/mfa')).enabled)throw new Error('多要素認証の設定が必要です。設定しない場合はログアウトしてください');$('#securityDialog').close();if(securityForced){securityForced=false;await signedIn(api.token);}});
$('#securityDialog').addEventListener('cancel',e=>{if(securityForced)e.preventDefault();});
$('#mfaStart').onclick=()=>guard(async()=>{const setup=await api.request('/api/auth/mfa/setup',{method:'POST',body:'{}'});$('#mfaQr').innerHTML=setup.qrSvg;$('#mfaSecret').textContent=setup.secret.replace(/(.{4})/g,'$1 ').trim();$('#mfaLink').href=setup.uri;$('#mfaSetup').hidden=false;$('#mfaStart').hidden=true;});
$('#mfaEnableForm').onsubmit=e=>{e.preventDefault();guard(async()=>{const result=await api.request('/api/auth/mfa/enable',{method:'POST',body:JSON.stringify({code:e.target.elements.code.value})});e.target.reset();$('#mfaSetup').hidden=true;showRecoveryCodes(result.recoveryCodes);await loadSecurity();toast('多要素認証を設定しました。回復コードを保管してから閉じてください');});};
$('#mfaRegenerate').onclick=()=>guard(async()=>{const code=prompt('認証アプリの6桁のコード、または未使用の回復コードを入力してください');if(!code)return;const result=await api.request('/api/auth/mfa/recovery-codes',{method:'POST',body:JSON.stringify({code})});showRecoveryCodes(result.recoveryCodes);await loadSecurity();});
$('#mfaDisable').onclick=()=>guard(async()=>{const code=prompt('多要素認証を無効にします。認証アプリの6桁のコード、または回復コードを入力してください');if(!code)return;await api.request('/api/auth/mfa/disable',{method:'POST',body:JSON.stringify({code})});$('#recoveryCodes').hidden=true;await loadSecurity();toast('多要素認証を無効にしました');});
$('#securityDialog').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
  if(b.id==='copyCodes')guard(async()=>{await navigator.clipboard.writeText($('#recoveryCodes pre').textContent);toast('回復コードをコピーしました');});
  if(b.dataset.revokeDevice)guard(async()=>{if(!confirm(`「${b.dataset.name}」の登録を解除します。その端末では生体認証でログインできなくなります。よろしいですか？`))return;await api.request('/api/auth/devices/revoke',{method:'POST',body:JSON.stringify({deviceId:b.dataset.revokeDevice})});await loadSecurity();toast('登録を解除しました');});});
const AI={gemini:{key:'Google AI Studioで発行したキー',auto:'最新の安定版Flash-Lite（速く安価）'},claude:{key:'Claude Consoleで発行したキー',auto:'最新のHaiku（速く安価）'},openai:{key:'OpenAI Platformで発行したキー',auto:'最新のnano（速く安価）'}};let aiSettings=null;
function showAiProvider(){const id=$('#aiProvider').value,ai=aiSettings?.providers.find(p=>p.id===id),sources={web:'この画面で設定',env:'サーバーの環境変数で設定',none:'未設定'};$('#aiKeyLabel').textContent=`${ai?.label||''} APIキー`;$('#aiKey').placeholder=AI[id].key;$('#aiModel').innerHTML=`<option value="auto">自動（推奨）：${AI[id].auto}</option>${(ai?.models||[]).map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('')}`;$('#aiModel').value=!ai||ai.auto||!ai.models.includes(ai.model)?'auto':ai.model;$('#aiClear').hidden=ai?.source!=='web';if(!aiSettings)return;const active=aiSettings.providers.find(p=>p.id===aiSettings.provider);$('#aiSettingsStatus').textContent=(active.configured?`読み取りに使用中：${active.label}・${active.model}`:'未設定です。AIサービスを選んでAPIキーを保存してください')+(ai.configured?`。${ai.label}：${ai.model}（${ai.auto?'自動選択':'手動選択'}・${sources[ai.source]}${ai.keyHint?`・キー ${ai.keyHint}`:''}）`:`。${ai.label}：未設定`);}
function showAiSettings(ai){aiSettings=ai;$('#aiProvider').value=ai.provider;showAiProvider();}
$('#aiProvider').onchange=showAiProvider;
async function loadAiSettings(){try{showAiSettings(await api.request('/api/ai-settings'));}catch(e){$('#aiSettingsStatus').textContent=e.message;}}
$('#aiForm').onsubmit=e=>{e.preventDefault();guard(async()=>{if(!api.token)throw new Error('先にログインしてください');const provider=$('#aiProvider').value;$('#aiSettingsStatus').textContent=`${$('#aiProvider').selectedOptions[0].textContent}に接続してモデルを確認しています…`;try{showAiSettings(await api.request('/api/ai-settings',{method:'POST',body:JSON.stringify({provider,apiKey:$('#aiKey').value.trim(),model:$('#aiModel').value})}));}catch(error){await loadAiSettings();$('#aiProvider').value=provider;showAiProvider();throw error;}$('#aiKey').value='';toast('AI設定を保存しました');});};
$('#aiClear').onclick=()=>guard(async()=>{const provider=$('#aiProvider').value,label=$('#aiProvider').selectedOptions[0].textContent;if(!confirm(`保存した${label}のAPIキーを削除します。このサービスでのAI解析は使えなくなります（手入力は使えます）。よろしいですか？`))return;showAiSettings(await api.request('/api/ai-settings',{method:'POST',body:JSON.stringify({clear:true,provider})}));toast('APIキーを削除しました');});
$('#settingsForm').onsubmit=e=>{e.preventDefault();guard(async()=>{if($('#storageMode').value==='shared')await useShared();else await usePersonal();await render();$('#settingsDialog').close();toast('保存先を切り替えました');});};
function download(data,name){const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('#export').onclick=()=>guard(async()=>{const envelope={application:'BACCHUS-SYSTEM',schema:1,mode,exportedAt:new Date().toISOString(),state};if(repo instanceof ServerRepository)envelope.shared=await repo.cache.read();download(envelope,`bacchus-backup-${new Date().toISOString().slice(0,10)}.json`);toast('バックアップを保存しました');});
function validateBackup(data){if(data.application!=='BACCHUS-SYSTEM'||data.schema!==1||data.mode!=='personal'||!Array.isArray(data.state?.events))throw new Error('個人用のBACCHUSバックアップを選択してください');let reconstructed=emptyState();for(const event of data.state.events){if(!event.command)throw new Error('このバックアップは復元形式に対応していません');reconstructed=InventoryService.apply(reconstructed,event.command);}return reconstructed;}
$('#import').onchange=e=>guard(async()=>{const f=e.target.files[0];try{if(!f)return;if(mode!=='personal')throw new Error('個人用モードに切り替えてください');if(f.size>50000000)throw new Error('バックアップが大きすぎます');const restored=validateBackup(JSON.parse(await f.text()));if(!confirm('現在の個人用在庫をバックアップの内容に置き換えます。よろしいですか？'))return;state=await repo.change(()=>restored);await render();toast('個人データを復元しました');}finally{e.target.value='';}});
$('#sync').onclick=()=>guard(async()=>{if(!(repo instanceof ServerRepository))throw new Error('共用モードで使用できます');await repo.flush();state=await repo.read();await render();if(repo.syncError)throw new Error(repo.syncError);toast('同期しました');});
$('#discardPending').onclick=()=>guard(async()=>{if(!(repo instanceof ServerRepository))throw new Error('共用モードで使用できます');if(!confirm('保留中の操作をすべて破棄して、サーバーの状態を取得します。必要なら先にバックアップしてください。'))return;state=await repo.discardPending();await render();toast('サーバーの状態を取得しました');});
async function connectionChanged(){$('#offline').hidden=navigator.onLine;if(navigator.onLine&&repo instanceof ServerRepository&&!busy)await guard(async()=>{state=await repo.read();await render();if(repo.syncError)toast(repo.syncError);});}
window.addEventListener('online',connectionChanged);window.addEventListener('offline',connectionChanged);
async function init(){try{
  // Returning from the identity provider: exchange the one-time code, then drop it from the address bar.
  const hash=new URLSearchParams(location.hash.slice(1));
  if(hash.has('sso')||hash.has('sso_error'))history.replaceState(null,'',location.pathname);
  if(hash.has('sso_error'))toast(hash.get('sso_error'));
  if(hash.has('sso')){const result=await new ApiClient('').request('/api/auth/exchange',{method:'POST',body:JSON.stringify({code:hash.get('sso')})});await signedIn(result.session);}
  else{const workspace=localStorage.getItem('bacchus-workspace');if(localStorage.getItem('bacchus-mode')==='shared'&&workspace){mode='shared';actor=session?.user||'共用利用者';repo=new ServerRepository(api,workspace);}showSession();state=await repo.read();await render();if(repo.syncError)toast(repo.syncError);}
}catch(e){toast(e.message);}$('#offline').hidden=navigator.onLine;if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>toast('オフライン起動にはHTTPS接続が必要です'));}
init();

$('#migratePersonal').onclick=()=>guard(async()=>{
  if(!(repo instanceof ServerRepository))throw new Error('先に共用モードに切り替えてください');
  if(!navigator.onLine)throw new Error('移行は通信可能な状態で実行してください');
  await repo.flush();
  const personal=await new LocalRepository().read(),snapshot=validateSnapshot(personal);
  if(!snapshot.bottles.length)throw new Error('個人用の在庫がありません');
  if(JSON.stringify(snapshot).length>20000000)throw new Error('移行データが大きすぎます（上限20MB）');
  const existing=new Set(state.bottles.map(b=>b.id));const added=snapshot.bottles.filter(b=>!existing.has(b.id)).length;
  if(!confirm(`個人用の${snapshot.bottles.length}本を確認し、新規${added}本と冷蔵庫・写真を共用サーバーにコピーします。共用利用者全員が参照できます。移行済みの瓶は上書きせず、端末のデータも残します。よろしいですか？`))return;
  await execute('importPersonal',{snapshot});
});
