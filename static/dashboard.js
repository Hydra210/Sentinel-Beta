import { request, post, icon, mountIcons, escapeHTML as esc, toast } from './common.js';
const $ = id => document.getElementById(id);
const TYPES = ['Audio','Decal','Video','Mesh','Plugin','Animation','Model','Package'];
let user, config = {}, groups = [], history = [], status = {}, currentTab = 'overview', rulesDirty = false;
let refreshBusy = false;
let confirmCallback = null;
function empty(symbol, title, description, action = '') {
  return `<div class="empty-state"><span>${icon(symbol)}</span><h3>${title}</h3><p>${description}</p>${action}</div>`;
}
async function api(path, options = {}) {
  try { return await request(path, options); }
  catch (error) { if (error.status === 401) location.assign('/sentinel/login'); throw error; }
}
function accountQuery(extra = '') { return `profile_id=${encodeURIComponent(user.id)}${extra}`; }
function send(path, body = {}) { return api(path, {method:'POST',body:JSON.stringify({profile_id:user.id,...body})}); }
function showTab(name) {
  currentTab = name;
  document.querySelectorAll('.dashboard-tab').forEach(panel => panel.classList.toggle('active', panel.id === 'tab-' + name));
  document.querySelectorAll('.dashboard-nav [data-tab]').forEach(button => { button.classList.toggle('active', button.dataset.tab === name); if (button.dataset.tab === name) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
  const titles = {overview:'Overview',groups:'Groups',rules:'Moderation rules',history:'Activity history',settings:'Settings'};
  $('breadcrumb-title').textContent = titles[name]; document.title = `Sentinel — ${titles[name]}`;
  window.scrollTo({top:0,behavior:'instant'});
}
function askConfirm(title, description, callback, action = 'Confirm') {
  $('confirm-title').textContent = title; $('confirm-description').textContent = description; $('confirm-action').textContent = action;
  confirmCallback = callback; $('confirm-dialog').showModal();
}
function showDialog(id) { $(id).showModal(); }
function openConnect() { $('connect-error').textContent=''; $('connect-form').reset(); showDialog('connect-dialog'); }
function openGroup() { $('group-error').textContent=''; $('group-form').reset(); showDialog('group-dialog'); }
function errorBanner(error) { $('dashboard-error').textContent = error.message; $('dashboard-error').classList.remove('hidden'); }
function renderStatus() {
  const monitoring = !!status.monitoring;
  [ $('top-status'), $('monitor-status') ].forEach(element => { element.classList.toggle('online', monitoring); element.innerHTML = `<i></i> ${monitoring ? 'MONITORING' : 'STANDBY'}`; });
  $('monitor-heading').textContent = monitoring ? 'Sentinel is on watch.' : 'Ready when you are.';
  $('monitor-description').textContent = monitoring ? `Watching ${groups.length} ${groups.length === 1 ? 'group' : 'groups'} for uploads that don't fit your rules.` : status.hasCredential ? 'Your account is connected. Start monitoring when your groups and rules are ready.' : 'Connect a moderation account, add your groups, and let Sentinel take watch.';
  $('monitor-button').innerHTML = `${icon(monitoring ? 'pause' : 'play')}<span>${monitoring ? 'Pause monitoring' : 'Start monitoring'}</span>`;
  const accountName = status.account?.displayName || status.account?.username || 'Roblox account';
  $('connection-heading').textContent = status.hasCredential ? accountName + ' is connected.' : 'Your moderation account';
  $('connection-description').textContent = status.hasCredential ? 'Your session credential is held in server memory. You are ready to moderate.' : 'Connect a dedicated Roblox account to start protecting your groups.';
  $('connect-button').innerHTML = `${status.hasCredential ? 'Manage connection' : 'Connect account'} ${icon('arrow')}`;
  $('settings-connection-status').textContent = status.hasCredential ? `Connected as ${accountName}` : 'No account connected';
  $('disconnect-button').disabled = !status.hasCredential;
}
function groupHTML(group, removable = false) {
  const initials = String(group.name || 'Roblox group').split(/\s+/).map(word=>word[0]).join('').slice(0,2).toUpperCase();
  return `<div class="group-row"><span class="group-avatar">${esc(initials)}</span><div class="group-details"><b>${esc(group.name || 'Roblox group')}</b><small>GROUP / ${esc(group.id)}</small></div><span class="status-label ${status.monitoring ? 'online' : ''}"><i></i>${status.monitoring ? 'ON WATCH' : 'STANDBY'}</span>${removable ? `<button class="icon-button" data-remove-group="${esc(group.id)}" aria-label="Remove ${esc(group.name || 'group')}">${icon('close')}</button>` : ''}</div>`;
}
function activityHTML(item) {
  return `<div class="activity-row"><span class="activity-symbol">${icon(item.asset_type === 'Audio' ? 'audio':'box')}</span><div><b>${esc(item.audio_name || 'Asset ' + item.audio_id)}</b><small>${esc(item.group_name || 'Roblox group')} · ${esc(item.asset_type || 'Audio')}</small></div><span class="mini-badge">${item.archived ? 'ARCHIVED' : 'NOT ARCHIVED'}</span></div>`;
}
function renderGroups() {
  $('group-count').textContent = groups.length;
  $('stat-groups').textContent = groups.length;
  $('overview-groups').innerHTML = groups.length ? groups.slice(0,4).map(group=>groupHTML(group)).join('') : empty('layers','Your first group starts here.','Add a Roblox group and put Sentinel on watch.',`<button class="button button-outline" data-action="add-group">Add a group ${icon('plus')}</button>`);
  $('groups-list').innerHTML = groups.length ? groups.map(group=>groupHTML(group,true)).join('') : empty('layers','A little peace of mind for your groups.','Add your first Roblox group. Your connected account needs permission to manage its assets.',`<button class="button button-white" data-action="add-group">Add your first group ${icon('plus')}</button>`);
}
function renderHistory() {
  $('overview-history').innerHTML = history.length ? history.slice(0,4).map(activityHTML).join('') : empty('clock','A clean slate.','Your moderation activity will appear here.<br>Connect a group and start monitoring.');
  const query = $('history-search').value.toLowerCase().trim();
  const filter = $('history-filter').value;
  const filtered = history.filter(item => {
    const match = [item.audio_name,item.audio_id,item.username,item.display_name,item.group_name].some(value => String(value || '').toLowerCase().includes(query));
    return match && (filter === 'all' || (filter === 'archived' ? !!item.archived : !item.archived));
  });
  $('history-count').textContent = `${filtered.length} ${filtered.length === 1 ? 'ACTION' : 'ACTIONS'}`;
  $('history-list').innerHTML = filtered.length ? filtered.map(item => `<div class="full-history-row"><span class="activity-symbol">${icon(item.asset_type === 'Audio' ? 'audio':'box')}</span><div><h3>${esc(item.audio_name || 'Asset ' + item.audio_id)}</h3><p>${esc(item.username || item.display_name || 'Unknown uploader')} · ${esc(item.group_name || 'Roblox group')} · ${esc(item.asset_type || 'Audio')}<br>Asset ID: ${esc(item.audio_id)}</p></div><span class="full-history-meta"><time>${esc(item.time || '')}</time><span class="mini-badge">${item.archived ? 'ARCHIVED' : 'NOT ARCHIVED'}</span>${item.archived ? `<button class="button button-outline" data-restore="${esc(item.audio_id)}" data-history-id="${esc(item.id)}">Restore ${icon('restore')}</button>` : `<button class="button button-outline" data-rearchive="${esc(item.audio_id)}" data-history-id="${esc(item.id)}">Archive ${icon('archive')}</button>`}</span></div>`).join('') : empty('clock',history.length ? 'Nothing matches this view.' : 'Your history starts fresh.',history.length ? 'Try a different search or filter.' : 'When Sentinel moderates an asset, you will see the action here.');
}
function renderRules() {
  $('asset-filters').innerHTML = TYPES.map(type => `<label class="asset-filter"><input type="checkbox" value="${type}" ${config.assetTypeFilters?.includes(type) ? 'checked' : ''}><span>${type}</span>${icon(type === 'Audio' ? 'audio' : 'box')}</label>`).join('');
  $('asset-type-count').textContent = `${config.assetTypeFilters?.length || 0} SELECTED`;
  renderAllowlist();
}
function renderAllowlist() {
  const type = $('allowlist-type').value;
  const list = config['whitelist_' + type] || [];
  $('allowlist-chips').innerHTML = list.length ? list.map((entry,index) => `<span class="allowlist-chip">${esc(entry)}<button data-remove-trusted="${index}" aria-label="Remove ${esc(entry)}">${icon('close')}</button></span>`).join('') : '<span class="allowlist-empty">No trusted uploaders on this list yet.</span>';
}
function renderSettings() {
  $('poll-interval').value = config.pollingInterval ?? 60;
  $('archive-delay').value = config.archiveDelay ?? 0;
  $('archive-existing').checked = !!config.archiveExisting;
  $('stat-interval').innerHTML = `${esc(config.pollingInterval || 60)}<span>s</span>`;
  $('stat-trusted').textContent = (config.whitelist_all || []).length;
}
async function refresh() {
  if (refreshBusy || !user) return;
  refreshBusy = true;
  try {
    const [newStatus,newGroups,newHistory,stats] = await Promise.all([
      api('/status?' + accountQuery()), api('/groups?' + accountQuery()), api('/history?' + accountQuery('&limit=200')), api('/stats?' + accountQuery()),
    ]);
    status = newStatus; groups = newGroups; history = newHistory;
    $('stat-archived').textContent = stats.archived ?? 0;
    renderStatus(); renderGroups(); renderHistory();
    $('dashboard-error').classList.add('hidden');
  } catch (error) { errorBanner(error); }
  finally { refreshBusy = false; }
}
function markRulesDirty() { rulesDirty = true; $('save-rules').innerHTML = `Save rules <span class="unsaved-marker">●</span>`; }
function finishRulesSave() { rulesDirty = false; $('save-rules').innerHTML = `Save rules ${icon('check')}`; }
async function handleMonitor() {
  if (!status.monitoring && !status.hasCredential) { openConnect(); return; }
  if (!status.monitoring && !groups.length) { openGroup(); toast('Add a group before starting monitoring.'); return; }
  const button = $('monitor-button'); button.disabled = true;
  try {
    await send('/monitoring/' + (status.monitoring ? 'stop':'start'));
    toast(status.monitoring ? 'Monitoring paused. You are still in control.' : 'Sentinel is on watch.');
    await refresh();
  } catch (error) { toast(error.message,'error'); }
  finally { button.disabled = false; }
}
async function disconnect() {
  try { await send('/credentials/clear'); toast('Roblox account disconnected.'); await refresh(); }
  catch(error) { toast(error.message,'error'); }
}
document.addEventListener('click', event => {
  const tab = event.target.closest('[data-tab]'); if (tab) showTab(tab.dataset.tab);
  const action = event.target.closest('[data-action]');
  if (action?.dataset.action === 'add-group') openGroup();
  if (action?.dataset.action === 'connect') openConnect();
  const close = event.target.closest('[data-close-dialog]'); if (close) close.closest('dialog').close();
  const remove = event.target.closest('[data-remove-group]');
  if (remove) askConfirm('Remove this group?', 'Sentinel will stop watching this group. Existing moderation history will remain available.', async () => {
    try { await api('/groups/' + encodeURIComponent(remove.dataset.removeGroup) + '?' + accountQuery(),{method:'DELETE'}); toast('Group removed from your workspace.'); await refresh(); }
    catch(error) { toast(error.message,'error'); }
  },'Remove group');
  const trusted = event.target.closest('[data-remove-trusted]');
  if (trusted) { const key = 'whitelist_' + $('allowlist-type').value; config[key].splice(Number(trusted.dataset.removeTrusted),1); renderAllowlist(); markRulesDirty(); }
  const rearchive = event.target.closest('[data-rearchive]');
  if (rearchive) askConfirm('Archive this asset?', 'Sentinel will ask Roblox to archive this asset again. Your connected account must have permission to manage it.', async () => {
    rearchive.disabled = true;
    try { const result = await send('/archive-asset', {asset_id:rearchive.dataset.rearchive,history_id:rearchive.dataset.historyId}); if (!result.ok) throw new Error('Roblox could not archive this asset. Check your permissions and try again.'); toast('Asset archived.'); await refresh(); }
    catch (error) { toast(error.message, 'error'); }
    finally { rearchive.disabled = false; }
  }, 'Archive asset');
  const restore = event.target.closest('[data-restore]');
  if (restore) askConfirm('Restore this asset?', 'Sentinel will ask Roblox to restore this archived asset. It will be excluded from automatic re-archiving after a successful restoration.', async () => {
    restore.disabled=true;
    try { const result = await send('/restore',{asset_id:restore.dataset.restore,history_id:restore.dataset.historyId}); if (!result.ok) throw new Error('Roblox could not restore this asset. Check your permissions and try again.'); toast('Asset restored.'); await refresh(); }
    catch(error) { toast(error.message,'error'); }
    finally { restore.disabled=false; }
  },'Restore asset');
});
$('confirm-cancel').addEventListener('click',()=>{ $('confirm-dialog').close(); confirmCallback=null; });
$('confirm-action').addEventListener('click',()=>{ const callback=confirmCallback; confirmCallback=null; $('confirm-dialog').close(); callback?.(); });
$('refresh-button').addEventListener('click',refresh);
$('monitor-button').addEventListener('click',handleMonitor);
$('connect-button').addEventListener('click',()=>status.hasCredential?showTab('settings'):openConnect());
$('disconnect-button').addEventListener('click',()=>askConfirm('Disconnect your account?','Monitoring will stop and your Roblox session credential will be removed from Sentinel memory.',disconnect,'Disconnect'));
$('group-form').addEventListener('submit',async event=>{
  event.preventDefault(); if (!$('group-form').reportValidity()) return;
  const button=event.submitter; button.disabled=true; $('group-error').textContent='';
  try { const id=$('group-id').value.trim(); if(groups.some(group=>String(group.id)===id)) throw new Error('This group is already in your workspace.'); await send('/groups',{id}); $('group-dialog').close(); toast('Group added. Your community, under control.'); await refresh(); }
  catch(error) { $('group-error').textContent=error.message; }
  finally { button.disabled=false; }
});
$('connect-form').addEventListener('submit',async event=>{
  event.preventDefault(); const button=event.submitter; button.disabled=true; $('connect-error').textContent='';
  try { await send('/credentials/manual',{cookie:$('roblox-cookie').value.trim()}); $('roblox-cookie').value=''; $('connect-dialog').close(); toast('Moderation account connected securely.'); await refresh(); }
  catch(error) { $('connect-error').textContent=error.message; }
  finally { button.disabled=false; }
});
$('connect-dialog').addEventListener('close',()=>{ $('roblox-cookie').value=''; });
$('allowlist-type').insertAdjacentHTML('beforeend',TYPES.map(type=>`<option value="${type}">${type}</option>`).join(''));
$('allowlist-type').addEventListener('change',renderAllowlist);
$('allowlist-form').addEventListener('submit',event=>{
  event.preventDefault(); const entry=$('allowlist-entry').value.trim();
  if(!/^[A-Za-z0-9_]{1,60}$/.test(entry)) { toast('Use a Roblox username or numeric user ID.','error'); return; }
  const key='whitelist_'+$('allowlist-type').value; config[key] ||= [];
  if(config[key].some(existing=>String(existing).toLowerCase()===entry.toLowerCase())) { toast('This uploader is already on this list.','error'); return; }
  config[key].push(entry); $('allowlist-entry').value=''; renderAllowlist(); markRulesDirty();
});
$('asset-filters').addEventListener('change',()=>{
  config.assetTypeFilters=[...$('asset-filters').querySelectorAll('input:checked')].map(input=>input.value);
  $('asset-type-count').textContent=`${config.assetTypeFilters.length} SELECTED`; markRulesDirty();
});
$('save-rules').addEventListener('click',async()=>{
  const button=$('save-rules'); button.disabled=true;
  try { const body={assetTypeFilters:config.assetTypeFilters}; ['all',...TYPES].forEach(type=>{ body['whitelist_'+type]=config['whitelist_'+type]||[]; }); const result=await send('/config',body); config={...config,...result}; finishRulesSave(); renderSettings(); toast('Your moderation rules are saved.'); }
  catch(error) { toast(error.message,'error'); }
  finally { button.disabled=false; }
});
$('save-settings').addEventListener('click',()=>{
  const poll=Number($('poll-interval').value), delay=Number($('archive-delay').value), existing=$('archive-existing').checked;
  if(!Number.isInteger(poll)||poll<30||poll>3600||!Number.isInteger(delay)||delay<0||delay>3600) { toast('Use a polling interval of 30–3600 seconds and an archive delay of 0–3600 seconds.','error'); return; }
  const save=async()=>{
    const button=$('save-settings');button.disabled=true;
    try { const result=await send('/config',{pollingInterval:poll,archiveDelay:delay,archiveExisting:existing}); config={...config,...result}; renderSettings(); toast('Your settings are saved.'); }
    catch(error) { toast(error.message,'error'); }
    finally { button.disabled=false; }
  };
  if(existing&&!config.archiveExisting) askConfirm('Include existing uploads?','When monitoring starts, Sentinel may archive older assets from your groups if they do not match your allowlists. This can affect assets your community already uses.',save,'Save and include'); else save();
});
$('history-search').addEventListener('input',renderHistory); $('history-filter').addEventListener('change',renderHistory);
$('export-history').addEventListener('click',()=>{
  if(!history.length) { toast('There is no moderation history to export yet.'); return; }
  const columns=['time','audio_id','audio_name','asset_type','username','group_id','group_name','archived'];
  const cell=value=>{ const text=String(value??''); return '"'+(/^[=+\-@\t\r]/.test(text)?"'":'')+text.replace(/"/g,'""')+'"'; };
  const csv=[columns.map(cell).join(','),...history.map(item=>columns.map(key=>cell(item[key])).join(','))].join('\r\n');
  const url=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'})); const link=document.createElement('a'); link.href=url; link.download='sentinel-moderation-history.csv'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
});
$('logout-button').addEventListener('click',async()=>{
  $('logout-button').disabled=true;
  try { await post('/auth/logout'); location.assign('/sentinel'); }
  catch(error) { toast(error.message,'error'); $('logout-button').disabled=false; }
});
window.addEventListener('beforeunload',event=>{if(rulesDirty){event.preventDefault();event.returnValue='';}});
async function init() {
  ['overview-groups','overview-history','groups-list','history-list'].forEach(id=>{$(id).innerHTML='<div class="loading-block">LOADING YOUR WORKSPACE…</div>';});
  try {
    const result=await api('/auth/me'); user=result.user;
    if(!user?.id) throw new Error('Your account could not be loaded. Please sign in again.');
    $('sidebar-name').textContent=user.name||'Your account'; $('account-avatar').textContent=(user.name||'S').slice(0,1).toUpperCase();
    $('settings-name').textContent=user.name||'—'; $('settings-email').textContent=user.email||'—';
    $('welcome-copy').textContent=`Good to see you${user.name?', '+user.name:''}. A clear view of everything that matters.`;
    config=await api('/config?'+accountQuery()); renderRules(); renderSettings(); await refresh();
    setInterval(()=>{if(!document.hidden)refresh();},15000);
    setInterval(()=>{post('/auth/refresh').catch(error=>{if(error.status===401)location.assign('/sentinel/login');});},12*60*1000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  } catch(error) { errorBanner(error); }
}
mountIcons(); init();
