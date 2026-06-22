const username = localStorage.getItem('username');
if (!username) window.location.href = '/';

const state = { sprintId: new URLSearchParams(window.location.search).get('sprint_id'), sprint: null, issues: [], users: [], selectedIssue: null, timer: null };
const statusMeta = {
  to_do: { label: 'Queued', icon: '◎' },
  in_progress: { label: 'Executing', icon: '▶' },
  in_review: { label: 'Review Ready', icon: '⬡' },
  done: { label: 'Completed', icon: '✓' }
};
const els = {
  heroTitle: document.getElementById('heroTitle'), heroSubtitle: document.getElementById('heroSubtitle'), lastSync: document.getElementById('lastSync'), sprintLabel: document.getElementById('sprintLabel'), statRunning: document.getElementById('statRunning'), statNeedsHuman: document.getElementById('statNeedsHuman'), statReview: document.getElementById('statReview'), statDone: document.getElementById('statDone'), statTotal: document.getElementById('statTotal'), statRunningMirror: document.getElementById('statRunningMirror'), statNeedsHumanMirror: document.getElementById('statNeedsHumanMirror'), statReviewMirror: document.getElementById('statReviewMirror'), factoryWorld: document.getElementById('factoryWorld'), detailDrawer: document.getElementById('detailDrawer'), drawerTitle: document.getElementById('drawerTitle'), drawerMeta: document.getElementById('drawerMeta'), drawerDescription: document.getElementById('drawerDescription'), drawerStatus: document.getElementById('drawerStatus'), drawerAssignedTo: document.getElementById('drawerAssignedTo'), drawerComments: document.getElementById('drawerComments'), legacyIssueLink: document.getElementById('legacyIssueLink'), createIssueBtn: document.getElementById('createIssueBtn'), logoutBtn: document.getElementById('logoutBtn'), closeDrawerBtn: document.getElementById('closeDrawerBtn'), saveIssueBtn: document.getElementById('saveIssueBtn'), createIssueModal: document.getElementById('createIssueModal'), closeModalBtn: document.getElementById('closeModalBtn'), cancelModalBtn: document.getElementById('cancelModalBtn'), createIssueForm: document.getElementById('createIssueForm'), issueTitle: document.getElementById('issueTitle'), issueDescription: document.getElementById('issueDescription'), issueAssignedTo: document.getElementById('issueAssignedTo'), assignableUsersList: document.getElementById('assignableUsersList'), toast: document.getElementById('toast'), commentForm: document.getElementById('commentForm'), commentInput: document.getElementById('commentInput'), addCommentBtn: document.getElementById('addCommentBtn')
};
const api = (path, options={}) => fetch(path, options).then(async r => { if (!r.ok) throw new Error(`${r.status}`); const t = r.headers.get('content-type')||''; return t.includes('application/json') ? r.json() : r.text(); });

function showToast(message, timeout=2200){ els.toast.textContent = message; els.toast.classList.remove('hidden'); clearTimeout(showToast._t); showToast._t = setTimeout(() => els.toast.classList.add('hidden'), timeout); }
function openModal(show){ els.createIssueModal.classList.toggle('hidden', !show); if (show) els.issueTitle.focus(); }
function closeDrawer(){ state.selectedIssue = null; els.detailDrawer.classList.add('hidden'); }
els.createIssueBtn.onclick = () => openModal(true); els.closeModalBtn.onclick = () => openModal(false); els.cancelModalBtn.onclick = () => openModal(false); els.closeDrawerBtn.onclick = closeDrawer; els.logoutBtn.onclick = () => { localStorage.removeItem('username'); window.location.href = '/'; }; window.addEventListener('click', e => { if (e.target === els.createIssueModal) openModal(false); });

async function bootstrap(){ await loadUsers(); await sync(); state.timer = setInterval(sync, 5000); window.addEventListener('resize', () => render()); }
async function loadUsers(){ state.users = await api('/api/users'); const opts = ['<option value="">Unassigned</option>'].concat(state.users.map(u => `<option value="${escapeHtml(u.username)}">${escapeHtml(u.username)}</option>`)); if (els.assignableUsersList) els.assignableUsersList.innerHTML = state.users.map(u => `<option value="${escapeHtml(u.username)}"></option>`).join(''); els.drawerAssignedTo.innerHTML = opts.join(''); }
async function resolveSprint(){ if (state.sprintId) { try { return await api(`/api/sprints/${state.sprintId}`); } catch {} } try { return await api('/api/sprints/active'); } catch { const all = await api('/api/sprints'); return all.find(s => s.is_active) || all[0] || null; } }
const HUMAN_TASK_USERS = new Set(['Aaron', 'Taylor']);
function needsHuman(issue){ return HUMAN_TASK_USERS.has(canonOwner(issue.assigned_to)); }
function canonOwner(name){
  const normalized = (name || '').trim();
  const aliasMap = { Claw: 'Jerry', claw: 'Jerry', aaron: 'Aaron', taylor: 'Taylor' };
  return aliasMap[normalized] || normalized || 'Unassigned';
}
function relTime(v){ const d = Date.now() - new Date(v).getTime(); const m = Math.round(d/60000); if (m < 1) return 'just now'; if (m < 60) return `${m}m ago`; const h = Math.round(m/60); if (h < 24) return `${h}h ago`; return `${Math.round(h/24)}d ago`; }
function escapeHtml(text){ const div = document.createElement('div'); div.textContent = text ?? ''; return div.innerHTML; }
function shortTitle(text, n=30){ const t = text || ''; return t.length > n ? t.slice(0,n-1) + '…' : t; }
function taskGlyph(issue){ if (issue.status === 'done') return '✓'; if (issue.status === 'in_review') return '⬡'; if (issue.status === 'in_progress') return '⚙'; return '▣'; }
function taskTheme(issue){
  const text = `${issue.title || ''} ${issue.description || ''}`.toLowerCase();
  if (/(ui|ux|design|dashboard|screen|branding|visual)/.test(text)) return { cls:'design', label:'design studio' };
  if (/(robot|motor|sensor|hardware|jetson|arduino|encoder|i2c|pid)/.test(text)) return { cls:'hardware', label:'mech bay' };
  if (/(api|backend|infra|pipeline|data|database|sync|automation|openclaw)/.test(text)) return { cls:'systems', label:'systems forge' };
  if (/(review|artifact|screenshot|client|naming|strategy|business|pr|pull request)/.test(text)) return { cls:'review-theme', label:'review lane' };
  return { cls:'general', label:'work cell' };
}
function prState(issue){
  const text = `${issue.title || ''} ${issue.description || ''}`.toLowerCase();
  if (issue.status === 'done') return 'merged';
  if (issue.status === 'in_review' || /(review|pr|pull request)/.test(text)) return 'review';
  if (issue.status === 'in_progress') return 'open';
  return 'queued';
}
function tasksFor(name){ return state.issues.filter(i => canonOwner(i.assigned_to || 'Unassigned') === name); }
function groupCounts(name){ const owned = tasksFor(name); return { total: owned.length, running: owned.filter(i => i.status === 'in_progress').length }; }
function nodeCategory(issue){ if (issue.status === 'done') return 'done'; if (issue.status === 'in_review') return 'review'; if (needsHuman(issue)) return 'human'; if (issue.status === 'in_progress') return 'build'; return 'queue'; }
function lane(issue){ const c = nodeCategory(issue); if (c === 'queue') return 0; if (c === 'build') return 1; if (c === 'human') return 2; return 3; }
function layoutFor(issue, idx, mobile){
  const laneXDesktop = [76, 320, 592, 838];
  const laneXMobile = [18, 170, 46, 194];
  const yBaseDesktop = [324, 380, 384, 268];
  const yBaseMobile = [318, 516, 746, 932];
  const laneIdx = lane(issue);
  const col = idx % (mobile ? 2 : 2);
  const row = Math.floor(idx / 2);
  const x = (mobile ? laneXMobile[laneIdx] : laneXDesktop[laneIdx]) + col * (mobile ? 120 : 152);
  const y = (mobile ? yBaseMobile[laneIdx] : yBaseDesktop[laneIdx]) + row * (mobile ? 118 : 104);
  return { x, y };
}
function nodeMarkup(issue, idx, mobile){
  const theme = taskTheme(issue);
  const stateChip = prState(issue);
  const pos = layoutFor(issue, idx, mobile);
  return `<article class="task-node ${nodeCategory(issue)} ${theme.cls}" style="left:${pos.x}px;top:${pos.y}px" data-id="${issue.id}"><h4>${taskGlyph(issue)} ${escapeHtml(shortTitle(issue.title, mobile ? 18 : 22))}</h4><p>${escapeHtml(canonOwner(issue.assigned_to || 'Unassigned'))} · ${escapeHtml(theme.label)}</p><div class="node-chip-row"><span class="node-chip">${statusMeta[issue.status].label}</span><span class="node-chip">PR ${stateChip}</span></div></article>`;
}
function workerTarget(name, mobile){
  const owned = tasksFor(name).filter(i => i.status !== 'done');
  const issue = owned.sort((a,b)=> new Date(b.created_at) - new Date(a.created_at))[0];
  const fallback = { Jerry: mobile ? {x:96,y:186} : {x:410,y:198}, Aaron: mobile ? {x:220,y:610} : {x:620,y:214}, Taylor: mobile ? {x:94,y:858} : {x:714,y:234} };
  if (!issue) return { ...fallback[name], issueId: null, title: 'idle' };
  const idx = state.issues.indexOf(issue);
  const pos = layoutFor(issue, idx, mobile);
  return { x: pos.x - 8, y: pos.y - 126, issueId: issue.id, title: issue.title };
}
function workerMarkup(name, mobile){
  const target = workerTarget(name, mobile);
  const counts = groupCounts(name);
  const cls = name.toLowerCase();
  return `<div class="worker-unit ${cls}" style="left:${target.x}px;top:${target.y}px" ${target.issueId ? `data-id="${target.issueId}"` : ''}><div class="worker-badge">${escapeHtml(name)} · ▶ ${counts.running} · ◎ ${counts.total}</div><div class="worker"><div class="halo"></div><div class="head"></div><div class="body"></div><div class="visor"></div><div class="tool"></div><div class="legs"></div></div><div class="worker-line"></div><div class="worker-target"></div></div>`;
}
function sceneMarkup(mobile){
  const tasks = [...state.issues].sort((a,b)=>{
    const order = { in_progress:0, in_review:1, to_do:2, done:3 };
    return (order[a.status] - order[b.status]) || (new Date(b.created_at) - new Date(a.created_at));
  });
  const nodes = tasks.map((issue, idx) => nodeMarkup(issue, idx, mobile)).join('');
  return `
    <div class="map-scene ${mobile ? 'mobile-map' : 'desktop-map'}">
      <div class="terrain t1"></div><div class="terrain t2"></div><div class="terrain t3"></div>
      <div class="depot queue"><div class="depot-label">Queue Gate</div></div>
      <div class="depot build"><div class="depot-label">Build Bay</div></div>
      <div class="depot human"><div class="depot-label">Human Checkpoint</div></div>
      <div class="depot output"><div class="depot-label">Output Dock</div></div>
      <div class="belt b1" style="left:${mobile ? 88 : 142}px;top:${mobile ? 248 : 254}px;width:${mobile ? 94 : 214}px"><span class="packet p1"></span><span class="packet p2"></span></div>
      <div class="belt b2" style="left:${mobile ? 188 : 392}px;top:${mobile ? 520 : 312}px;width:${mobile ? 120 : 214}px"><span class="packet p3"></span></div>
      <div class="belt b3" style="left:${mobile ? 136 : 640}px;top:${mobile ? 826 : 274}px;width:${mobile ? 168 : 214}px"><span class="packet p4"></span><span class="packet p5"></span></div>
      <div class="belt b4 vertical" style="left:${mobile ? 238 : 784}px;top:${mobile ? 696 : 238}px;height:${mobile ? 116 : 208}px"><span class="packet p2"></span></div>
      ${nodes}
      ${workerMarkup('Jerry', mobile)}
      ${workerMarkup('Aaron', mobile)}
      ${workerMarkup('Taylor', mobile)}
      <div class="legend-strip">
        <article class="legend-card"><h5>Blue belts</h5><p>Jerry is routing/building autonomous work.</p></article>
        <article class="legend-card"><h5>Yellow tasks</h5><p>Jerry scheduled a human checkpoint for Aaron or Taylor.</p></article>
        <article class="legend-card"><h5>Pink review</h5><p>PR open / review needed / review activity.</p></article>
        <article class="legend-card"><h5>Green dock</h5><p>Merged, shipped, or fully completed output.</p></article>
      </div>
    </div>`;
}
function openDrawer(issue){ state.selectedIssue = issue; els.drawerTitle.textContent = issue.title; els.drawerMeta.textContent = `#${issue.id} · assigned by Jerry to ${canonOwner(issue.assigned_to || 'Unassigned')} · ${relTime(issue.created_at)}`; els.drawerDescription.textContent = issue.description || 'No description.'; els.drawerStatus.value = issue.status; els.drawerAssignedTo.value = issue.assigned_to || ''; els.commentInput.value = ''; els.legacyIssueLink.href = `/static/issue.html?id=${issue.id}`; els.drawerComments.innerHTML = issue.comments?.length ? issue.comments.map(c => `<article class="comment-item"><strong>${escapeHtml(c.username)}</strong><p>${escapeHtml(c.content)}</p><span>${new Date(c.created_at).toLocaleString()}</span></article>`).join('') : '<div class="comment-item"><strong>Jerry</strong><p>No extra notes yet. Tap a task node to inspect the work order.</p><span>factory map</span></div>'; els.detailDrawer.classList.remove('hidden'); }
function render(){
  const mobile = window.innerWidth <= 760;
  els.factoryWorld.innerHTML = sceneMarkup(mobile);
  els.factoryWorld.querySelectorAll('[data-id]').forEach(node => node.onclick = () => {
    const issue = state.issues.find(i => i.id === Number(node.dataset.id));
    if (issue) openDrawer(issue);
  });
}
els.saveIssueBtn.onclick = async () => { if (!state.selectedIssue) return; const original = els.saveIssueBtn.textContent; els.saveIssueBtn.disabled = true; els.saveIssueBtn.textContent = 'Saving…'; try { await api(`/api/issues/${state.selectedIssue.id}`, { method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ status: els.drawerStatus.value, assigned_to: els.drawerAssignedTo.value || null }) }); await sync(); showToast('Task updated'); closeDrawer(); } catch (e) { console.error(e); showToast('Update failed'); } finally { els.saveIssueBtn.disabled = false; els.saveIssueBtn.textContent = original; } };
els.createIssueForm.onsubmit = async (e) => { e.preventDefault(); if (!state.sprint) return; const submitBtn = els.createIssueForm.querySelector('button[type="submit"]'); const original = submitBtn.textContent; submitBtn.disabled = true; submitBtn.textContent = 'Launching…'; try { const issue = await api('/api/issues', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ title: els.issueTitle.value.trim(), description: els.issueDescription.value.trim(), created_by: username, assigned_to: els.issueAssignedTo.value || null }) }); await api(`/api/issues/${issue.id}/assign-to-sprint?sprint_id=${state.sprint.id}`, { method:'POST' }); els.createIssueForm.reset(); openModal(false); await sync(); showToast('Task created'); } catch (err) { console.error(err); showToast('Create failed'); } finally { submitBtn.disabled = false; submitBtn.textContent = original; } };
async function sync(){ try { state.sprint = await resolveSprint(); if (!state.sprint) return; state.sprintId = state.sprint.id; state.issues = await api(`/api/issues?sprint_id=${state.sprint.id}`); const running = state.issues.filter(i => i.status === 'in_progress'); const review = state.issues.filter(i => i.status === 'in_review'); const done = state.issues.filter(i => i.status === 'done'); const human = state.issues.filter(i => i.status !== 'done' && needsHuman(i)); els.heroTitle.textContent = `${state.sprint.name} // Task Map`; els.heroSubtitle.textContent = `Jerry schedules work and humans travel to assigned nodes.`; els.sprintLabel.textContent = state.sprint.name; els.statRunning.textContent = String(running.length); els.statNeedsHuman.textContent = String(human.length); els.statReview.textContent = String(review.length); els.statDone.textContent = String(done.length); els.statTotal.textContent = String(state.issues.length); els.statRunningMirror.textContent = String(running.length); els.statNeedsHumanMirror.textContent = String(human.length); els.statReviewMirror.textContent = String(review.length); els.lastSync.textContent = new Date().toLocaleTimeString(); render(); if (state.selectedIssue) { const refreshed = state.issues.find(i => i.id === state.selectedIssue.id); if (refreshed) openDrawer(refreshed); } } catch (e) { console.error(e); els.heroTitle.textContent = 'Factory map sync failed'; els.heroSubtitle.textContent = 'Unable to refresh the live task map from backend.'; } }
bootstrap();
els.commentForm.onsubmit = async (e) => { e.preventDefault(); if (!state.selectedIssue) return; const content = els.commentInput.value.trim(); if (!content) { showToast('Enter a comment first'); return; } const original = els.addCommentBtn.textContent; els.addCommentBtn.disabled = true; els.addCommentBtn.textContent = 'Adding…'; try { await api(`/api/issues/${state.selectedIssue.id}/comments`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ content, username }) }); await sync(); const refreshed = state.issues.find(i => i.id === state.selectedIssue.id); if (refreshed) openDrawer(refreshed); showToast('Comment added'); } catch (err) { console.error(err); showToast('Comment failed'); } finally { els.addCommentBtn.disabled = false; els.addCommentBtn.textContent = original; } };
