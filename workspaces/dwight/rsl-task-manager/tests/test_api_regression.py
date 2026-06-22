import io
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / 'backend'
import sys
sys.path.insert(0, str(BACKEND_DIR))

TMPDIR = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = f"sqlite:///{Path(TMPDIR.name) / 'test_taskmanager.db'}"

import main  # noqa: E402
import models  # noqa: E402
from database import Base, engine, SessionLocal  # noqa: E402

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
main.run_safe_migrations()
main.cleanup_priority_column_if_present()

client = TestClient(main.app)
PNG_BYTES = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x02\x00\x01'
    b'\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82'
)


def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    main.run_safe_migrations()
    main.cleanup_priority_column_if_present()


def create_sprint(name='Sprint A'):
    response = client.post('/api/sprints', json={'name': name})
    assert response.status_code == 200
    return response.json()


def create_issue(**overrides):
    payload = {
        'title': 'Test issue',
        'description': 'Test description',
        'created_by': 'Jerry',
    }
    payload.update(overrides)
    response = client.post('/api/issues', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _clean_db():
    reset_db()
    yield
    reset_db()


def test_login_rejects_unknown_user():
    response = client.post('/api/users/login', json={'username': 'Mallory'})
    assert response.status_code == 400
    assert 'Invalid username' in response.text


def test_create_issue_via_multipart_defaults_to_active_sprint():
    sprint = create_sprint()
    start = client.post(f"/api/sprints/{sprint['id']}/start")
    assert start.status_code == 200

    response = client.post(
        '/api/issues',
        data={
            'title': 'Multipart issue',
            'description': 'Created from form',
            'created_by': 'Jerry',
            'assigned_to': 'Aaron',
            'story_points': '5',
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['sprint_id'] == sprint['id']
    assert body['assigned_to'] == 'Aaron'
    assert body['story_points'] == 5


def test_create_issue_reuses_recent_duplicate_json_payload():
    payload = {
        'title': 'Duplicate guard',
        'description': 'Same request twice should not duplicate',
        'created_by': 'Jerry',
        'assigned_to': 'Aaron',
        'acceptance_criteria': 'One row only',
        'story_points': 3,
    }
    first = client.post('/api/issues', json=payload)
    assert first.status_code == 201, first.text

    second = client.post('/api/issues', json=payload)
    assert second.status_code == 200, second.text
    assert second.json()['id'] == first.json()['id']

    issues = client.get('/api/issues')
    assert issues.status_code == 200, issues.text
    assert len(issues.json()) == 1


def test_create_issue_reuses_recent_duplicate_multipart_payload():
    form = {
        'title': 'Multipart duplicate guard',
        'description': 'Same form twice should not duplicate',
        'created_by': 'Jerry',
        'assigned_to': 'Aaron',
        'story_points': '5',
    }
    first = client.post('/api/issues', data=form)
    assert first.status_code == 201, first.text

    second = client.post('/api/issues', data=form)
    assert second.status_code == 200, second.text
    assert second.json()['id'] == first.json()['id']

    issues = client.get('/api/issues')
    assert issues.status_code == 200, issues.text
    assert len(issues.json()) == 1


def test_blocked_reason_sets_blocked_status_and_clearing_resets_to_todo():
    issue = create_issue(blocked_reason='Waiting on dependency')
    assert issue['status'] == 'blocked'

    updated = client.patch(f"/api/issues/{issue['id']}", json={'blocked_reason': None, 'updated_by': 'Jerry'})
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body['blocked_reason'] is None
    assert body['status'] == 'to_do'


def test_story_points_validation_rejects_out_of_range_values():
    response = client.post('/api/issues', json={
        'title': 'Bad estimate',
        'description': 'Too big',
        'created_by': 'Jerry',
        'story_points': 34,
    })
    assert response.status_code == 400
    assert 'story_points must be between 1 and 21' in response.text


def test_upload_image_accepts_real_png_and_rejects_fake_extension():
    issue = create_issue()

    ok = client.post(
        f"/api/issues/{issue['id']}/images?uploaded_by=Jerry",
        files={'file': ('tiny.png', io.BytesIO(PNG_BYTES), 'image/png')},
    )
    assert ok.status_code == 200, ok.text
    image = ok.json()
    assert image['source_type'] == 'issue'
    assert image['uploaded_by'] == 'Jerry'

    bad = client.post(
        f"/api/issues/{issue['id']}/images?uploaded_by=Jerry",
        files={'file': ('fake.png', io.BytesIO(b'not actually an image'), 'image/png')},
    )
    assert bad.status_code == 400
    assert 'Invalid image content' in bad.text


def test_comment_image_requires_matching_comment():
    issue = create_issue()
    response = client.post(
        f"/api/issues/{issue['id']}/images?uploaded_by=Jerry&source_type=comment&comment_id=9999",
        files={'file': ('tiny.png', io.BytesIO(PNG_BYTES), 'image/png')},
    )
    assert response.status_code == 404
    assert 'Comment not found for this issue' in response.text


def test_search_filters_and_issue_number_lookup_work():
    a = create_issue(title='Alpha feature', story_points=3, assigned_to='Jerry')
    b = create_issue(title='Beta review', story_points=8, assigned_to='Aaron')
    client.patch(f"/api/issues/{b['id']}", json={'status': 'in_review', 'updated_by': 'Jerry'})

    by_id = client.get(f"/api/issues/search?q=%23{a['id']}")
    assert by_id.status_code == 200
    assert [item['id'] for item in by_id.json()] == [a['id']]

    filtered = client.get('/api/issues/search?assigned_to=Aaron&needs_review=true&min_story_points=5')
    assert filtered.status_code == 200
    ids = [item['id'] for item in filtered.json()]
    assert ids == [b['id']]


def test_end_sprint_retains_issue_assignment():
    sprint = create_sprint('Historical Sprint')
    issue = create_issue(sprint_id=sprint['id'])

    response = client.post(f"/api/sprints/{sprint['id']}/end")
    assert response.status_code == 200
    assert response.json()['issues_retained'] == 1

    fetched = client.get(f"/api/issues/{issue['id']}")
    assert fetched.status_code == 200
    assert fetched.json()['sprint_id'] == sprint['id']


def test_branch_repo_slug_round_trip_and_activity_logging():
    issue = create_issue(branch='feature/x', repo_slug='aarwitz/lidi-solutions')
    updated = client.patch(
        f"/api/issues/{issue['id']}",
        json={'branch': 'feature/y', 'repo_slug': 'aarwitz/Task-Manager', 'updated_by': 'Jerry'},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body['branch'] == 'feature/y'
    assert body['repo_slug'] == 'aarwitz/Task-Manager'
    fields = [event['field_name'] for event in body['activity_events'] if event['event_type'] == 'field_changed']
    assert 'branch' in fields
    assert 'repo_slug' in fields


def test_issue_detail_response_contains_expected_frontend_fields():
    sprint = create_sprint('UI Sprint')
    issue = create_issue(
        sprint_id=sprint['id'],
        assigned_to='Jerry',
        branch='feature/ui',
        repo_slug='aarwitz/Task-Manager',
        story_points=3,
        blocked_reason='Waiting on review',
        acceptance_criteria='It should render cleanly',
    )
    comment = client.post(
        f"/api/issues/{issue['id']}/comments",
        json={'content': 'Looks good', 'username': 'Aaron'},
    )
    assert comment.status_code == 200

    detail = client.get(f"/api/issues/{issue['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body['id'] == issue['id']
    assert body['assigned_to'] == 'Jerry'
    assert body['sprint_id'] == sprint['id']
    assert body['branch'] == 'feature/ui'
    assert body['repo_slug'] == 'aarwitz/Task-Manager'
    assert body['story_points'] == 3
    assert body['blocked_reason'] == 'Waiting on review'
    assert body['acceptance_criteria'] == 'It should render cleanly'
    assert isinstance(body['comments'], list) and len(body['comments']) == 1
    assert isinstance(body['images'], list)
    assert isinstance(body['activity_events'], list) and len(body['activity_events']) >= 1


def test_lidi_parallel_approvals_execute_side_effect_once():
    draft = client.post('/api/lidi/actions/draft', json={
        'action_type': 'create_issue',
        'title': 'Parallel approval guard',
        'description': 'Should create only one issue',
        'requested_by': 'Aaron',
    })
    assert draft.status_code == 200, draft.text
    action_id = draft.json()['id']

    def approve_once():
        return client.post(f'/api/lidi/actions/{action_id}/approve?actor=Aaron')

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: approve_once(), range(2)))

    normalized = []
    for response in responses:
        assert response.status_code == 200, response.text
        body = response.json()
        if body['status'] == 'executing':
            follow_up = client.post(f'/api/lidi/actions/{action_id}/approve?actor=Aaron')
            assert follow_up.status_code == 200, follow_up.text
            body = follow_up.json()
        assert body['status'] == 'approved'
        normalized.append(body)

    issue_id_values = {body.get('result_issue_id') for body in normalized}
    issue_id_values.discard(None)
    assert len(issue_id_values) == 1

    created = client.get('/api/issues')
    assert created.status_code == 200, created.text
    assert len(created.json()) == 1
    assert created.json()[0]['title'] == 'Parallel approval guard'
