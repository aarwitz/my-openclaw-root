const username = localStorage.getItem('username');
if (!username) {
    window.location.href = '/static/index.html';
}

document.getElementById('currentUser').textContent = username;
const { fetchJson, fetchSprints, buildSprintSelectOptions, renderIssueCard, attachInlineIssueEditors, findDuplicateCandidates, STATUS_OPTIONS, formatSprintLabel } = window.TM_SHARED;
const { submitIssueForm } = window.TMIssueForm;
let backlogIssues = [];
let backlogSprints = [];
let backlogSprintMap = new Map();
let archivedSprints = [];
let archivedVisible = false;

const createIssueModal = document.getElementById('createIssueModal');
const createIssueBtn = document.getElementById('createIssueBtn');
const closeModal = document.querySelector('#createIssueModal .close');
const cancelBtn = document.querySelector('#createIssueModal .cancel-btn');
const issueImagesInput = document.getElementById('issueImages');
const issueImagesLabel = document.getElementById('issueImagesLabel');
const issueSprintSelect = document.getElementById('issueSprintId');
const toggleArchivedSprintsBtn = document.getElementById('toggleArchivedSprintsBtn');
const archivedSprintsSection = document.getElementById('archivedSprintsSection');

document.getElementById('logoutBtn').addEventListener('click', () => {
    localStorage.removeItem('username');
    window.location.href = '/static/index.html';
});

createIssueBtn.addEventListener('click', async () => {
    await populateIssueSprintOptions();
    createIssueModal.classList.add('show');
});
closeModal.addEventListener('click', () => createIssueModal.classList.remove('show'));
cancelBtn.addEventListener('click', () => createIssueModal.classList.remove('show'));
window.addEventListener('click', (e) => {
    if (e.target === createIssueModal) createIssueModal.classList.remove('show');
});

toggleArchivedSprintsBtn.addEventListener('click', async () => {
    archivedVisible = !archivedVisible;
    archivedSprintsSection.style.display = archivedVisible ? 'block' : 'none';
    toggleArchivedSprintsBtn.textContent = archivedVisible ? 'Hide Archived Sprints' : 'View Archived Sprints';
    if (archivedVisible) await loadArchivedSprints();
});

if (issueImagesInput && issueImagesLabel) {
    issueImagesInput.addEventListener('change', () => {
        const files = issueImagesInput.files;
        issueImagesLabel.textContent = !files || files.length === 0 ? 'No files selected' : `${files.length} image${files.length === 1 ? '' : 's'} selected`;
    });
}

async function populateIssueSprintOptions() {
    if (!issueSprintSelect) return;
    try {
        const activeSprint = await fetch('/api/sprints/active').then(r => r.ok ? r.json() : null);
        issueSprintSelect.innerHTML = buildSprintSelectOptions(backlogSprints, activeSprint?.id ?? null, false, true);
        if (activeSprint) issueSprintSelect.value = String(activeSprint.id);
    } catch (error) {
        console.error('Error loading sprint options:', error);
    }
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
                await loadIssues();
            }
        });
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'An error occurred');
    }
});

const createSprintModal = document.getElementById('createSprintModal');
const createSprintBtn = document.getElementById('createSprintBtn');
const closeSprintModal = document.querySelector('#createSprintModal .close');
const cancelSprintBtn = document.querySelector('#createSprintModal .cancel-sprint-btn');
createSprintBtn.addEventListener('click', () => createSprintModal.classList.add('show'));
closeSprintModal.addEventListener('click', () => createSprintModal.classList.remove('show'));
cancelSprintBtn.addEventListener('click', () => createSprintModal.classList.remove('show'));
window.addEventListener('click', (e) => {
    if (e.target === createSprintModal) createSprintModal.classList.remove('show');
});

document.getElementById('createSprintForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        await fetchJson('/api/sprints', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: document.getElementById('sprintName').value })
        });
        createSprintModal.classList.remove('show');
        document.getElementById('createSprintForm').reset();
        await loadSprints();
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'An error occurred');
    }
});

async function loadSprints() {
    try {
        const { sprints, sprintMap } = await fetchSprints();
        backlogSprints = sprints;
        backlogSprintMap = sprintMap;
        const sprintsList = document.getElementById('sprintsList');
        if (sprints.length === 0) {
            sprintsList.innerHTML = '<div class="no-data"><p>No sprints created yet. Create one to get started!</p></div>';
            if (archivedVisible) await loadArchivedSprints();
            return;
        }
        sprintsList.innerHTML = sprints.map(sprint => `
            <div class="sprint-card ${sprint.is_active ? 'active' : ''}">
                <h4>${formatSprintLabel(sprint)}</h4>
                <div class="sprint-meta">
                    <p>${sprint.started_at ? `Started: ${new Date(sprint.started_at).toLocaleDateString()}` : 'Not started yet'}</p>
                    ${sprint.ended_at ? `<p>Ended: ${new Date(sprint.ended_at).toLocaleDateString()}</p>` : ''}
                </div>
                <div class="sprint-actions">
                    ${!sprint.is_active ? `<button class="btn btn-success btn-sm" onclick="startSprint(${sprint.id})">Start</button>` : `<button class="btn btn-danger btn-sm" onclick="endSprint(${sprint.id})">End</button>`}
                    <button class="btn btn-secondary btn-sm" onclick="viewSprint(${sprint.id})">View</button>
                    ${!sprint.is_active ? `<button class="btn btn-secondary btn-sm" onclick="archiveSprint(${sprint.id})">Archive</button>` : ''}
                </div>
            </div>
        `).join('');
        await populateIssueSprintOptions();
        if (archivedVisible) await loadArchivedSprints();
    } catch (error) {
        console.error('Error loading sprints:', error);
    }
}

async function loadArchivedSprints() {
    try {
        const { sprints } = await fetchSprints({ archivedOnly: true });
        archivedSprints = sprints;
        const archivedList = document.getElementById('archivedSprintsList');
        if (sprints.length === 0) {
            archivedList.innerHTML = '<div class="no-data"><p>No archived sprints.</p></div>';
            return;
        }
        archivedList.innerHTML = sprints.map(sprint => `
            <div class="sprint-card">
                <h4>${formatSprintLabel(sprint)}</h4>
                <div class="sprint-meta">
                    ${sprint.started_at ? `<p>Started: ${new Date(sprint.started_at).toLocaleDateString()}</p>` : '<p>Never started</p>'}
                    ${sprint.ended_at ? `<p>Ended: ${new Date(sprint.ended_at).toLocaleDateString()}</p>` : ''}
                </div>
                <div class="sprint-actions">
                    <button class="btn btn-secondary btn-sm" onclick="unarchiveSprint(${sprint.id})">Unarchive</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteSprint(${sprint.id})">Delete</button>
                </div>
            </div>
        `).join('');
    } catch (error) {
        console.error('Error loading archived sprints:', error);
    }
}

async function startSprint(sprintId) {
    if (!confirm('Start this sprint? Other active sprints will remain active.')) return;
    try {
        await fetchJson(`/api/sprints/${sprintId}/start`, { method: 'POST' });
        await loadSprints();
        alert('Sprint started!');
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to start sprint');
    }
}
window.startSprint = startSprint;

async function endSprint(sprintId) {
    if (!confirm('End this sprint? Issues will stay assigned to this sprint, but the sprint will be marked inactive.')) return;
    try {
        await fetchJson(`/api/sprints/${sprintId}/end`, { method: 'POST' });
        await loadSprints();
        await loadIssues();
        alert('Sprint ended. Its issues remain assigned for history and review.');
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to end sprint');
    }
}
window.endSprint = endSprint;

async function archiveSprint(sprintId) {
    if (!confirm('Archive this sprint? It will disappear from Available Sprints.')) return;
    try {
        await fetchJson(`/api/sprints/${sprintId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_archived: true })
        });
        await loadSprints();
        alert('Sprint archived.');
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to archive sprint');
    }
}
window.archiveSprint = archiveSprint;

async function unarchiveSprint(sprintId) {
    try {
        await fetchJson(`/api/sprints/${sprintId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_archived: false })
        });
        await loadSprints();
        alert('Sprint restored.');
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to unarchive sprint');
    }
}
window.unarchiveSprint = unarchiveSprint;

async function deleteSprint(sprintId) {
    if (!confirm('Delete this sprint? Any linked issues will be moved to backlog.')) return;
    try {
        await fetchJson(`/api/sprints/${sprintId}`, { method: 'DELETE' });
        await loadSprints();
        await loadIssues();
        alert('Sprint deleted.');
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'Failed to delete sprint');
    }
}
window.deleteSprint = deleteSprint;

function viewSprint(sprintId) {
    window.location.href = `/static/sprint.html?sprint_id=${sprintId}`;
}
window.viewSprint = viewSprint;

async function loadIssues() {
    try {
        const response = await fetchJson('/api/issues?in_backlog=true');
        backlogIssues = response;
        const issuesList = document.getElementById('issuesList');
        if (backlogIssues.length === 0) {
            issuesList.innerHTML = '<div class="no-data"><h3>No issues in backlog</h3><p>Create an issue to get started!</p></div>';
            return;
        }
        const sortBy = document.getElementById('sortBy').value;
        const sortedIssues = [...backlogIssues];
        if (sortBy === 'status') {
            const statusOrder = STATUS_OPTIONS.reduce((acc, item, index) => ({ ...acc, [item.value]: index }), {});
            sortedIssues.sort((a, b) => (statusOrder[a.status] ?? 99) - (statusOrder[b.status] ?? 99));
        } else {
            sortedIssues.sort((a, b) => new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at));
        }
        const duplicateMap = findDuplicateCandidates(sortedIssues);
        issuesList.innerHTML = sortedIssues.map(issue => renderIssueCard(issue, { sprints: backlogSprints, sprintMap: backlogSprintMap, duplicateMap, viewHandler: 'viewIssue' })).join('');
        attachInlineIssueEditors({ issues: backlogIssues, onUpdated: () => loadIssues() });
    } catch (error) {
        console.error('Error loading issues:', error);
    }
}

function viewIssue(issueId) {
    window.location.href = `/static/issue.html?id=${issueId}`;
}
window.viewIssue = viewIssue;

document.getElementById('sortBy').addEventListener('change', loadIssues);
loadSprints().then(loadIssues);
