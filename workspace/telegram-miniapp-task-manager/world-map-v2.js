const nodeDetails = {
  jerry: {
    icon: '🤖',
    eyebrow: 'JERRY HQ',
    title: 'Jerry HQ',
    summary: 'Working on: build today\'s task plan',
    description: 'Jerry is the dispatcher hub. He decides what Aaron should do next, what Taylor should do next, reports what he is personally working on, and flags blockers or missing inputs.',
    link: 'http://localhost:8001',
    linkLabel: 'Open full task manager ↗',
  },
  aaron: {
    icon: '🧢',
    eyebrow: 'AARON TASK',
    title: 'Aaron',
    summary: 'Next task: review daily priorities',
    description: 'Jerry wants Aaron focused on the highest-value priority for today, including deciding sequencing, approvals, and anything that unblocks the team fastest.',
    link: 'http://localhost:8001',
    linkLabel: 'Open Aaron in task manager ↗',
  },
  taylor: {
    icon: '💁‍♀️',
    eyebrow: 'TAYLOR TASK',
    title: 'Taylor',
    summary: 'Next task: resolve blockers + dependencies',
    description: 'Jerry wants Taylor reviewing dependencies, clarifying blockers, and helping shape the clean handoff plan so the day moves smoothly.',
    link: 'http://localhost:8001',
    linkLabel: 'Open Taylor in task manager ↗',
  },
  meetings: {
    icon: '📅',
    eyebrow: 'MEETING INFO',
    title: 'Meetings',
    summary: '3:00 PM Product sync',
    description: 'Upcoming meeting node. Use this for agenda, timing, context, links, and any notes that should be visible from the map without cluttering the scene.',
    link: 'http://localhost:8001',
    linkLabel: 'Open meeting details ↗',
  },
};

const overlay = document.getElementById('detailOverlay');
const modalIcon = document.getElementById('modalIcon');
const modalEyebrow = document.getElementById('modalEyebrow');
const modalTitle = document.getElementById('modalTitle');
const modalSummary = document.getElementById('modalSummary');
const modalDescription = document.getElementById('modalDescription');
const modalLink = document.getElementById('modalLink');
const closeModalButton = document.getElementById('closeModal');

function openModal(key) {
  const detail = nodeDetails[key];
  if (!detail) return;
  modalIcon.textContent = detail.icon;
  modalEyebrow.textContent = detail.eyebrow;
  modalTitle.textContent = detail.title;
  modalSummary.textContent = detail.summary;
  modalDescription.textContent = detail.description;
  modalLink.href = detail.link;
  modalLink.textContent = detail.linkLabel;
  overlay.classList.remove('hidden');
}

function closeModal() {
  overlay.classList.add('hidden');
}

document.querySelectorAll('.tappable').forEach((node) => {
  node.addEventListener('click', () => openModal(node.dataset.node));
});

overlay.addEventListener('click', (event) => {
  if (event.target.dataset.close === 'true') closeModal();
});

closeModalButton.addEventListener('click', closeModal);

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeModal();
});
