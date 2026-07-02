window.TM_SHARED = (() => {
  const ASSIGNEE_OPTIONS = ['', 'Dwight', 'Jerry', 'Druck', 'Aaron', 'Taylor'];
  const PUBLIC_LOGIN_PATH = '/public-auth';
  const INTERNAL_LOGIN_PATH = '/index.html';
  const OWNER_APPROVER_EMAIL = 'aaron@lidisolutions.ai';
  const STATUS_OPTIONS = [
    { value: 'to_do', label: 'To Do' },
    { value: 'in_progress', label: 'In Progress' },
    { value: 'in_review', label: 'In Review' },
    { value: 'blocked', label: 'Blocked' },
    { value: 'done', label: 'Done' }
  ];
  const LIDI_HISTORY_KEY = 'tmLidiChatHistory';
  const LIDI_MINIMIZED_KEY = 'tmLidiMinimized';
  const LIDI_LAST_OPEN_KEY = 'tmLidiLastOpen';

  function buildApiHeaders(existing = {}) {
    const headers = new Headers(existing || {});
    if (getAuthMode() === 'public') {
      const token = getPublicSessionToken();
      if (token && !headers.has('Authorization')) {
        headers.set('Authorization', `Bearer ${token}`);
      }
    } else {
      const actor = getCurrentActor();
      if (actor && !headers.has('X-TM-User')) {
        headers.set('X-TM-User', actor);
      }
    }
    return headers;
  }

  function escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatStatus(status) {
    return String(status || '').replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  }

  function isBlocked(issue) {
    return issue.status === 'blocked' || Boolean(issue.blocked_reason);
  }

  function getUpdatedAt(issue) {
    return issue.updated_at || issue.created_at;
  }

  function getDaysStale(issue) {
    const updatedAt = new Date(getUpdatedAt(issue));
    if (Number.isNaN(updatedAt.getTime())) return 0;
    return Math.floor((Date.now() - updatedAt.getTime()) / 86400000);
  }

  function sprintLabel(issue, sprintMap) {
    if (!issue.sprint_id) return 'Backlog';
    return sprintMap?.get?.(issue.sprint_id) || `Sprint ${issue.sprint_id}`;
  }

  function branchLabel(issue) {
    return issue.branch ? `Branch: ${escapeHtml(issue.branch)}` : 'No branch';
  }

  function activitySummary(issue) {
    const activity = issue.activity_events?.[0];
    if (!activity) return 'No recent activity';
    const actor = activity.actor ? `${escapeHtml(activity.actor)} ` : '';
    if (activity.event_type === 'created') return `${actor}created this issue`;
    if (activity.event_type === 'comment_added') return `${actor}commented`;
    if (activity.field_name) return `${actor}updated ${escapeHtml(activity.field_name.replace(/_/g, ' '))}`;
    return `${actor}updated this issue`;
  }

  function buildOptions(options, selectedValue, emptyLabel = null) {
    const values = [];
    if (emptyLabel !== null) {
      values.push(`<option value="">${escapeHtml(emptyLabel)}</option>`);
    }
    options.forEach((option) => {
      const value = typeof option === 'string' ? option : option.value;
      const label = typeof option === 'string' ? option : option.label;
      const selected = String(selectedValue ?? '') === String(value) ? ' selected' : '';
      values.push(`<option value="${escapeHtml(value)}"${selected}>${escapeHtml(label)}</option>`);
    });
    return values.join('');
  }

  async function fetchAssignableUsers() {
    try {
      const users = await fetchJson('/api/users');
      return users.map((user) => user.username);
    } catch (error) {
      console.error('Failed to load assignable users', error);
      return ASSIGNEE_OPTIONS.filter(Boolean);
    }
  }

  function populateAssigneeSelect(selectEl, usernames, selectedValue = '', emptyLabel = 'Unassigned') {
    if (!selectEl) return;
    const canonical = ASSIGNEE_OPTIONS.filter(Boolean);
    const deduped = [];
    [...canonical, ...(usernames || [])].forEach((username) => {
      if (username && !deduped.includes(username)) deduped.push(username);
    });
    selectEl.innerHTML = buildOptions(deduped, selectedValue, emptyLabel);
  }

  function getAuthMode() {
    return localStorage.getItem('authMode') || 'internal';
  }

  function getPublicUser() {
    try {
      return JSON.parse(localStorage.getItem('publicUser') || 'null');
    } catch (error) {
      return null;
    }
  }

  function getPublicSessionToken() {
    return localStorage.getItem('publicSessionToken') || '';
  }

  function canAccessApprovals(user = null) {
    const candidate = user || getPublicUser();
    if (!candidate?.email) return false;
    return String(candidate.email).toLowerCase() === OWNER_APPROVER_EMAIL;
  }

  function getCurrentActor() {
    return localStorage.getItem('tmActor') || localStorage.getItem('username') || '';
  }

  function getCurrentDisplayName() {
    return localStorage.getItem('displayName') || localStorage.getItem('username') || getCurrentActor();
  }

  function getLoginPath() {
    return getAuthMode() === 'public' ? PUBLIC_LOGIN_PATH : INTERNAL_LOGIN_PATH;
  }

  function clearSession() {
    localStorage.removeItem('username');
    localStorage.removeItem('tmActor');
    localStorage.removeItem('displayName');
    localStorage.removeItem('authMode');
    localStorage.removeItem('publicUser');
    localStorage.removeItem('publicSessionToken');
  }

  async function fetchJson(url, options = {}) {
    const requestOptions = {
      ...options,
      headers: buildApiHeaders(options.headers)
    };
    const response = await fetch(url, requestOptions);
    const contentType = response.headers.get('content-type') || '';
    const body = contentType.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) {
      throw new Error(body?.detail || body || `Request failed: ${response.status}`);
    }
    return body;
  }

  function authenticatedFetch(url, options = {}) {
    return fetch(url, {
      ...options,
      headers: buildApiHeaders(options.headers)
    });
  }

  async function patchIssue(issueId, fields) {
    return fetchJson(`/api/issues/${issueId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields)
    });
  }

  async function fetchSprints(options = {}) {
    const all = await fetchJson('/api/sprints');
    const archivedOnly = Boolean(options.archivedOnly);
    const includeArchived = Boolean(options.includeArchived);
    const sprints = archivedOnly
      ? all.filter((s) => Boolean(s.is_archived))
      : includeArchived
        ? all
        : all.filter((s) => !s.is_archived);
    const sprintMap = new Map(sprints.map((s) => [s.id, s.is_active ? `${s.name} (Active)` : s.name]));
    return { sprints, sprintMap };
  }

  function formatSprintLabel(sprint) {
    if (!sprint) return 'Sprint';
    const base = String(sprint.name || `Sprint ${sprint.id || ''}`).trim();
    if (sprint.is_archived) return `${base} (Archived)`;
    if (Array.isArray(sprint.allowed_users) && sprint.allowed_users.length) {
      return sprint.is_active ? `${base} (Active, Restricted)` : `${base} (Restricted)`;
    }
    if (sprint.is_active) return `${base} (Active)`;
    return base;
  }

  function buildSprintSelectOptions(sprints, selectedSprintId, includeBacklog = true, includeAuto = false) {
    const options = [];
    if (includeAuto) options.push('<option value="">Auto (Active Sprint)</option>');
    else if (includeBacklog) options.push('<option value="">Backlog</option>');
    sprints.forEach((sprint) => {
      const selected = String(selectedSprintId ?? '') === String(sprint.id) ? ' selected' : '';
      const label = formatSprintLabel(sprint);
      options.push(`<option value="${sprint.id}"${selected}>${escapeHtml(label)}</option>`);
    });
    return options.join('');
  }

  function renderIssueCard(issue, context = {}) {
    const staleDays = getDaysStale(issue);
    const staleLabel = staleDays >= 3 ? `Stale ${staleDays}d` : `Updated ${new Date(getUpdatedAt(issue)).toLocaleDateString()}`;
    const blockedPill = isBlocked(issue) ? `<span class="issue-pill blocked">Blocked${issue.blocked_reason ? `: ${escapeHtml(issue.blocked_reason)}` : ''}</span>` : '';
    const reviewPill = issue.status === 'in_review' ? '<span class="issue-pill review">Needs review</span>' : '';
    const pointsPill = issue.story_points != null ? `<span class="issue-pill">${issue.story_points} pts</span>` : '<span class="issue-pill muted">No points</span>';
    const duplicates = context.duplicateMap?.get?.(issue.id) || [];
    const dupPill = duplicates.length ? `<span class="issue-pill duplicate">Possible dupes: ${duplicates.map(d => `#${d.id}`).join(', ')}</span>` : '';

    return `
      <div class="issue-card issue-card-rich" data-issue-id="${issue.id}" onclick="${context.viewHandler || 'viewIssue'}(${issue.id})">
        <div class="issue-card-header">
          <div>
            <div class="issue-id-badge">#${issue.id}</div>
            <div class="issue-card-title">${escapeHtml(issue.title)}</div>
          </div>
          <span class="status-badge ${escapeHtml(issue.status)}">${formatStatus(issue.status)}</span>
        </div>
        <div class="issue-card-description">${escapeHtml(issue.description || '').slice(0, 220)}${(issue.description || '').length > 220 ? '…' : ''}</div>
        <div class="issue-pills">${pointsPill}${reviewPill}${blockedPill}${dupPill}<span class="issue-pill muted">${escapeHtml(staleLabel)}</span></div>
        <div class="issue-card-meta issue-card-footer">
          <span>${activitySummary(issue)}</span>
          <span class="inline-save-status" data-save-status="${issue.id}"></span>
        </div>
      </div>
    `;
  }

  function findDuplicateCandidates(issues) {
    const normalized = issues.map((issue) => ({
      issue,
      tokens: new Set(String(issue.title || '').toLowerCase().split(/[^a-z0-9]+/).filter(t => t.length > 2))
    }));
    const duplicateMap = new Map();
    normalized.forEach(({ issue, tokens }, idx) => {
      const matches = [];
      normalized.forEach((other, otherIdx) => {
        if (idx === otherIdx) return;
        let overlap = 0;
        tokens.forEach((token) => { if (other.tokens.has(token)) overlap += 1; });
        if (overlap >= 3) matches.push({ id: other.issue.id, title: other.issue.title });
      });
      if (matches.length) duplicateMap.set(issue.id, matches.slice(0, 3));
    });
    return duplicateMap;
  }

  function attachInlineIssueEditors({ issues, onUpdated }) {
    document.querySelectorAll('.issue-inline-control').forEach((selectEl) => {
      selectEl.addEventListener('change', async (event) => {
        const issueId = Number(event.target.dataset.issueId);
        const field = event.target.dataset.field;
        const issue = issues.find((item) => item.id === issueId);
        const statusEl = document.querySelector(`[data-save-status="${issueId}"]`);
        if (!issue) return;
        const previousValue = field === 'sprint_id'
          ? (issue.sprint_id == null ? '' : String(issue.sprint_id))
          : String(issue[field] ?? '');
        const nextValue = event.target.value;
        if (previousValue === nextValue) return;
        statusEl.textContent = 'Saving...';
        event.target.disabled = true;
        try {
          const payload = { updated_by: getCurrentActor() || 'unknown' };
          if (field === 'sprint_id') payload[field] = nextValue === '' ? null : Number(nextValue);
          else payload[field] = nextValue === '' ? null : nextValue;
          const updated = await patchIssue(issueId, payload);
          Object.assign(issue, updated);
          statusEl.textContent = 'Saved';
          statusEl.classList.remove('error');
          onUpdated?.(updated);
          setTimeout(() => { statusEl.textContent = ''; }, 1200);
        } catch (error) {
          event.target.value = previousValue;
          statusEl.textContent = error.message || 'Failed';
          statusEl.classList.add('error');
        } finally {
          event.target.disabled = false;
        }
      });
    });
  }

  function parseDateSafe(value) {
    const date = value ? new Date(value) : null;
    if (!date || Number.isNaN(date.getTime())) return null;
    return date;
  }

  function relativeTime(value) {
    const date = parseDateSafe(value);
    if (!date) return 'n/a';
    const delta = Date.now() - date.getTime();
    if (delta < 60_000) return 'just now';
    const mins = Math.floor(delta / 60_000);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  }

  function loadLidiHistory() {
    try {
      const raw = localStorage.getItem(LIDI_HISTORY_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.slice(-30) : [];
    } catch (error) {
      return [];
    }
  }

  function saveLidiHistory(messages) {
    try {
      localStorage.setItem(LIDI_HISTORY_KEY, JSON.stringify(messages.slice(-30)));
    } catch (error) {
      // ignore localStorage write errors
    }
  }

  async function fetchLidiContext() {
    const [issues, users] = await Promise.all([
      fetchJson('/api/issues'),
      fetchJson('/api/users')
    ]);
    return { issues, users };
  }

  async function createLidiActionDraft(draft) {
    return fetchJson('/api/lidi/actions/draft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(draft)
    });
  }

  async function resolveLidiAction(actionId, decision) {
    const route = decision === 'approve' ? 'approve' : 'cancel';
    const actor = encodeURIComponent(getPublicUser()?.email || getCurrentActor() || getCurrentDisplayName() || '');
    return fetchJson(`/api/lidi/actions/${actionId}/${route}?actor=${actor}`, {
      method: 'POST'
    });
  }

  function parseLidiActionIntent(message, issues) {
    const text = String(message || '').trim();
    const lower = text.toLowerCase();

    const createRegex = /(create|open|file|make)\s+(an?\s+)?(issue|ticket|task)\b/i;
    if (createRegex.test(text)) {
      let title = text.replace(createRegex, '').trim().replace(/^[:\-\s]+/, '').trim();
      let description = '';
      const splitMatch = title.match(/\b(desc|description)\s*:\s*/i);
      if (splitMatch) {
        const splitIndex = title.toLowerCase().indexOf(splitMatch[0].toLowerCase());
        description = title.slice(splitIndex + splitMatch[0].length).trim();
        title = title.slice(0, splitIndex).trim();
      }

      const assignMatch = text.match(/assign(?:ed)?\s+to\s+([A-Za-z0-9@._-]+)/i);
      const assignedTo = assignMatch ? assignMatch[1] : null;

      if (!title) {
        return {
          needMoreInfo: true,
          content: 'I can draft that issue. Share the title after "create issue ..." and I will prepare an approval button.'
        };
      }

      return {
        action_type: 'create_issue',
        title,
        description,
        assigned_to: assignedTo,
        source_prompt: text
      };
    }

    const commentRegex = /(?:comment\s+on|add\s+comment\s+to|note\s+on)\s+#?(\d+)\s*[:\-]?\s*(.+)/i;
    const commentMatch = text.match(commentRegex);
    if (commentMatch) {
      const issueId = Number(commentMatch[1]);
      const issue = issues.find((item) => item.id === issueId);
      if (!issue) {
        return {
          needMoreInfo: true,
          content: `I could not find issue #${issueId}. Ask me for issue #id first, then I can draft a comment action.`
        };
      }
      return {
        action_type: 'comment_issue',
        issue_id: issueId,
        comment_content: commentMatch[2].trim(),
        source_prompt: text
      };
    }

    if (lower.startsWith('create issue') || lower.startsWith('open issue') || lower.startsWith('file issue')) {
      return {
        needMoreInfo: true,
        content: 'I can do that. Send: "create issue <title>" and optionally "description: ..." or "assign to <name>".'
      };
    }

    return null;
  }

  function summarizeHumanBlockers(issues) {
    const matcher = /(approval|approve|manual|owner|human|client|external|waiting|dependency|blocked by)/i;
    const blockers = issues
      .filter((issue) => issue.status === 'blocked' || Boolean(issue.blocked_reason))
      .filter((issue) => {
        const assignee = String(issue.assigned_to || '');
        return assignee === 'Aaron' || assignee === 'Taylor' || !assignee || matcher.test(issue.blocked_reason || '');
      })
      .sort((a, b) => {
        const aDate = parseDateSafe(a.updated_at || a.created_at)?.getTime() || 0;
        const bDate = parseDateSafe(b.updated_at || b.created_at)?.getTime() || 0;
        return bDate - aDate;
      });

    if (!blockers.length) {
      return 'No blockers currently require human intervention.';
    }

    const lines = blockers.slice(0, 8).map((issue) => {
      const reason = issue.blocked_reason ? ` - ${issue.blocked_reason}` : '';
      const owner = issue.assigned_to || 'Unassigned';
      return `#${issue.id} (${owner}) ${issue.title}${reason}`;
    });

    return `Blockers requiring human intervention:\n${lines.join('\n')}`;
  }

  function summarizeAgentWork(issues) {
    const agentNames = [...new Set(
      issues
        .map((issue) => issue.assigned_to)
        .filter((name) => name && (/['Dwight', 'Jerry', 'Druck', 'Overseer', 'Researcher', 'Quant', 'Critic', 'Trader', 'Executor', 'Archivist', 'Developer'].includes(name)))
    )].sort();

    if (!agentNames.length) return 'No agent-assigned issues yet.';

    const rows = agentNames.map((name) => {
      const assigned = issues.filter((issue) => issue.assigned_to === name);
      const inProgress = assigned.filter((issue) => issue.status === 'in_progress').length;
      const inReview = assigned.filter((issue) => issue.status === 'in_review').length;
      const blocked = assigned.filter((issue) => issue.status === 'blocked' || issue.blocked_reason).length;
      return `${name}: ${inProgress} in progress, ${inReview} in review, ${blocked} blocked`;
    });
    return `Agent workload snapshot:\n${rows.join('\n')}`;
  }

  function summarizeMyWork(issues) {
    const actor = getCurrentActor();
    if (!actor) return 'Sign in first, then I can show your assigned work.';
    const mine = issues.filter((issue) => issue.assigned_to === actor && issue.status !== 'done');
    if (!mine.length) return `No active assigned tasks for ${actor}.`;
    const rows = mine.slice(0, 8).map((issue) => {
      const updated = relativeTime(issue.updated_at || issue.created_at);
      return `#${issue.id} [${formatStatus(issue.status)}] ${issue.title} (${updated})`;
    });
    return `Your active tasks:\n${rows.join('\n')}`;
  }

  function summarizeStale(issues) {
    const stale = issues
      .filter((issue) => issue.status !== 'done')
      .map((issue) => ({
        issue,
        ageMs: Date.now() - (parseDateSafe(issue.updated_at || issue.created_at)?.getTime() || Date.now())
      }))
      .filter((entry) => entry.ageMs >= 3 * 86400000)
      .sort((a, b) => b.ageMs - a.ageMs);

    if (!stale.length) return 'No stale active tasks older than 3 days.';
    const rows = stale.slice(0, 8).map((entry) => {
      const days = Math.floor(entry.ageMs / 86400000);
      return `#${entry.issue.id} (${days}d stale) ${entry.issue.title}`;
    });
    return `Stale tasks:\n${rows.join('\n')}`;
  }

  function summarizeIssueById(issues, message) {
    const match = message.match(/#?(\d+)/);
    if (!match) return null;
    const issueId = Number(match[1]);
    const issue = issues.find((item) => item.id === issueId);
    if (!issue) return `I couldn't find issue #${issueId}.`;

    const prUrls = [];
    (issue.comments || []).forEach((comment) => {
      const matches = String(comment.content || '').match(/https:\/\/github\.com\/[^\s]+\/pull\/\d+/gi) || [];
      matches.forEach((url) => prUrls.push(url));
    });

    const lastUpdate = relativeTime(issue.updated_at || issue.created_at);
    const prLine = prUrls.length ? `Open PR evidence: ${prUrls[0]}` : 'Open PR evidence: none logged';
    return `#${issue.id} ${issue.title}\nStatus: ${formatStatus(issue.status)}\nAssigned: ${issue.assigned_to || 'Unassigned'}\nLast update: ${lastUpdate}\n${prLine}`;
  }

  async function generateLidiReply(input) {
    const message = String(input || '').trim();
    if (!message) return 'Ask me about blockers, agents, stale tasks, your tasks, or a specific issue number.';

    const { issues } = await fetchLidiContext();
    const lower = message.toLowerCase();

    const actionIntent = parseLidiActionIntent(message, issues);
    if (actionIntent?.needMoreInfo) {
      return { content: actionIntent.content };
    }
    if (actionIntent?.action_type) {
      const requester = getPublicUser()?.email || getCurrentActor() || getCurrentDisplayName();
      if (!requester) {
        return { content: 'Sign in first so I can attach this request to your account before asking for approval.' };
      }

      const draft = await createLidiActionDraft({
        ...actionIntent,
        requested_by: requester
      });

      return {
        content: `Would you like me to execute this action?\n${draft.preview_text}`,
        action: {
          id: draft.id,
          status: draft.status,
          action_type: draft.action_type,
          preview_text: draft.preview_text
        }
      };
    }

    const issueSummary = summarizeIssueById(issues, lower);
    if (issueSummary && /#?\d+/.test(lower)) return issueSummary;
    if (lower === 'help' || lower.includes('what can you do')) {
      return { content: 'Try: blockers, agents, my tasks, stale, issue #123, create issue ..., or comment on #id ...' };
    }
    if (lower.includes('blocker') || lower.includes('human intervention')) {
      return { content: summarizeHumanBlockers(issues) };
    }
    if (lower.includes('agent') || lower.includes('bot')) {
      return { content: summarizeAgentWork(issues) };
    }
    if (lower.includes('my task') || lower.includes('my work') || lower.includes('assigned to me')) {
      return { content: summarizeMyWork(issues) };
    }
    if (lower.includes('stale') || lower.includes('old tasks')) {
      return { content: summarizeStale(issues) };
    }

    const activeCount = issues.filter((issue) => issue.status !== 'done').length;
    const blockedCount = issues.filter((issue) => issue.status === 'blocked' || issue.blocked_reason).length;
    return { content: `Current board snapshot: ${activeCount} active tasks, ${blockedCount} blocked. Ask for blockers, agents, stale, my tasks, issue #id, create issue, or comment on #id.` };
  }

  function renderLidiActionButtons(msg, index) {
    if (!msg.action || msg.action.status !== 'pending') return '';
    return `
      <div class="lidi-action-row">
        <button type="button" class="lidi-action-btn approve" data-lidi-decision="approve" data-msg-index="${index}" data-action-id="${msg.action.id}">Approve</button>
        <button type="button" class="lidi-action-btn reject" data-lidi-decision="cancel" data-msg-index="${index}" data-action-id="${msg.action.id}">Cancel</button>
      </div>
    `;
  }

  function renderLidiLinks(msg) {
    if (!Array.isArray(msg.links) || msg.links.length === 0) return '';
    const links = msg.links
      .map((link) => {
        const href = String(link?.href || '');
        const label = escapeHtml(String(link?.label || 'Open'));
        if (!href.startsWith('/issue?id=')) return '';
        return `<a class="lidi-msg-link" href="${escapeHtml(href)}">${label}</a>`;
      })
      .filter(Boolean)
      .join('');
    if (!links) return '';
    return `<div class="lidi-link-row">${links}</div>`;
  }

  function renderLidiMessages(container, messages) {
    container.innerHTML = messages
      .map((msg, index) => `
        <div class="lidi-msg ${msg.role === 'user' ? 'lidi-user' : 'lidi-assistant'}">
          <div class="lidi-msg-bubble">${escapeHtml(msg.content).replace(/\n/g, '<br>')}${renderLidiActionButtons(msg, index)}${renderLidiLinks(msg)}</div>
        </div>
      `)
      .join('');
    container.scrollTop = container.scrollHeight;
  }

  function initLidiWidget() {
    if (typeof document === 'undefined') return;
    if (document.getElementById('lidiWidgetRoot')) return;
    if (window.location.pathname === '/public-auth' || window.location.pathname === '/index.html' || window.location.pathname === '/') {
      return;
    }

    const root = document.createElement('div');
    root.id = 'lidiWidgetRoot';
    root.className = 'lidi-widget';
    root.innerHTML = `
      <button class="lidi-toggle" id="lidiToggle" aria-label="Open Lidi">
        <span class="lidi-toggle-dot">L</span>
        <span class="lidi-toggle-label">Lidi</span>
      </button>
      <div class="lidi-panel" id="lidiPanel" aria-live="polite">
        <div class="lidi-header">
          <div>
            <strong>Lidi</strong>
            <p>Always-on task intelligence</p>
          </div>
          <button id="lidiMinimize" class="lidi-minimize" aria-label="Minimize">&minus;</button>
        </div>
        <div class="lidi-messages" id="lidiMessages"></div>
        <form id="lidiForm" class="lidi-form">
          <input id="lidiInput" type="text" placeholder="Ask about blockers, agents, stale, or issue #..." autocomplete="off" />
          <button type="submit">Send</button>
        </form>
      </div>
    `;
    document.body.appendChild(root);

    const toggle = root.querySelector('#lidiToggle');
    const panel = root.querySelector('#lidiPanel');
    const messagesEl = root.querySelector('#lidiMessages');
    const form = root.querySelector('#lidiForm');
    const input = root.querySelector('#lidiInput');
    const minimizeBtn = root.querySelector('#lidiMinimize');

    const defaultMessages = [
      { role: 'assistant', content: 'Hi, I\'m Lidi. Ask me: blockers, agents, stale, my tasks, or issue #id.' }
    ];
    let messages = loadLidiHistory();
    if (!messages.length) {
      messages = defaultMessages;
      saveLidiHistory(messages);
    }

    const minimized = localStorage.getItem(LIDI_MINIMIZED_KEY);
    const startMinimized = minimized === null ? true : minimized === 'true';
    if (startMinimized) panel.classList.remove('open');
    else panel.classList.add('open');
    renderLidiMessages(messagesEl, messages);

    function setMinimized(nextMinimized) {
      localStorage.setItem(LIDI_MINIMIZED_KEY, String(nextMinimized));
      if (nextMinimized) {
        panel.classList.remove('open');
      } else {
        panel.classList.add('open');
        input.focus();
        localStorage.setItem(LIDI_LAST_OPEN_KEY, String(Date.now()));
      }
    }

    toggle.addEventListener('click', () => {
      const isOpen = panel.classList.contains('open');
      setMinimized(isOpen);
    });

    minimizeBtn.addEventListener('click', () => setMinimized(true));

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;

      messages.push({ role: 'user', content: text });
      renderLidiMessages(messagesEl, messages);
      saveLidiHistory(messages);
      input.value = '';

      const pending = { role: 'assistant', content: 'Thinking...' };
      messages.push(pending);
      renderLidiMessages(messagesEl, messages);

      try {
        const reply = await generateLidiReply(text);
        pending.content = reply?.content || 'Done.';
        if (reply?.action) {
          pending.action = reply.action;
        }
      } catch (error) {
        pending.content = `I couldn't fetch that right now: ${error.message || 'unknown error'}`;
      }

      renderLidiMessages(messagesEl, messages);
      saveLidiHistory(messages);
    });

    messagesEl.addEventListener('click', async (event) => {
      const btn = event.target.closest('[data-lidi-decision]');
      if (!btn) return;
      const actionId = Number(btn.dataset.actionId);
      const msgIndex = Number(btn.dataset.msgIndex);
      const decision = btn.dataset.lidiDecision;
      if (!actionId || Number.isNaN(msgIndex) || !messages[msgIndex]?.action) return;

      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = decision === 'approve' ? 'Approving...' : 'Cancelling...';

      try {
        const result = await resolveLidiAction(actionId, decision);
        messages[msgIndex].action.status = result.status;

        if (result.status === 'approved') {
          const details = [];
          const links = [];
          if (result.result_issue_id) details.push(`Issue #${result.result_issue_id} created.`);
          if (result.result_comment_id) details.push(`Comment #${result.result_comment_id} added.`);
          if (result.result_issue_id) {
            const issueId = Number(result.result_issue_id);
            if (Number.isFinite(issueId) && issueId > 0) {
              const linkLabel = result.result_comment_id
                ? `View issue #${issueId} (comment #${result.result_comment_id})`
                : `View issue #${issueId}`;
              links.push({ href: `/issue?id=${issueId}`, label: linkLabel });
            }
          }
          messages.push({
            role: 'assistant',
            content: details.length ? details.join(' ') : 'Approved and executed.',
            links
          });
        } else if (result.status === 'cancelled') {
          messages.push({ role: 'assistant', content: 'Cancelled. No changes were made.' });
        } else if (result.status === 'failed') {
          messages.push({ role: 'assistant', content: `Execution failed: ${result.error_message || 'unknown error'}` });
        } else if (result.status === 'expired') {
          messages.push({ role: 'assistant', content: 'That draft expired. Ask me again and I can re-draft it.' });
        } else {
          messages.push({ role: 'assistant', content: `Action status: ${result.status}` });
        }
      } catch (error) {
        messages.push({ role: 'assistant', content: `Action request failed: ${error.message || 'unknown error'}` });
      } finally {
        btn.disabled = false;
        btn.textContent = original;
        renderLidiMessages(messagesEl, messages);
        saveLidiHistory(messages);
      }
    });
  }

  if (typeof window !== 'undefined') {
    window.setTimeout(initLidiWidget, 0);
  }

  return {
    ASSIGNEE_OPTIONS,
    STATUS_OPTIONS,
    clearSession,
    escapeHtml,
    authenticatedFetch,
    formatStatus,
    fetchJson,
    getPublicSessionToken,
    getPublicUser,
    getAuthMode,
    getCurrentActor,
    getCurrentDisplayName,
    getLoginPath,
    canAccessApprovals,
    patchIssue,
    fetchSprints,
    formatSprintLabel,
    buildSprintSelectOptions,
    renderIssueCard,
    attachInlineIssueEditors,
    findDuplicateCandidates,
    getDaysStale,
    isBlocked,
    fetchAssignableUsers,
    populateAssigneeSelect,
    initLidiWidget
  };
})();
