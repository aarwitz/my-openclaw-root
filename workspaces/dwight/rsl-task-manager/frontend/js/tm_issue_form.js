window.TMIssueForm = (() => {
  async function submitIssueForm({ form, username, fetchJson, uploadImages, onCreated }) {
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

    const createdIssue = await fetch('/api/issues', {
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
  }

  return { submitIssueForm };
})();
