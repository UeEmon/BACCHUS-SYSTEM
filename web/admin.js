// Administration screens. System administrators see tenants only; tenant administrators manage
// everything inside their tenant. The server enforces every permission; this only presents it.
export const ROLES={system_admin:'全体管理者',tenant_admin:'テナント管理者',member:'メンバー'};
const ACTIONS={'auth.login':'ログイン','auth.login_failed':'ログイン失敗','auth.logout':'ログアウト','auth.password_change':'パスワード変更','auth.sso_failed':'SSO失敗',
  'tenant.create':'テナント作成','tenant.rename':'テナント名変更','tenant.delete':'テナント削除','tenant.ai_cap':'AI上限（運営）','tenant.add_admin':'テナント管理者の追加',
  'system_admin.create':'全体管理者の追加','system_admin.delete':'全体管理者の削除','user.create':'利用者追加','user.update':'利用者変更','user.delete':'利用者削除',
  'user.password_reset':'パスワード再発行','user.unlink_sso':'SSO連携解除','settings.login':'ログイン方式の変更','settings.ai':'AI設定','settings.ai_clear':'AIキー削除',
  'settings.ai_limit':'AI上限（テナント）','inventory.addFridge':'冷蔵庫追加','inventory.renameFridge':'冷蔵庫名変更','inventory.deleteFridge':'冷蔵庫削除','inventory.importPersonal':'個人データ移行'};
const STATUS={ok:'成功',error:'失敗',rejected:'上限で拒否'};

export function createAdmin({$,esc,guard,toast,api}){
  let data=null,view='';
  const time=v=>v?new Date(v).toLocaleString('ja-JP',{year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—';
  const num=v=>Number(v||0).toLocaleString();
  const tabs=(items)=>`<nav class="adminTabs" aria-label="管理メニュー">${items.map(([id,label])=>`<button type="button" data-view="${id}" class="${id===view?'selected':''}">${label}</button>`).join('')}</nav>`;
  const auditTable=rows=>`<div class="tableWrap"><table><thead><tr><th>日時</th><th>操作者</th><th>操作</th><th>対象</th><th>IP</th></tr></thead><tbody>${rows.map(e=>`<tr><td>${esc(time(e.at))}</td><td>${esc(e.actor)}</td><td>${esc(ACTIONS[e.action]||e.action)}${Object.keys(e.detail||{}).length?`<br><small class="muted">${esc(JSON.stringify(e.detail))}</small>`:''}</td><td>${esc(e.target)}</td><td>${esc(e.ip)}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">記録はまだありません</td></tr>'}</tbody></table></div><p class="muted">新しい順に最大200件を表示します。</p>`;
  const state=u=>[u.source==='env'?'環境変数で設定':'',u.disabled?'無効':'',u.locked?'ロック中':'',u.mustChangePassword?'初回パスワード変更待ち':'',u.ssoLinked?'SSO連携済み':'',u.source!=='env'&&!u.hasPassword?'パスワードなし':''].filter(Boolean).join('・');
  const userRow=(u,actions)=>`<li><div><b>${esc(u.name)}</b> <span class="badge">${esc(ROLES[u.role]||u.role)}</span>${u.id===data.self?' <small>（自分）</small>':''}<br><small class="muted">${esc(u.email||'')}${state(u)?` ・ ${esc(state(u))}`:''}${u.lastLoginAt?` ・ 最終ログイン ${esc(time(u.lastLoginAt))}`:''}</small></div>${u.source==='env'?'':`<div class="actions">${actions(u)}</div>`}</li>`;

  function systemView(){
    const t=data.tenants;
    if(view==='admins')return `<h3>全体管理者</h3><ul class="userList">${data.systemAdmins.map(u=>userRow(u,u=>`<button class="quiet" data-act="resetSystemAdmin" data-user="${esc(u.id)}" data-name="${esc(u.name)}">パスワード再発行</button>${u.id===data.self?'':`<button class="quiet" data-act="deleteSystemAdmin" data-user="${esc(u.id)}" data-name="${esc(u.name)}">削除</button>`}`)).join('')}</ul>
      <form class="adminForm" data-act="createSystemAdmin"><div class="two"><label>名前<input name="name" required maxlength="80"></label><label>メールアドレス<input name="email" type="email" required maxlength="254"></label></div><button type="submit" class="secondary full">全体管理者を追加</button></form>`;
    if(view==='audit')return `<h3>システムの監査ログ</h3>${auditTable(data.audit)}`;
    return `<form class="adminForm" data-act="createTenant"><h3>テナントを追加</h3><label>テナント名<input name="name" required maxlength="80" placeholder="店舗・会社・家族など"></label><div class="two"><label>最初の管理者名<input name="adminName" required maxlength="80"></label><label>管理者のメールアドレス<input name="adminEmail" type="email" required maxlength="254"></label></div><button type="submit" class="secondary full">テナントと管理者を作成</button></form><hr>
      ${t.map(x=>`<section class="card"><span class="badge">テナント</span><h3>${esc(x.name)}</h3><p class="muted">利用者 ${num(x.users)}人・在庫 ${num(x.bottles)}本・今月のAI読み取り ${num(x.aiThisMonth)}回（運営上限 ${x.aiMonthlyCap??'なし'}）<br>作成 ${esc(time(x.createdAt))}</p>
        <div class="actions wrap"><button class="quiet" data-act="renameTenant" data-tenant="${esc(x.id)}" data-name="${esc(x.name)}">名前を変更</button><button class="quiet" data-act="setAiCap" data-tenant="${esc(x.id)}" data-cap="${x.aiMonthlyCap??''}">AI上限</button><button class="quiet" data-act="deleteTenant" data-tenant="${esc(x.id)}" data-name="${esc(x.name)}">削除</button></div>
        <details><summary>テナント管理者を追加（復旧用）</summary><form class="adminForm" data-act="addTenantAdmin" data-tenant="${esc(x.id)}"><div class="two"><label>名前<input name="name" required maxlength="80"></label><label>メールアドレス<input name="email" type="email" required maxlength="254"></label></div><button type="submit" class="secondary full">追加</button></form></details></section>`).join('')||'<div class="empty">テナントはまだありません。</div>'}
      <p class="muted">テナント内の利用者・冷蔵庫・AI設定は各テナントの管理者が管理します。全体管理者はテナントの在庫を閲覧できません。</p>`;
  }
  function tenantView(){
    const l=data.login,u=data.usage;
    if(view==='login')return `<form class="adminForm" data-act="updateLogin"><h3>ログイン方式</h3>
      <label class="check"><input type="checkbox" name="allowPassword" ${l.allowPassword?'checked':''}> メールアドレスとパスワード</label>
      <label class="check"><input type="checkbox" name="allowSso" ${l.allowSso?'checked':''}> SSO（OpenID Connect）</label>
      <label>IDプロバイダーのURL（issuer）<input name="ssoIssuer" type="url" maxlength="500" value="${esc(l.ssoIssuer)}" placeholder="https://accounts.google.com"></label>
      <label>クライアントID<input name="ssoClientId" maxlength="300" value="${esc(l.ssoClientId)}"></label>
      <label>クライアントシークレット<input name="ssoClientSecret" type="password" autocomplete="off" maxlength="500" placeholder="${l.ssoSecretSet?'設定済み（変更するときだけ入力）':''}"></label>
      <p class="muted">IDプロバイダーに登録するリダイレクトURI：<br><code>${esc(l.redirectUri||'サーバーの環境変数BACCHUS_PUBLIC_URLが未設定のためSSOは使えません')}</code><br>SSOでは登録済みのメールアドレスと一致する利用者だけがログインできます。初回ログイン時にIDプロバイダーのアカウントと結び付けます。</p>
      <button type="submit" class="secondary full">保存</button></form>`;
    if(view==='usage')return `<h3>AI利用量（今月）</h3><p class="stat"><strong>${num(u.used)}</strong> 回 ${u.effectiveLimit!=null?`／ 上限 ${num(u.effectiveLimit)} 回`:'（上限なし）'}</p>
      <form class="adminForm" data-act="setAiLimit"><label>テナントの月間上限（回、空欄で無制限）<input name="limit" type="number" min="0" max="1000000" step="1" value="${u.limit??''}"></label><button type="submit" class="secondary full">上限を保存</button></form>
      <p class="muted">${u.cap!=null?`運営による上限：${num(u.cap)}回。テナントの上限と小さい方が適用されます。`:'運営による上限は設定されていません。'}上限に達すると、その月は手入力のみになります。回数は毎月1日（UTC）にリセットされます。</p>
      <div class="tableWrap"><table><thead><tr><th>サービス・モデル</th><th>結果</th><th>回数</th><th>入力トークン</th><th>出力トークン</th></tr></thead><tbody>${u.summary.map(s=>`<tr><td>${esc(s.provider)}<br><small>${esc(s.model)}</small></td><td>${esc(STATUS[s.status]||s.status)}</td><td>${num(s.count)}</td><td>${num(s.inputTokens)}</td><td>${num(s.outputTokens)}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">今月の利用はありません</td></tr>'}</tbody></table></div>
      <h3>最近の利用</h3><div class="tableWrap"><table><thead><tr><th>日時</th><th>利用者</th><th>モデル</th><th>結果</th><th>トークン（入力/出力）</th></tr></thead><tbody>${u.recent.map(r=>`<tr><td>${esc(time(r.at))}</td><td>${esc(r.user)}</td><td>${esc(r.model)}</td><td>${esc(STATUS[r.status]||r.status)}</td><td>${num(r.inputTokens)} / ${num(r.outputTokens)}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">記録はありません</td></tr>'}</tbody></table></div>`;
    if(view==='audit')return `<h3>テナントの監査ログ</h3>${auditTable(data.audit)}`;
    return `<h3>利用者</h3><ul class="userList">${data.users.map(x=>userRow(x,x=>`<select data-role-of="${esc(x.id)}" aria-label="${esc(x.name)}のロール" ${x.id===data.self?'disabled':''}><option value="member" ${x.role==='member'?'selected':''}>メンバー</option><option value="tenant_admin" ${x.role==='tenant_admin'?'selected':''}>テナント管理者</option></select>
      ${x.id===data.self?'':`<button class="quiet" data-act="toggleUser" data-user="${esc(x.id)}" data-disabled="${x.disabled}">${x.disabled?'有効にする':'無効にする'}</button>`}${data.login.allowPassword?`<button class="quiet" data-act="resetPassword" data-user="${esc(x.id)}" data-name="${esc(x.name)}">パスワード再発行</button>`:''}${x.ssoLinked?`<button class="quiet" data-act="unlinkSso" data-user="${esc(x.id)}" data-name="${esc(x.name)}">SSO連携解除</button>`:''}${x.id===data.self?'':`<button class="quiet" data-act="deleteUser" data-user="${esc(x.id)}" data-name="${esc(x.name)}">削除</button>`}`)).join('')}</ul>
      <form class="adminForm" data-act="createUser"><h3>利用者を追加</h3><div class="two"><label>名前<input name="name" required maxlength="80"></label><label>ロール<select name="role"><option value="member">メンバー</option><option value="tenant_admin">テナント管理者</option></select></label></div><label>メールアドレス<input name="email" type="email" required maxlength="254"></label><button type="submit" class="secondary full">追加</button></form>
      <p class="muted">${data.login.allowPassword?'追加すると一時パスワードを表示します。本人が初回ログイン時に変更します。':'パスワードでのログインが無効のため、追加した利用者はSSOでログインします。'}</p>`;
  }
  function render(){
    const system=data.kind==='system';
    $('#adminTitle').textContent=system?'テナント管理（全体管理者）':`${data.tenant.name}の管理`;
    $('#adminBody').innerHTML=(system?tabs([['tenants','テナント'],['admins','全体管理者'],['audit','監査ログ']])+systemView():tabs([['users','利用者'],['login','ログイン'],['usage','AI利用量'],['audit','監査ログ']])+tenantView());
  }
  async function load(kind){
    const result=await api().request(kind==='system'?'/api/system':'/api/tenant');data={...result,kind};view||=kind==='system'?'tenants':'users';render();
  }
  function secret(result,text){
    $('#adminSecret').hidden=false;
    $('#adminSecret').innerHTML=`<p><b>${esc(result.user.name)}</b>（${esc(result.user.email)}）の一時パスワードです。${esc(text)}この表示を閉じると二度と確認できません。本人に安全な方法で渡してください。</p><input id="issuedSecret" readonly value="${esc(result.temporaryPassword)}" aria-label="一時パスワード"><button type="button" id="copySecret" class="secondary full">コピー</button>`;
  }
  async function act(body,done){
    const result=await api().request(data.kind==='system'?'/api/system':'/api/tenant',{method:'POST',body:JSON.stringify(body)});
    if(result.temporaryPassword)secret(result,'初回ログイン時に変更を求めます。');
    await load(data.kind);if(done)toast(done);return result;
  }
  $('#adminDialog').addEventListener('close',()=>{$('#adminSecret').hidden=true;$('#adminSecret').innerHTML='';});
  $('#adminBody').addEventListener('submit',e=>{e.preventDefault();const form=e.target,f=Object.fromEntries(new FormData(form)),a=form.dataset.act;guard(async()=>{
    if(a==='updateLogin')return act({action:a,allowPassword:'allowPassword' in f,allowSso:'allowSso' in f,ssoIssuer:f.ssoIssuer.trim(),ssoClientId:f.ssoClientId.trim(),ssoClientSecret:f.ssoClientSecret},'ログイン方式を保存しました');
    if(a==='setAiLimit')return act({action:a,limit:f.limit===''?null:Number(f.limit)},'上限を保存しました');
    if(a==='addTenantAdmin')return act({action:a,tenantId:form.dataset.tenant,name:f.name,email:f.email},'テナント管理者を追加しました');
    if(a==='createTenant')return act({action:a,name:f.name,adminName:f.adminName,adminEmail:f.adminEmail},'テナントを作成しました');
    return act({action:a,...f},'追加しました');});});
  $('#adminBody').addEventListener('change',e=>{const id=e.target.dataset.roleOf;if(id)guard(async()=>{try{await act({action:'updateUser',userId:id,role:e.target.value},'ロールを変更しました（本人は再ログインが必要です）');}catch(error){await load(data.kind);throw error;}});});
  $('#adminDialog').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
    if(b.id==='copySecret')return guard(async()=>{await navigator.clipboard.writeText($('#issuedSecret').value);toast('コピーしました');});
    if(b.dataset.view){view=b.dataset.view;render();return;}
    const a=b.dataset.act,user=b.dataset.user,name=b.dataset.name;if(!a)return;
    guard(async()=>{
      if(a==='renameTenant'){const n=prompt('新しいテナント名',name);if(n===null||n.trim()===name)return;return act({action:a,tenantId:b.dataset.tenant,name:n},'テナント名を変更しました');}
      if(a==='setAiCap'){const v=prompt('このテナントの月間AI読み取り上限（回）。空欄で上限なし',b.dataset.cap);if(v===null)return;return act({action:a,tenantId:b.dataset.tenant,cap:v.trim()===''?null:Number(v)},'AI上限を設定しました');}
      if(a==='deleteTenant'){const v=prompt(`テナント「${name}」を削除します。利用者・冷蔵庫・在庫・写真・履歴・AI設定・利用記録がすべて消え、元に戻せません。確認のためテナント名を入力してください。`);if(v===null)return;return act({action:a,tenantId:b.dataset.tenant,confirmName:v},'テナントを削除しました');}
      if(a==='toggleUser')return act({action:'updateUser',userId:user,disabled:b.dataset.disabled!=='true'},b.dataset.disabled==='true'?'有効にしました':'無効にしました（ログイン中のセッションも終了します）');
      if((a==='resetPassword'||a==='resetSystemAdmin')&&!confirm(`${name}のパスワードを再発行します。今のパスワードとログイン中のセッションは使えなくなります。よろしいですか？`))return;
      if(a==='unlinkSso'&&!confirm(`${name}のSSO連携を解除します。次回のSSOログインで改めて結び付けます。よろしいですか？`))return;
      if((a==='deleteUser'||a==='deleteSystemAdmin')&&!confirm(`${name}を削除します。ログインできなくなります（操作履歴の名前は残ります）。よろしいですか？`))return;
      return act({action:a,userId:user},{deleteUser:'削除しました',deleteSystemAdmin:'削除しました',unlinkSso:'SSO連携を解除しました'}[a]);
    });});
  return {open:kind=>guard(async()=>{view='';$('#adminSecret').hidden=true;await load(kind);$('#adminDialog').showModal();})};
}
