const nodeDetails = {
  jerry: {
    icon: '🤖', eyebrow: 'JERRY HQ', title: 'Jerry HQ',
    summary: 'Working on: build today\'s task plan',
    description: 'Jerry is the central dispatcher. He decides Aaron\'s next task, Taylor\'s next task, reports what he is personally working on, and flags blockers or missing inputs for the day.',
    meta: ['Priority: Daily handoffs', 'Blocker: None', 'Status: Dispatching'],
    link: 'http://localhost:8001', linkLabel: 'Open full task manager ↗'
  },
  aaron: {
    icon: '🧢', eyebrow: 'AARON QUEST', title: 'Aaron',
    summary: 'Next task: review daily priorities',
    description: 'Focus on the highest-value move for today: review priorities, approve sequencing, and unblock anything that speeds up the team.',
    meta: ['Owner: Aaron', 'State: Ready', 'Source: Jerry assignment'],
    link: 'http://localhost:8001', linkLabel: 'Open Aaron in task manager ↗'
  },
  taylor: {
    icon: '💁‍♀️', eyebrow: 'TAYLOR QUEST', title: 'Taylor',
    summary: 'Next task: resolve blockers + dependencies',
    description: 'Review dependencies, clarify blockers, and help shape clean handoffs so the rest of the day moves smoothly.',
    meta: ['Owner: Taylor', 'State: Ready', 'Source: Jerry assignment'],
    link: 'http://localhost:8001', linkLabel: 'Open Taylor in task manager ↗'
  },
  meetings: {
    icon: '📅', eyebrow: 'MEETING INFO', title: 'Meetings',
    summary: '3:00 PM EWAG sync',
    description: 'Upcoming meeting node for time, context, agenda, and direct links. Keep this concise so the map stays clean while still giving enough detail on tap.',
    meta: ['When: Today 3:00 PM', 'Type: Sync', 'Status: Upcoming'],
    link: 'http://localhost:8001', linkLabel: 'Open meeting details ↗'
  }
};

const overlay = document.getElementById('detailOverlay');
const modalIcon = document.getElementById('modalIcon');
const modalEyebrow = document.getElementById('modalEyebrow');
const modalTitle = document.getElementById('modalTitle');
const modalSummary = document.getElementById('modalSummary');
const modalDescription = document.getElementById('modalDescription');
const modalMeta = document.getElementById('modalMeta');
const modalLink = document.getElementById('modalLink');
const closeModalButton = document.getElementById('closeModal');

function setupTelegram() {
  if (!window.Telegram?.WebApp) return;
  const tg = window.Telegram.WebApp;
  tg.ready();
  tg.expand();
  tg.setHeaderColor('#79c7ff');
  tg.setBackgroundColor('#79c7ff');
}

function openModal(key) {
  const detail = nodeDetails[key];
  if (!detail) return;
  modalIcon.textContent = detail.icon;
  modalEyebrow.textContent = detail.eyebrow;
  modalTitle.textContent = detail.title;
  modalSummary.textContent = detail.summary;
  modalDescription.textContent = detail.description;
  modalMeta.innerHTML = (detail.meta || []).map(item => `<span class="meta-pill">${item}</span>`).join('');
  modalLink.href = detail.link;
  modalLink.textContent = detail.linkLabel;
  overlay.classList.remove('hidden');
  if (window.Telegram?.WebApp?.HapticFeedback) {
    window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
  }
}

function closeModal() { overlay.classList.add('hidden'); }

document.querySelectorAll('.tappable').forEach((node) => {
  node.addEventListener('click', () => openModal(node.dataset.node));
});

overlay.addEventListener('click', (event) => {
  if (event.target.dataset.close === 'true') closeModal();
});
closeModalButton.addEventListener('click', closeModal);
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeModal(); });
setupTelegram();
