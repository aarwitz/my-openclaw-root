const username = localStorage.getItem('username');
if (!username) {
    window.location.href = '/static/index.html';
}

document.getElementById('currentUser').textContent = username;
const { escapeHtml, formatStatus, fetchJson, authenticatedFetch, buildSprintSelectOptions, fetchSprints, fetchAssignableUsers } = window.TM_SHARED;
let currentIssue = null;
let issueSprints = [];
let assignableUsers = [];
let createIssueSubmitInFlight = false;
const DEFAULT_GITHUB_REPO = 'aarwitz/Task-Manager';
const urlParams = new URLSearchParams(window.location.search);
const issueId = urlParams.get('id');
if (!issueId) {
    alert('No issue specified');
    window.location.href = '/static/backlog.html';
}

function getIssueRepoSlug(issue) {
    return issue?.repo_slug || DEFAULT_GITHUB_REPO;
}
function getBranchGitHubUrl(issue) {
    if (!issue?.branch) return null;
    return `https://github.com/${getIssueRepoSlug(issue)}/tree/${encodeURIComponent(issue.branch)}`;
}
function renderBranchDisplay(issue) {
    if (!issue?.branch) return 'None';
    const url = getBranchGitHubUrl(issue);
    const repoHint = escapeHtml(getIssueRepoSlug(issue));
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" style="color: var(--primary-color); text-decoration: none; border-bottom: 1px solid currentColor; cursor: pointer;">${escapeHtml(issue.branch)}</a> <span class="muted-text" style="margin-left:6px;">(${repoHint})</span>`;
}

document.getElementById('logoutBtn').addEventListener('click', () => {
    localStorage.removeItem('username');
    window.location.href = '/static/index.html';
});

const createIssueModal = document.getElementById('createIssueModal');
const createIssueBtn = document.getElementById('createIssueBtn');
const closeModal = document.querySelector('#createIssueModal .close');
const cancelBtn = document.querySelector('#createIssueModal .cancel-btn');
const newIssueImagesInput = document.getElementById('newIssueImages');
const newIssueImagesLabel = document.getElementById('newIssueImagesLabel');
const newIssueSprintSelect = document.getElementById('newIssueSprintId');
const issueStatusSelect = document.getElementById('issueStatusSelect');
const issueSprintSelect = document.getElementById('issueSprintSelect');
const issueAssignedToInput = document.getElementById('issueAssignedToInput');
const newIssueAssignedToInput = document.getElementById('newIssueAssignedToInput');
const assignableUsersList = document.getElementById('assignableUsersList');
const issueStatusSaving = document.getElementById('issueStatusSaving');
const issueSprintSaving = document.getElementById('issueSprintSaving');
const issueAssignedToSaving = document.getElementById('issueAssignedToSaving');
const commentImageUploadInput = document.getElementById('commentImageUpload');
const commentFileNameLabel = document.getElementById('commentFileName');

createIssueBtn.addEventListener('click', async () => {
    await populateIssueSprintOptions();
    await ensureAssignableUsersLoaded();
    createIssueModal.classList.add('show');
});
closeModal.addEventListener('click', () => createIssueModal.classList.remove('show'));
cancelBtn.addEventListener('click', () => createIssueModal.classList.remove('show'));
window.addEventListener('click', (e) => {
    if (e.target === createIssueModal) createIssueModal.classList.remove('show');
});

if (newIssueImagesInput && newIssueImagesLabel) {
    newIssueImagesInput.addEventListener('change', () => {
        const files = newIssueImagesInput.files;
        newIssueImagesLabel.textContent = !files || files.length === 0 ? 'No files selected' : `${files.length} image${files.length === 1 ? '' : 's'} selected`;
    });
}
if (commentImageUploadInput && commentFileNameLabel) {
    commentImageUploadInput.addEventListener('change', () => {
        const files = commentImageUploadInput.files;
        commentFileNameLabel.textContent = !files || files.length === 0 ? 'No files selected' : `${files.length} image${files.length === 1 ? '' : 's'} selected`;
    });
}

function setInlineSavingStatus(element, message = '', isError = false) {
    element.textContent = message;
    element.classList.toggle('error', Boolean(isError));
}

function renderTextWithLineBreaks(text) {
    return escapeHtml(text || '').replace(/\n/g, '<br>');
}

function renderAssignableUserOptions(usernames) {
    if (!assignableUsersList) return;
    assignableUsersList.innerHTML = (usernames || []).map((candidate) => (
        `<option value="${escapeHtml(candidate)}"></option>`
    )).join('');
}

function resolveAssignableUser(value, { allowBlank = true } = {}) {
    const trimmed = String(value || '').trim();
    if (!trimmed) return allowBlank ? '' : null;
    if (trimmed.toLowerCase() === 'unassigned') return '';
    return assignableUsers.find((candidate) => candidate.toLowerCase() === trimmed.toLowerCase()) || null;
}

async function ensureAssignableUsersLoaded() {
    if (assignableUsers.length) return assignableUsers;
    assignableUsers = await fetchAssignableUsers();
    renderAssignableUserOptions(assignableUsers);
    return assignableUsers;
}

function formatUploadMeta(image) {
    const uploadedAt = new Date(image.uploaded_at).toLocaleString();
    const source = image.source_type === 'comment' ? `Comment #${image.comment_id}` : image.source_type === 'description' ? 'Issue Description' : 'Issue Attachment';
    const by = image.uploaded_by ? ` by ${escapeHtml(image.uploaded_by)}` : '';
    return `Uploaded ${uploadedAt}${by} · ${source}`;
}

function renderInlineImages(images, cssClass = 'inline-image-list') {
    if (!images || images.length === 0) return '';
    return `<div class="${cssClass}">${images.map((image) => `
        <figure class="inline-image-item">
            <img src="/static/uploads/${encodeURIComponent(image.filename)}" alt="Attached image">
            <figcaption>${formatUploadMeta(image)}</figcaption>
        </figure>`).join('')}</div>`;
}

function humanizeActivityField(fieldName = '') {
    const labels = {
        sprint_id: 'sprint',
        assigned_to: 'assignee',
        repo_slug: 'repository',
        story_points: 'story points',
        blocked_reason: 'block reason',
    };
    return labels[fieldName] || fieldName.replace(/_/g, ' ');
}

function summarizeTextLengthChange(oldValue, newValue) {
    const oldLength = (oldValue || '').trim().length;
    const newLength = (newValue || '').trim().length;
    if (!oldLength && newLength) return `set (${newLength} chars)`;
    if (oldLength && !newLength) return `cleared (${oldLength} chars removed)`;
    if (!oldLength && !newLength) return 'updated';
    return `updated (${oldLength} -> ${newLength} chars)`;
}

function summarizeCommentPreview(value) {
    const compact = (value || '').replace(/\s+/g, ' ').trim();
    if (!compact) return 'added a comment';
    if (compact.length <= 48) return `commented: "${escapeHtml(compact)}"`;
    return `added a comment (${compact.length} chars)`;
}

function renderFieldChangeDetail(event) {
    const fieldLabel = escapeHtml(humanizeActivityField(event.field_name || ''));
    const oldValue = event.old_value || '';
    const newValue = event.new_value || '';
    const compactOld = oldValue.replace(/\s+/g, ' ').trim();
    const compactNew = newValue.replace(/\s+/g, ' ').trim();
    const isLongTextField = ['description', 'acceptance_criteria', 'blocked_reason'].includes(event.field_name);

    if (isLongTextField) {
        return `${summarizeTextLengthChange(oldValue, newValue)} ${fieldLabel}`;
    }
    if (!compactOld && compactNew) {
        return `set ${fieldLabel} to <em>${escapeHtml(compactNew)}</em>`;
    }
    if (compactOld && !compactNew) {
        return `cleared ${fieldLabel}`;
    }
    if (!compactOld && !compactNew) {
        return `updated ${fieldLabel}`;
    }
    if (compactOld.length <= 32 && compactNew.length <= 32) {
        return `changed ${fieldLabel} from <em>${escapeHtml(compactOld)}</em> to <em>${escapeHtml(compactNew)}</em>`;
    }
    return `updated ${fieldLabel}`;
}

function renderActivity(events = []) {
    const activityEl = document.getElementById('issueActivity');
    if (!events.length) {
        activityEl.innerHTML = '<div class="no-data"><p>No activity yet.</p></div>';
        return;
    }
    activityEl.innerHTML = events.map((event) => {
        const actor = event.actor ? `<strong>${escapeHtml(event.actor)}</strong> ` : '';
        const timestamp = new Date(event.created_at).toLocaleString();
        let detail = '';
        if (event.event_type === 'created') detail = 'created this issue';
        else if (event.event_type === 'comment_added') detail = summarizeCommentPreview(event.new_value);
        else if (event.field_name) detail = renderFieldChangeDetail(event);
        else detail = escapeHtml(event.event_type);
        return `<div class="activity-item"><div class="activity-line">${actor}${detail}</div><div class="activity-time">${timestamp}</div></div>`;
    }).join('');
}

function renderPlanning(issue) {
    document.getElementById('issueStoryPoints').textContent = issue.story_points != null ? String(issue.story_points) : 'None';
    document.getElementById('issueRepoSlug').textContent = issue.repo_slug || 'None';
    document.getElementById('issueAutoLaunchEnabled').textContent = issue.auto_launch_enabled ? 'Enabled' : 'Disabled';
    document.getElementById('issueLaunchState').textContent = issue.launch_state ? formatStatus(issue.launch_state) : 'Disabled';
    const launchParts = [];
    if (issue.last_launch_at) launchParts.push(`Last launch: ${new Date(issue.last_launch_at).toLocaleString()}`);
    if (issue.launch_error) launchParts.push(issue.launch_error);
    document.getElementById('issueLaunchDetail').innerHTML = launchParts.length ? renderTextWithLineBreaks(launchParts.join('\n')) : '<span class="muted-text">No launch attempts yet.</span>';
    document.getElementById('issueBlockedReason').textContent = issue.blocked_reason || 'None';
    document.getElementById('issueAcceptanceCriteria').innerHTML = issue.acceptance_criteria ? renderTextWithLineBreaks(issue.acceptance_criteria) : '<span class="muted-text">None</span>';
}

async function populateIssueSprintOptions() {
    const { sprints } = await fetchSprints();
    let activeSprint = null;
    try {
        activeSprint = await fetchJson('/api/sprints/active');
    } catch (error) {
        activeSprint = null;
    }
    issueSprints = sprints;
    newIssueSprintSelect.innerHTML = buildSprintSelectOptions(sprints, activeSprint?.id ?? null, false, true);
    if (activeSprint) newIssueSprintSelect.value = String(activeSprint.id);
}

async function populateStorySprintSelect(selectedSprintId = null) {
    const { sprints } = await fetchSprints({ includeArchived: true });
    issueSprints = sprints;
    issueSprintSelect.innerHTML = buildSprintSelectOptions(sprints, selectedSprintId, true, false);
    issueSprintSelect.value = selectedSprintId == null ? '' : String(selectedSprintId);
}

async function patchIssue(fields) {
    return fetchJson(`/api/issues/${issueId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...fields, updated_by: username })
    });
}

async function uploadIssueImages(issueId, files, sourceType, commentId = null) {
    if (!files || files.length === 0) return;
    const uploads = Array.from(files).map((file) => {
        const params = new URLSearchParams({ source_type: sourceType, uploaded_by: username });
        if (commentId !== null) params.set('comment_id', String(commentId));
        const formData = new FormData();
        formData.append('file', file);
        return authenticatedFetch(`/api/issues/${issueId}/images?${params.toString()}`, { method: 'POST', body: formData });
    });
    const results = await Promise.all(uploads);
    if (results.some((result) => !result.ok)) throw new Error('One or more image uploads failed');
}

document.getElementById('createIssueForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (createIssueSubmitInFlight) return;
    const assignedTo = resolveAssignableUser(newIssueAssignedToInput?.value, { allowBlank: true });
    const payload = {
        title: document.getElementById('newIssueTitle').value,
        description: document.getElementById('newIssueDescription').value,
        created_by: username,
        assigned_to: assignedTo || null,
        sprint_id: newIssueSprintSelect.value ? Number(newIssueSprintSelect.value) : null,
        branch: document.getElementById('newIssueBranch').value.trim() || null,
        repo_slug: document.getElementById('newIssueRepoSlug')?.value.trim() || null,
        auto_launch_enabled: document.getElementById('newIssueAutoLaunchEnabled')?.checked || false,
        acceptance_criteria: document.getElementById('newIssueAcceptanceCriteria').value.trim() || null,
        story_points: document.getElementById('newIssueStoryPoints').value ? Number(document.getElementById('newIssueStoryPoints').value) : null,
        blocked_reason: document.getElementById('newIssueBlockedReason').value.trim() || null
    };
    const submitButton = e.target.querySelector('button[type="submit"]');
    const originalLabel = submitButton?.textContent || '';
    try {
        createIssueSubmitInFlight = true;
        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = 'Creating...';
        }
        if (newIssueAssignedToInput?.value && assignedTo === null) {
            throw new Error(`Unknown assignee. Choose one of: ${assignableUsers.join(', ')}`);
        }
        const createdIssue = await fetchJson('/api/issues', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        await uploadIssueImages(createdIssue.id, newIssueImagesInput?.files, 'description');
        createIssueModal.classList.remove('show');
        document.getElementById('createIssueForm').reset();
        newIssueImagesLabel.textContent = 'No files selected';
        if (newIssueAssignedToInput) newIssueAssignedToInput.value = '';
        window.location.href = `/static/issue.html?id=${createdIssue.id}`;
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'An error occurred');
    } finally {
        createIssueSubmitInFlight = false;
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.textContent = originalLabel;
        }
    }
});

async function loadIssue() {
    try {
        await ensureAssignableUsersLoaded();
        const issue = await fetchJson(`/api/issues/${issueId}`);
        currentIssue = issue;
        document.getElementById('issueId').textContent = `#${issue.id}`;
        document.getElementById('issueTitle').textContent = issue.title;
        const descriptionImages = (issue.images || []).filter((image) => image.source_type === 'description');
        document.getElementById('issueDescription').innerHTML = `<div class="issue-text">${renderTextWithLineBreaks(issue.description)}</div>${renderInlineImages(descriptionImages)}`;
        document.getElementById('issueCreated').textContent = new Date(issue.created_at).toLocaleString();
        document.getElementById('issueCreatedBy').textContent = issue.created_by;
        issueAssignedToInput.value = issue.assigned_to || '';
        issueAssignedToInput.disabled = false;
        document.getElementById('issueBranch').innerHTML = renderBranchDisplay(issue);
        const statusBadge = document.getElementById('issueStatus');
        statusBadge.textContent = formatStatus(issue.status);
        statusBadge.className = `status-badge ${issue.status}`;
        issueStatusSelect.value = issue.status;
        issueStatusSelect.disabled = false;
        await populateStorySprintSelect(issue.sprint_id);
        issueSprintSelect.disabled = false;
        renderPlanning(issue);
        renderActivity(issue.activity_events || []);
        setInlineSavingStatus(issueStatusSaving, '');
        setInlineSavingStatus(issueSprintSaving, '');
        setInlineSavingStatus(issueAssignedToSaving, '');
        loadComments(issue.comments || []);
        loadImages(issue.images || []);
    } catch (error) {
        console.error('Error loading issue:', error);
        const detail = error?.message || 'An error occurred';
        alert(detail);
    }
}

function loadComments(comments) {
    const commentsList = document.getElementById('commentsList');
    if (comments.length === 0) {
        commentsList.innerHTML = '<div class="no-data"><p>No comments yet. Be the first to comment!</p></div>';
        return;
    }
    commentsList.innerHTML = comments.map(comment => `
        <div class="comment">
            <div class="comment-header">
                <span class="comment-author">${escapeHtml(comment.username)}</span>
                <span class="comment-date">${new Date(comment.created_at).toLocaleString()}</span>
            </div>
            <div class="comment-content">${renderTextWithLineBreaks(comment.content)}</div>
            ${renderInlineImages(comment.images || [], 'inline-image-list comment-image-list')}
        </div>`).join('');
}

document.getElementById('addCommentForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const content = document.getElementById('commentContent').value;
    try {
        const createdComment = await fetchJson(`/api/issues/${issueId}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, username })
        });
        await uploadIssueImages(issueId, commentImageUploadInput?.files, 'comment', createdComment.id);
        document.getElementById('addCommentForm').reset();
        commentFileNameLabel.textContent = 'No files selected';
        await loadIssue();
    } catch (error) {
        console.error('Error:', error);
        alert(error.message || 'An error occurred');
    }
});

function loadImages(images) {
    const issueImages = document.getElementById('issueImages');
    const standaloneImages = (images || []).filter((image) => image.source_type === 'issue');
    if (standaloneImages.length === 0) {
        issueImages.innerHTML = '<div class="no-data"><p>No standalone images uploaded yet.</p></div>';
        return;
    }
    issueImages.innerHTML = standaloneImages.map(image => `
        <div class="image-item">
            <img src="/static/uploads/${encodeURIComponent(image.filename)}" alt="Issue image">
            <button class="image-delete-btn" onclick="deleteImage(${image.id})">Delete</button>
            <div class="image-meta"><div class="image-meta-line">${formatUploadMeta(image)}</div></div>
        </div>`).join('');
}

let deleteConfirmState = false;
let deleteConfirmTimeoutId = null;
const deleteIssueBtn = document.getElementById('deleteIssueBtn');
function resetDeleteButtonState() {
    deleteConfirmState = false;
    deleteIssueBtn.disabled = false;
    deleteIssueBtn.textContent = 'Delete Issue';
    if (deleteConfirmTimeoutId) {
        clearTimeout(deleteConfirmTimeoutId);
        deleteConfirmTimeoutId = null;
    }
}
deleteIssueBtn.addEventListener('click', async () => {
    if (!currentIssue) return;
    if (!deleteConfirmState) {
        deleteConfirmState = true;
        deleteIssueBtn.textContent = 'Click again to confirm';
        deleteConfirmTimeoutId = setTimeout(resetDeleteButtonState, 5000);
        return;
    }
    deleteIssueBtn.disabled = true;
    deleteIssueBtn.textContent = 'Deleting...';
    try {
        await fetchJson(`/api/issues/${issueId}`, { method: 'DELETE' });
        window.location.href = '/static/backlog.html';
    } catch (error) {
        console.error('Error deleting issue:', error);
        alert(error.message || 'An error occurred while deleting the issue');
    } finally {
        resetDeleteButtonState();
    }
});

function cancelTitleEdit() {
    document.getElementById('issueTitle').style.display = 'block';
    document.getElementById('issueTitleEdit').style.display = 'none';
    document.getElementById('titleEditControls').style.display = 'none';
    document.getElementById('editTitleBtn').style.display = 'inline-block';
}
function cancelDescriptionEdit() {
    document.getElementById('issueDescription').style.display = 'block';
    document.getElementById('issueDescriptionEdit').style.display = 'none';
    document.getElementById('descriptionEditControls').style.display = 'none';
    document.getElementById('editDescriptionBtn').style.display = 'inline-block';
}
function cancelBranchEdit() {
    document.getElementById('issueBranch').style.display = 'inline';
    document.getElementById('issueBranchEdit').style.display = 'none';
    document.getElementById('branchEditControls').style.display = 'none';
    document.getElementById('editBranchBtn').style.display = 'inline-block';
}
function cancelPlanningEdit() {
    document.getElementById('issuePlanningView').style.display = 'grid';
    document.getElementById('issuePlanningEditWrap').style.display = 'none';
    document.getElementById('editPlanningBtn').style.display = 'inline-block';
}

document.getElementById('editTitleBtn').addEventListener('click', () => {
    document.getElementById('issueTitle').style.display = 'none';
    document.getElementById('issueTitleEdit').style.display = 'block';
    document.getElementById('issueTitleEdit').value = currentIssue?.title || '';
    document.getElementById('titleEditControls').style.display = 'flex';
    document.getElementById('editTitleBtn').style.display = 'none';
});
document.getElementById('cancelTitleBtn').addEventListener('click', cancelTitleEdit);
document.getElementById('saveTitleBtn').addEventListener('click', async () => {
    const newTitle = document.getElementById('issueTitleEdit').value.trim();
    if (!newTitle) return alert('Title cannot be empty');
    try {
        currentIssue = await patchIssue({ title: newTitle });
        document.getElementById('issueTitle').textContent = currentIssue.title;
        renderActivity(currentIssue.activity_events || []);
        cancelTitleEdit();
    } catch (error) {
        alert(error.message || 'Failed to update title');
    }
});

document.getElementById('editDescriptionBtn').addEventListener('click', () => {
    document.getElementById('issueDescription').style.display = 'none';
    document.getElementById('issueDescriptionEdit').style.display = 'block';
    document.getElementById('issueDescriptionEdit').value = currentIssue?.description || '';
    document.getElementById('descriptionEditControls').style.display = 'flex';
    document.getElementById('editDescriptionBtn').style.display = 'none';
});
document.getElementById('cancelDescriptionBtn').addEventListener('click', cancelDescriptionEdit);
document.getElementById('saveDescriptionBtn').addEventListener('click', async () => {
    const newDescription = document.getElementById('issueDescriptionEdit').value.trim();
    if (!newDescription) return alert('Description cannot be empty');
    try {
        currentIssue = await patchIssue({ description: newDescription });
        const descriptionImages = (currentIssue.images || []).filter((image) => image.source_type === 'description');
        document.getElementById('issueDescription').innerHTML = `<div class="issue-text">${renderTextWithLineBreaks(currentIssue.description)}</div>${renderInlineImages(descriptionImages)}`;
        renderActivity(currentIssue.activity_events || []);
        cancelDescriptionEdit();
    } catch (error) {
        alert(error.message || 'Failed to update description');
    }
});

document.getElementById('editBranchBtn').addEventListener('click', () => {
    document.getElementById('issueBranch').style.display = 'none';
    document.getElementById('issueBranchEdit').style.display = 'block';
    document.getElementById('issueBranchEdit').value = currentIssue?.branch || '';
    document.getElementById('branchEditControls').style.display = 'flex';
    document.getElementById('editBranchBtn').style.display = 'none';
});
document.getElementById('cancelBranchBtn').addEventListener('click', cancelBranchEdit);
document.getElementById('saveBranchBtn').addEventListener('click', async () => {
    try {
        currentIssue = await patchIssue({ branch: document.getElementById('issueBranchEdit').value.trim() || null });
        document.getElementById('issueBranch').innerHTML = renderBranchDisplay(currentIssue);
        renderActivity(currentIssue.activity_events || []);
        cancelBranchEdit();
    } catch (error) {
        alert(error.message || 'Failed to update branch');
    }
});

document.getElementById('editPlanningBtn').addEventListener('click', () => {
    document.getElementById('issuePlanningView').style.display = 'none';
    document.getElementById('issuePlanningEditWrap').style.display = 'block';
    document.getElementById('editPlanningBtn').style.display = 'none';
    document.getElementById('issueStoryPointsEdit').value = currentIssue?.story_points ?? '';
    document.getElementById('issueRepoSlugEdit').value = currentIssue?.repo_slug || '';
    document.getElementById('issueBlockedReasonEdit').value = currentIssue?.blocked_reason || '';
    document.getElementById('issueAutoLaunchEnabledEdit').checked = Boolean(currentIssue?.auto_launch_enabled);
    document.getElementById('issueAcceptanceCriteriaEdit').value = currentIssue?.acceptance_criteria || '';
});
document.getElementById('cancelPlanningBtn').addEventListener('click', cancelPlanningEdit);
document.getElementById('savePlanningBtn').addEventListener('click', async () => {
    const payload = {
        story_points: document.getElementById('issueStoryPointsEdit').value ? Number(document.getElementById('issueStoryPointsEdit').value) : null,
        repo_slug: document.getElementById('issueRepoSlugEdit').value.trim() || null,
        blocked_reason: document.getElementById('issueBlockedReasonEdit').value.trim() || null,
        auto_launch_enabled: document.getElementById('issueAutoLaunchEnabledEdit').checked,
        acceptance_criteria: document.getElementById('issueAcceptanceCriteriaEdit').value.trim() || null
    };
    try {
        currentIssue = await patchIssue(payload);
        renderPlanning(currentIssue);
        const statusBadge = document.getElementById('issueStatus');
        statusBadge.textContent = formatStatus(currentIssue.status);
        statusBadge.className = `status-badge ${currentIssue.status}`;
        issueStatusSelect.value = currentIssue.status;
        renderActivity(currentIssue.activity_events || []);
        cancelPlanningEdit();
    } catch (error) {
        alert(error.message || 'Failed to update planning');
    }
});

issueStatusSelect.addEventListener('change', async (event) => {
    const previousValue = currentIssue?.status || 'to_do';
    const nextValue = event.target.value;
    if (!currentIssue || nextValue === previousValue) return;
    issueStatusSelect.disabled = true;
    setInlineSavingStatus(issueStatusSaving, 'Saving...');
    try {
        currentIssue = await patchIssue({ status: nextValue });
        const statusBadge = document.getElementById('issueStatus');
        statusBadge.textContent = formatStatus(currentIssue.status);
        statusBadge.className = `status-badge ${currentIssue.status}`;
        issueStatusSelect.value = currentIssue.status;
        renderPlanning(currentIssue);
        renderActivity(currentIssue.activity_events || []);
        setInlineSavingStatus(issueStatusSaving, 'Saved');
        setTimeout(() => setInlineSavingStatus(issueStatusSaving, ''), 1200);
    } catch (error) {
        issueStatusSelect.value = previousValue;
        setInlineSavingStatus(issueStatusSaving, error.message || 'Failed to save', true);
    } finally {
        issueStatusSelect.disabled = false;
    }
});
issueSprintSelect.addEventListener('change', async (event) => {
    const previousValue = currentIssue?.sprint_id == null ? '' : String(currentIssue.sprint_id);
    const nextValue = event.target.value;
    if (!currentIssue || nextValue === previousValue) return;
    issueSprintSelect.disabled = true;
    setInlineSavingStatus(issueSprintSaving, 'Saving...');
    try {
        currentIssue = await patchIssue({ sprint_id: nextValue === '' ? null : Number(nextValue) });
        issueSprintSelect.value = currentIssue.sprint_id == null ? '' : String(currentIssue.sprint_id);
        renderActivity(currentIssue.activity_events || []);
        setInlineSavingStatus(issueSprintSaving, 'Saved');
        setTimeout(() => setInlineSavingStatus(issueSprintSaving, ''), 1200);
    } catch (error) {
        issueSprintSelect.value = previousValue;
        setInlineSavingStatus(issueSprintSaving, error.message || 'Failed to save', true);
    } finally {
        issueSprintSelect.disabled = false;
    }
});
issueAssignedToInput.addEventListener('change', async (event) => {
    const previousValue = currentIssue?.assigned_to || '';
    const nextValue = resolveAssignableUser(event.target.value, { allowBlank: true });
    if (event.target.value && nextValue === null) {
        issueAssignedToInput.value = previousValue;
        setInlineSavingStatus(issueAssignedToSaving, `Unknown assignee. Choose one of: ${assignableUsers.join(', ')}`, true);
        return;
    }
    if (!currentIssue || nextValue === previousValue) return;
    issueAssignedToInput.disabled = true;
    setInlineSavingStatus(issueAssignedToSaving, 'Saving...');
    try {
        currentIssue = await patchIssue({ assigned_to: nextValue === '' ? null : nextValue });
        issueAssignedToInput.value = currentIssue.assigned_to || '';
        renderActivity(currentIssue.activity_events || []);
        setInlineSavingStatus(issueAssignedToSaving, 'Saved');
        setTimeout(() => setInlineSavingStatus(issueAssignedToSaving, ''), 1200);
    } catch (error) {
        issueAssignedToInput.value = previousValue;
        setInlineSavingStatus(issueAssignedToSaving, error.message || 'Failed to save', true);
    } finally {
        issueAssignedToInput.disabled = false;
    }
});

let selectedFile = null;
document.getElementById('imageUpload').addEventListener('change', (e) => {
    const file = e.target.files[0];
    const fileName = document.getElementById('fileName');
    const uploadBtn = document.getElementById('uploadImageBtn');
    if (file) {
        selectedFile = file;
        fileName.textContent = file.name;
        uploadBtn.style.display = 'inline-block';
    } else {
        selectedFile = null;
        fileName.textContent = 'No file chosen';
        uploadBtn.style.display = 'none';
    }
});
document.getElementById('uploadImageBtn').addEventListener('click', async () => {
    if (!selectedFile) return alert('Please select a file');
    const progressEl = document.getElementById('uploadProgress');
    progressEl.textContent = 'Uploading...';
    const formData = new FormData();
    formData.append('file', selectedFile);
    try {
        await fetchJson(`/api/issues/${issueId}/images?source_type=issue&uploaded_by=${encodeURIComponent(username)}`, { method: 'POST', body: formData });
        progressEl.textContent = 'Upload successful!';
        document.getElementById('imageUpload').value = '';
        document.getElementById('fileName').textContent = 'No file chosen';
        document.getElementById('uploadImageBtn').style.display = 'none';
        selectedFile = null;
        setTimeout(async () => {
            progressEl.textContent = '';
            await loadIssue();
        }, 1000);
    } catch (error) {
        progressEl.textContent = '';
        alert(error.message || 'Failed to upload image');
    }
});

async function deleteImage(imageId) {
    if (!confirm('Are you sure you want to delete this image?')) return;
    try {
        await fetchJson(`/api/issues/${issueId}/images/${imageId}`, { method: 'DELETE' });
        await loadIssue();
    } catch (error) {
        alert(error.message || 'Failed to delete image');
    }
}
window.deleteImage = deleteImage;

loadIssue();
