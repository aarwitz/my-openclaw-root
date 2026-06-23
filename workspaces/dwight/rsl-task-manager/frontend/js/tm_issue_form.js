window.TMIssueForm = (() => {
  let assignableUsersPromise = null;
  let submitInFlight = false;

  function getAssignableUsers(fetchJson) {
    if (!assignableUsersPromise) {
      assignableUsersPromise = fetchJson('/api/users').then((users) => users.map((user) => user.username));
    }
    return assignableUsersPromise;
  }

  function renderAssignableUsers(form, usernames) {
    const assigneeInput = form?.querySelector('[name="assigned_to"][list]');
    if (!assigneeInput) return;
    const listId = assigneeInput.getAttribute('list');
    const datalist = listId ? document.getElementById(listId) : null;
    if (!datalist) return;
    datalist.innerHTML = (usernames || []).map((username) => `<option value="${window.TM_SHARED.escapeHtml(username)}"></option>`).join('');
  }

  function normalizeAssignee(form, usernames) {
    const assigneeInput = form?.querySelector('[name="assigned_to"]');
    if (!assigneeInput) return;
    const trimmed = String(assigneeInput.value || '').trim();
    if (!trimmed || trimmed.toLowerCase() === 'unassigned') {
      assigneeInput.value = '';
      return;
    }
    const canonical = (usernames || []).find((username) => username.toLowerCase() === trimmed.toLowerCase());
    if (!canonical) {
      throw new Error(`Unknown assignee. Choose one of: ${usernames.join(', ')}`);
    }
    assigneeInput.value = canonical;
  }

  async function prepareIssueForm({ form, fetchJson }) {
    const usernames = await getAssignableUsers(fetchJson);
    renderAssignableUsers(form, usernames);
    return usernames;
  }

  async function submitIssueForm({ form, username, fetchJson, uploadImages, onCreated }) {
    if (submitInFlight) {
      throw new Error('Issue creation already in progress');
    }
    submitInFlight = true;
    const submitButton = form?.querySelector('button[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    const originalLabel = submitButton?.textContent || '';
    const usernames = await prepareIssueForm({ form, fetchJson });
    try {
      normalizeAssignee(form, usernames);
      const formData = new FormData(form);
      formData.set('created_by', username);
      if (!formData.get('assigned_to')) formData.delete('assigned_to');
      if (!formData.get('acceptance_criteria')) formData.delete('acceptance_criteria');
      if (!formData.get('blocked_reason')) formData.delete('blocked_reason');
      if (!formData.get('branch')) formData.delete('branch');
      if (!formData.get('repo_slug')) formData.delete('repo_slug');
      if (!formData.get('story_points')) formData.delete('story_points');
      if (!formData.get('sprint_id')) formData.delete('sprint_id');

      const imageInput = form.querySelector('input[type="file"]');
      const files = imageInput?.files;

      if (submitButton) submitButton.textContent = 'Creating...';
      const createdIssue = await window.TM_SHARED.authenticatedFetch('/api/issues', {
        method: 'POST',
        body: formData
      }).then(async (response) => {
        const contentType = response.headers.get('content-type') || '';
        const body = contentType.includes('application/json') ? await response.json() : await response.text();
        if (!response.ok) throw new Error(body?.detail || body || `Request failed: ${response.status}`);
        return body;
      });

      if (uploadImages) {
        await uploadImages(createdIssue.id, files);
      }
      if (onCreated) await onCreated(createdIssue);
      return createdIssue;
    } finally {
      submitInFlight = false;
      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = originalLabel;
      }
    }
  }

  return { prepareIssueForm, submitIssueForm };
})();
