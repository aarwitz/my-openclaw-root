const username = localStorage.getItem('username');
if (!username) {
    window.location.href = '/static/index.html';
}

document.getElementById('currentUser').textContent = username;
const { fetchJson, fetchSprints, buildSprintSelectOptions, renderIssueCard, attachInlineIssueEditors } = window.TM_SHARED;
const { prepareIssueForm, submitIssueForm } = window.TMIssueForm;
let currentSprint = null;
let currentSprintIssues = [];
let sprintList = [];
let sprintMap = new Map();

const createIssueModal = document.getElementById('createIssueModal');
const createIssueBtn = document.getElementById('createIssueBtn');
const closeModal = document.querySelector('#createIssueModal .close');
const cancelBtn = document.querySelector('#createIssueModal .cancel-btn');
const issueSprintSelect = document.getElementById('issueSprintId');
const issueImagesInput = document.getElementById('issueImages');
const issueImagesLabel = document.getElementById('issueImagesLabel');
const sprintPicker = document.getElementById('sprintPicker');
const sprintParams = new URLSearchParams(window.location.search);
const requestedSprintId = sprintParams.get('sprint_id');

document.getElementById('logoutBtn').addEventListener('click', () => {
    localStorage.removeItem('username');
    window.location.href = '/static/index.html';
});

createIssueBtn.addEventListener('click', async () => {
    await populateIssueSprintOptions();
    await prepareIssueForm({ form: document.getElementById('createIssueForm'), fetchJson });
    createIssueModal.classList.add('show');
});
closeModal.addEventListener('click', () => createIssueModal.classList.remove('show'));
cancelBtn.addEventListener('click', () => createIssueModal.classList.remove('show'));
window.addEventListener('click', (e) => {
    if (e.target === createIssueModal) createIssueModal.classList.remove('show');
});

if (issueImagesInput && issueImagesLabel) {
    issueImagesInput.addEventListener('change', () => {
        const files = issueImagesInput.files;
        issueImagesLabel.textContent = !files || files.length === 0 ? 'No files selected' : `${files.length} image${files.length === 1 ? '' : 's'} selected`;
    });
}

async function populateIssueSprintOptions() {
    issueSprintSelect.innerHTML = buildSprintSelectOptions(sprintList, currentSprint?.id ?? null, false, true);
    if (currentSprint) issueSprintSelect.value = String(currentSprint.id);
}

async function uploadIssueImages(issueId, files) {
    if (!files || files.length === 0) return;
    await Promise.all(Array.from(files).map((file) => {
        const formData = new FormData();
        formData.append('file', file);
        return fetch(`/api/issues/${issueId}/images?source_type=description&uploaded_by=${encodeURIComponent(username)}`, { method: 'POST', body: formData });
    }));
}

document.getElementById('createIssueForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await submitIssueForm({
            form: e.target,
            username,
            fetchJson,
            uploadImages: uploadIssueImages,
            onCreated: async () => {
                createIssueModal.classList.remove('show');
                e.target.reset();
                if (issueImagesLabel) issueImagesLabel.textContent = 'No files selected';
                if (currentSprint) await loadSprintIssues(currentSprint.id);
            }
        });
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'An error occurred');
    }
});

async function getActiveSprint() {
    try {
        const response = await fetch('/api/sprints/active');
        if (response.ok) return await response.json();
        return null;
    } catch (error) {
        console.error('Error fetching active sprint:', error);
        return null;
    }
}

async function loadSprint() {
    const sprintData = await fetchSprints();
    sprintList = sprintData.sprints;
    sprintMap = sprintData.sprintMap;
    sprintPicker.innerHTML = buildSprintSelectOptions(sprintList, requestedSprintId ?? null, false, false);

    let sprint = null;
    if (requestedSprintId) {
        try {
            sprint = await fetchJson(`/api/sprints/${requestedSprintId}`);
        } catch (error) {
            console.error('Error fetching requested sprint:', error);
        }
    }
    if (!sprint) sprint = await getActiveSprint();
    if (!sprint) {
        document.getElementById('noSprintMessage').style.display = 'block';
        document.getElementById('sprintBoard').style.display = 'none';
        document.getElementById('startSprintBtn').style.display = 'none';
        document.getElementById('endSprintBtn').style.display = 'none';
        sprintPicker.innerHTML = '<option value="">No sprint available</option>';
        return;
    }
    currentSprint = sprint;
    sprintPicker.value = String(sprint.id);
    await populateIssueSprintOptions();
    document.getElementById('noSprintMessage').style.display = 'none';
    document.getElementById('sprintBoard').style.display = 'grid';
    document.getElementById('sprintTitle').textContent = sprint.name;
    const sprintMeta = [];
    if (sprint.started_at) sprintMeta.push(`Started: ${new Date(sprint.started_at).toLocaleString()}`);
    if (sprint.is_active) sprintMeta.push('Status: Active');
    if (!sprint.is_active) sprintMeta.push('Status: Planned / inactive');
    document.getElementById('sprintInfo').textContent = sprintMeta.join(' · ');
    document.getElementById('startSprintBtn').style.display = sprint.is_active ? 'none' : 'block';
    document.getElementById('endSprintBtn').style.display = sprint.is_active ? 'block' : 'none';
    await loadSprintIssues(sprint.id);
}

async function loadSprintIssues(sprintId) {
    try {
        currentSprintIssues = await fetchJson(`/api/issues?sprint_id=${sprintId}`);
        document.querySelectorAll('.column-content').forEach((column) => {
            column.innerHTML = '';
        });
        const issuesByStatus = { to_do: [], in_progress: [], in_review: [], blocked: [], done: [] };
        currentSprintIssues.forEach((issue) => {
            if (!issuesByStatus[issue.status]) issuesByStatus[issue.status] = [];
            issuesByStatus[issue.status].push(issue);
        });
        Object.keys(issuesByStatus).forEach((status) => {
            const column = document.querySelector(`.column-content[data-status="${status}"]`);
            if (!column) return;
            const issues = issuesByStatus[status];
            document.querySelector(`.column[data-status="${status}"] .issue-count`).textContent = issues.length;
            column.innerHTML = issues.map((issue) => renderIssueCard(issue, { sprints: sprintList, sprintMap, viewHandler: 'viewIssue' })).join('');
        });
        attachInlineIssueEditors({ issues: currentSprintIssues, onUpdated: async () => { if (currentSprint) await loadSprintIssues(currentSprint.id); } });
        setupDragAndDrop();
    } catch (error) {
        console.error('Error loading sprint issues:', error);
    }
}

function viewIssue(issueId) {
    window.location.href = `/static/issue.html?id=${issueId}`;
}
window.viewIssue = viewIssue;

let draggedElement = null;
function setupDragAndDrop() {
    const cards = document.querySelectorAll('.sprint-issue-card, .issue-card-rich');
    const columns = document.querySelectorAll('.column-content');
    cards.forEach((card) => {
        card.draggable = true;
        card.addEventListener('dragstart', handleDragStart);
        card.addEventListener('dragend', handleDragEnd);
    });
    columns.forEach((column) => {
        column.addEventListener('dragover', handleDragOver);
        column.addEventListener('drop', handleDrop);
        column.addEventListener('dragenter', handleDragEnter);
        column.addEventListener('dragleave', handleDragLeave);
    });
}
function handleDragStart(e) {
    draggedElement = this;
    this.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
}
function handleDragEnd() {
    this.classList.remove('dragging');
    document.querySelectorAll('.column-content').forEach(col => col.classList.remove('drag-over'));
}
function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    return false;
}
function handleDragEnter() { this.classList.add('drag-over'); }
function handleDragLeave(e) { if (e.target.classList.contains('column-content')) this.classList.remove('drag-over'); }
async function handleDrop(e) {
    e.stopPropagation();
    e.preventDefault();
    const newStatus = this.dataset.status;
    const issueId = Number(draggedElement.dataset.issueId || draggedElement.dataset.issueId);
    try {
        await fetchJson(`/api/issues/${issueId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus, updated_by: username })
        });
        await loadSprintIssues(currentSprint.id);
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to update issue status');
    }
    return false;
}

document.getElementById('endSprintBtn').addEventListener('click', async () => {
    if (!currentSprint) return;
    if (!confirm('End this sprint? Issues will stay assigned to this sprint, but the sprint will be marked inactive.')) return;
    try {
        await fetchJson(`/api/sprints/${currentSprint.id}/end`, { method: 'POST' });
        alert('Sprint ended. Its issues remain assigned for history and review.');
        window.location.href = '/static/backlog.html';
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to end sprint');
    }
});

sprintPicker.addEventListener('change', (event) => {
    const sprintId = event.target.value;
    if (!sprintId) return;
    window.location.href = `/static/sprint.html?sprint_id=${sprintId}`;
});

document.getElementById('startSprintBtn').addEventListener('click', async () => {
    if (!currentSprint) return;
    if (!confirm('Start this sprint? Other active sprints will remain active.')) return;
    try {
        await fetchJson(`/api/sprints/${currentSprint.id}/start`, { method: 'POST' });
        await loadSprint();
        alert('Sprint started.');
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to start sprint');
    }
});

loadSprint();
