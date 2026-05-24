const state = {
  objective: 'Ship the daily update with clean owner handoffs',
  crew: [
    {
      name: 'Aaron',
      role: 'Commander',
      avatar: '🧠',
      status: 'active',
      current: 'Choosing the highest-value move for today',
      next: 'Approve daily update structure and owner assignments',
      progress: 64,
    },
    {
      name: 'Taylor',
      role: 'Strategist',
      avatar: '🛰️',
      status: 'waiting',
      current: 'Waiting on latest priorities + dependency notes',
      next: 'Confirm blockers and sequence the follow-ups',
      progress: 35,
    },
    {
      name: 'Jerry',
      role: 'Builder',
      avatar: '⚙️',
      status: 'active',
      current: 'Executing the next implementation task in queue',
      next: 'Report status and request review when ready',
      progress: 78,
    },
  ],
  meetings: [
    {
      title: 'EWAG Sync',
      time: 'Today · 3:00 PM',
      duration: '30 min',
      location: 'Telegram / quick standup',
      agenda: 'Review priorities, blockers, and owner handoffs',
    },
    {
      title: 'Planning Check-In',
      time: 'Mon · 10:00 AM',
      duration: '45 min',
      location: 'Remote',
      agenda: 'Lock near-term roadmap and dependencies',
    },
  ],
  queue: [
    {
      title: 'Finalize today\'s update',
      owner: 'Aaron',
      priority: 'P1',
      state: 'active',
    },
    {
      title: 'Validate meeting agenda + blockers',
      owner: 'Taylor',
      priority: 'P1',
      state: 'next',
    },
    {
      title: 'Push current implementation chunk',
      owner: 'Jerry',
      priority: 'P2',
      state: 'next',
    },
    {
      title: 'Tidy stale queue items',
      owner: 'Jerry',
      priority: 'P3',
      state: 'done',
    },
  ],
};

const statusMap = {
  active: { label: 'Active', className: 'status-active' },
  next: { label: 'Next Up', className: 'status-next' },
  waiting: { label: 'Waiting', className: 'status-waiting' },
  blocked: { label: 'Blocked', className: 'status-blocked' },
};

function renderObjective() {
  document.getElementById('todayObjective').textContent = state.objective;
  const active = state.queue.filter((item) => item.state === 'active').length;
  const blocked = state.crew.filter((member) => member.status === 'blocked').length;
  document.getElementById('queueSummary').textContent = `${active} active mission${active === 1 ? '' : 's'} · ${blocked} blocker${blocked === 1 ? '' : 's'} · ${state.meetings.length} meeting${state.meetings.length === 1 ? '' : 's'} ahead`;
}

function renderCrew() {
  const root = document.getElementById('crewGrid');
  root.innerHTML = state.crew.map((member) => {
    const status = statusMap[member.status] || statusMap.next;
    return `
      <article class="crew-card">
        <div class="crew-header">
          <div class="name-wrap">
            <div class="avatar">${member.avatar}</div>
            <div>
              <h4 class="crew-name">${member.name}</h4>
              <p class="role">${member.role}</p>
            </div>
          </div>
          <span class="status-pill ${status.className}">${status.label}</span>
        </div>
        <p class="info-label">Current mission</p>
        <p class="info-text">${member.current}</p>
        <p class="info-label">Next quest</p>
        <p class="info-text">${member.next}</p>
        <div class="progress-track">
          <div class="progress-fill" style="width:${member.progress}%"></div>
        </div>
      </article>
    `;
  }).join('');
}

function renderMeetings() {
  const root = document.getElementById('meetingsList');
  root.innerHTML = state.meetings.map((meeting) => `
    <article class="meeting-card">
      <div class="meeting-header">
        <div>
          <h4 class="meeting-title">${meeting.title}</h4>
          <p class="role">${meeting.agenda}</p>
        </div>
        <span class="status-pill status-next">Queued</span>
      </div>
      <div class="meeting-meta">
        <span class="meta-pill">🕒 ${meeting.time}</span>
        <span class="meta-pill">⏱️ ${meeting.duration}</span>
        <span class="meta-pill">📍 ${meeting.location}</span>
      </div>
    </article>
  `).join('');
}

function renderQueue() {
  const root = document.getElementById('queueList');
  root.innerHTML = state.queue.map((item, index) => `
    <article class="queue-card ${item.state === 'done' ? 'done' : ''}" data-index="${index}">
      <div class="meeting-header">
        <div>
          <h4 class="meeting-title">${item.title}</h4>
          <p class="role">Owner: ${item.owner}</p>
        </div>
        <span class="status-pill ${(statusMap[item.state] || statusMap.next).className}">${(statusMap[item.state] || statusMap.next).label}</span>
      </div>
      <div class="queue-meta">
        <span class="meta-pill">⚡ ${item.priority}</span>
        <span class="meta-pill">Tap to cycle state</span>
      </div>
    </article>
  `).join('');

  root.querySelectorAll('.queue-card').forEach((card) => {
    card.addEventListener('click', () => {
      const index = Number(card.dataset.index);
      const item = state.queue[index];
      const flow = ['next', 'active', 'done'];
      item.state = flow[(flow.indexOf(item.state) + 1) % flow.length] || 'next';
      renderAll();
    });
  });
}

function bindTabs() {
  document.querySelectorAll('.tab').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((tab) => tab.classList.remove('active'));
      document.querySelectorAll('.panel').forEach((panel) => panel.classList.remove('active'));
      button.classList.add('active');
      document.getElementById(`tab-${button.dataset.tab}`).classList.add('active');
    });
  });
}

function randomizeCrew() {
  const statuses = ['active', 'next', 'waiting'];
  state.crew = state.crew.map((member) => ({
    ...member,
    status: statuses[Math.floor(Math.random() * statuses.length)],
    progress: Math.max(12, Math.min(100, member.progress + Math.round((Math.random() - 0.5) * 24))),
  }));
}

function bindRefresh() {
  document.getElementById('refreshButton').addEventListener('click', () => {
    randomizeCrew();
    renderAll();
    if (window.Telegram?.WebApp?.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
    }
  });
}

function setupTelegram() {
  if (!window.Telegram?.WebApp) return;
  const tg = window.Telegram.WebApp;
  tg.ready();
  tg.expand();
  tg.setHeaderColor('#11131f');
  tg.setBackgroundColor('#090910');
}

function renderAll() {
  renderObjective();
  renderCrew();
  renderMeetings();
  renderQueue();
}

setupTelegram();
renderAll();
bindTabs();
bindRefresh();
