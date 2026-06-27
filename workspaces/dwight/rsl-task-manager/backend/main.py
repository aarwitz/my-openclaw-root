from fastapi import FastAPI, Depends, HTTPException, status, Query, Request, UploadFile, File, Header
from pydantic import ValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi import Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, text as sql_text
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import os
import uuid
import shutil
import re
import imghdr
import hashlib
import hmac
import secrets
import json
import subprocess
import urllib.parse

import models
import schemas
from database import SessionLocal, engine, get_db

# Create database tables
models.Base.metadata.create_all(bind=engine)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}


APPROVER_EMAIL = os.getenv("OWNER_APPROVER_EMAIL", "aaron@lidisolutions.ai").strip().lower()
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "noreply@lidisolutions.ai").strip()
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "LIDI Task Manager").strip()
PASSWORD_RESET_SECRET = os.getenv("PASSWORD_RESET_SECRET", "change-me")
EMAIL_VERIFICATION_SECRET = os.getenv("EMAIL_VERIFICATION_SECRET", "change-me-verify")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-session")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
LIDI_TM_BASE_URL = os.getenv("LIDI_TM_BASE_URL", "https://tm.lidisolutions.ai").strip().rstrip('/')
PUBLIC_SESSION_TTL_HOURS = int(os.getenv("PUBLIC_SESSION_TTL_HOURS", "336"))
EMAIL_VERIFICATION_TTL_HOURS = int(os.getenv("EMAIL_VERIFICATION_TTL_HOURS", "72"))
TASK_MANAGER_INTERNAL_BASE_URL = os.getenv("TASK_MANAGER_INTERNAL_BASE_URL", "http://127.0.0.1:8000").strip().rstrip('/')
DWIGHT_LAUNCH_WRAPPER = os.path.expanduser(os.getenv("DWIGHT_LAUNCH_WRAPPER", "~/.openclaw/scripts/run-with-trace.sh"))
DWIGHT_LAUNCH_SCRIPT = os.path.expanduser(os.getenv("DWIGHT_LAUNCH_SCRIPT", "~/.openclaw/scripts/dwight-launch-from-issue.py"))
DWIGHT_WORKSPACE_ROOT = os.path.expanduser(os.getenv("DWIGHT_WORKSPACE_ROOT", "~/.openclaw/workspaces/dwight"))


def parse_csv_env(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


EWAG_AGENT_USERS = parse_csv_env(
    os.getenv(
        "EWAG_AGENT_USERS",
        "Overseer,Researcher,Quant,Critic,Trader,Executor,Archivist,Developer",
    )
)
EWAG_ALLOWED_REPOS = set(
    parse_csv_env(
        os.getenv(
            "EWAG_ALLOWED_REPOS",
            "aarwitz/EWAG-dev-iosApp,aarwitz/lidi-task-manager,aarwitz/EWAG-androidApp",
        )
    )
)
AUTO_APPROVE_EMAILS = {email.lower() for email in parse_csv_env(
    os.getenv(
        "AUTO_APPROVE_EMAILS",
        "taylor@lidisolutions.ai",
    )
)}
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
GITHUB_WEBHOOK_TRUSTED_REPOS = set(
    parse_csv_env(
        os.getenv(
            "GITHUB_WEBHOOK_TRUSTED_REPOS",
            "EWAG-dev/iosApp,aarwitz/EWAG-dev-iosApp,aarwitz/Task-Manager,aarwitz/lidi-task-manager",
        )
    )
)


def run_safe_migrations():
    """Apply additive SQLite migrations without deleting existing data."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(issues)").fetchall()}
        if "assigned_to" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN assigned_to VARCHAR"))
        if "branch" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN branch VARCHAR"))
        if "repo_slug" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN repo_slug VARCHAR"))
        if "acceptance_criteria" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN acceptance_criteria TEXT"))
        if "updated_at" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN updated_at DATETIME"))
            conn.execute(sql_text("UPDATE issues SET updated_at = created_at WHERE updated_at IS NULL"))
        if "story_points" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN story_points INTEGER"))
        if "blocked_reason" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN blocked_reason TEXT"))
        if "auto_launch_enabled" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN auto_launch_enabled BOOLEAN DEFAULT 0"))
            conn.execute(sql_text("UPDATE issues SET auto_launch_enabled = 0 WHERE auto_launch_enabled IS NULL"))
        if "launch_state" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN launch_state VARCHAR"))
        if "launch_error" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN launch_error TEXT"))
        if "last_launch_at" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN last_launch_at DATETIME"))
        if "launch_signature" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN launch_signature TEXT"))
        if "launch_claim_token" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN launch_claim_token VARCHAR"))
        if "launch_claimed_at" not in columns:
            conn.execute(sql_text("ALTER TABLE issues ADD COLUMN launch_claimed_at DATETIME"))

        sprint_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sprints)").fetchall()}
        if "is_archived" not in sprint_columns:
            conn.execute(sql_text("ALTER TABLE sprints ADD COLUMN is_archived BOOLEAN DEFAULT 0"))
            conn.execute(sql_text("UPDATE sprints SET is_archived = 0 WHERE is_archived IS NULL"))
        if "allowed_users_json" not in sprint_columns:
            conn.execute(sql_text("ALTER TABLE sprints ADD COLUMN allowed_users_json TEXT"))

        image_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(issue_images)").fetchall()}
        if "comment_id" not in image_columns:
            conn.execute(sql_text("ALTER TABLE issue_images ADD COLUMN comment_id INTEGER"))
        if "source_type" not in image_columns:
            conn.execute(sql_text("ALTER TABLE issue_images ADD COLUMN source_type VARCHAR DEFAULT 'issue'"))
        if "uploaded_by" not in image_columns:
            conn.execute(sql_text("ALTER TABLE issue_images ADD COLUMN uploaded_by VARCHAR"))

        table_names = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "issue_activity" not in table_names:
            conn.execute(sql_text("""
                CREATE TABLE issue_activity (
                    id INTEGER PRIMARY KEY,
                    issue_id INTEGER NOT NULL,
                    event_type VARCHAR NOT NULL,
                    field_name VARCHAR,
                    old_value TEXT,
                    new_value TEXT,
                    actor VARCHAR,
                    created_at DATETIME,
                    FOREIGN KEY(issue_id) REFERENCES issues (id)
                )
            """))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_issue_activity_issue_id ON issue_activity (issue_id)"))

        if "public_users" not in table_names:
            conn.execute(sql_text("""
                CREATE TABLE public_users (
                    id INTEGER PRIMARY KEY,
                    email VARCHAR NOT NULL UNIQUE,
                    full_name VARCHAR NOT NULL,
                    company VARCHAR,
                    password_hash VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    is_owner BOOLEAN NOT NULL DEFAULT 0,
                    approved_at DATETIME,
                    approved_by_email VARCHAR,
                    email_verified_at DATETIME,
                    last_login_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_public_users_email ON public_users (email)"))
        public_user_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(public_users)").fetchall()}
        if "email_verified_at" not in public_user_columns:
            conn.execute(sql_text("ALTER TABLE public_users ADD COLUMN email_verified_at DATETIME"))

        if "signup_requests" not in table_names:
            conn.execute(sql_text("""
                CREATE TABLE signup_requests (
                    id INTEGER PRIMARY KEY,
                    email VARCHAR NOT NULL,
                    full_name VARCHAR NOT NULL,
                    company VARCHAR,
                    note TEXT,
                    status_snapshot VARCHAR NOT NULL DEFAULT 'pending',
                    requested_at DATETIME
                )
            """))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_signup_requests_email ON signup_requests (email)"))

        if "password_reset_tokens" not in table_names:
            conn.execute(sql_text("""
                CREATE TABLE password_reset_tokens (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    token_hash VARCHAR NOT NULL,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME,
                    created_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES public_users (id)
                )
            """))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_user_id ON password_reset_tokens (user_id)"))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_password_reset_tokens_token_hash ON password_reset_tokens (token_hash)"))
        if "email_verification_tokens" not in table_names:
            conn.execute(sql_text("""
                CREATE TABLE email_verification_tokens (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    token_hash VARCHAR NOT NULL,
                    expires_at DATETIME NOT NULL,
                    used_at DATETIME,
                    created_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES public_users (id)
                )
            """))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_email_verification_tokens_user_id ON email_verification_tokens (user_id)"))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_email_verification_tokens_token_hash ON email_verification_tokens (token_hash)"))
        if "public_sessions" not in table_names:
            conn.execute(sql_text("""
                CREATE TABLE public_sessions (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    token_hash VARCHAR NOT NULL UNIQUE,
                    expires_at DATETIME NOT NULL,
                    revoked_at DATETIME,
                    last_used_at DATETIME,
                    created_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES public_users (id)
                )
            """))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_public_sessions_user_id ON public_sessions (user_id)"))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_public_sessions_token_hash ON public_sessions (token_hash)"))

        if "lidi_action_requests" not in table_names:
            conn.execute(sql_text("""
                CREATE TABLE lidi_action_requests (
                    id INTEGER PRIMARY KEY,
                    action_type VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    requested_by VARCHAR NOT NULL,
                    approved_by VARCHAR,
                    preview_text TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_issue_id INTEGER,
                    result_comment_id INTEGER,
                    error_message TEXT,
                    created_at DATETIME,
                    expires_at DATETIME NOT NULL,
                    approved_at DATETIME,
                    FOREIGN KEY(result_issue_id) REFERENCES issues (id),
                    FOREIGN KEY(result_comment_id) REFERENCES comments (id)
                )
            """))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_lidi_action_requests_status ON lidi_action_requests (status)"))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_lidi_action_requests_requested_by ON lidi_action_requests (requested_by)"))
            conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_lidi_action_requests_expires_at ON lidi_action_requests (expires_at)"))


run_safe_migrations()


def cleanup_priority_column_if_present():
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(issues)").fetchall()}
        if "priority" not in columns:
            return
        conn.execute(sql_text("""
            CREATE TABLE IF NOT EXISTS issues_new (
                id INTEGER PRIMARY KEY,
                title VARCHAR,
                description TEXT,
                status VARCHAR,
                sprint_id INTEGER,
                created_at DATETIME,
                created_by VARCHAR,
                assigned_to VARCHAR,
                branch VARCHAR,
                acceptance_criteria TEXT,
                updated_at DATETIME,
                story_points INTEGER,
                blocked_reason TEXT,
                repo_slug VARCHAR,
                auto_launch_enabled BOOLEAN DEFAULT 0,
                launch_state VARCHAR,
                launch_error TEXT,
                last_launch_at DATETIME,
                launch_signature TEXT,
                launch_claim_token VARCHAR,
                launch_claimed_at DATETIME,
                FOREIGN KEY(sprint_id) REFERENCES sprints (id)
            )
        """))
        conn.execute(sql_text("""
            INSERT INTO issues_new (id, title, description, status, sprint_id, created_at, created_by, assigned_to, branch, acceptance_criteria, updated_at, story_points, blocked_reason, repo_slug, auto_launch_enabled, launch_state, launch_error, last_launch_at, launch_signature, launch_claim_token, launch_claimed_at)
            SELECT id, title, description, status, sprint_id, created_at, created_by, assigned_to, branch, acceptance_criteria, updated_at, story_points, blocked_reason, repo_slug, auto_launch_enabled, launch_state, launch_error, last_launch_at, launch_signature, launch_claim_token, launch_claimed_at
            FROM issues
        """))
        conn.execute(sql_text("DROP TABLE issues"))
        conn.execute(sql_text("ALTER TABLE issues_new RENAME TO issues"))
        conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_issues_id ON issues (id)"))
        conn.execute(sql_text("CREATE INDEX IF NOT EXISTS ix_issues_title ON issues (title)"))


cleanup_priority_column_if_present()

CANONICAL_TM_USERS = ["Dwight", "Jerry", "Resi", "Druck", "Aaron", "Taylor"]
LOGIN_ALLOWED_USERS = CANONICAL_TM_USERS.copy()
INTERNAL_HUMAN_USERS = ["Aaron", "Taylor"]
EXECUTING_AGENT_USERS = ["Dwight", "Jerry", "Resi", "Druck"]
USERNAME_ALIASES = {
    "claw": "Jerry",
    "aaron": "Aaron",
    "taylor": "Taylor",
}


def canonicalize_username(value: Optional[str]) -> Optional[str]:
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None
    alias_key = normalized.lower()
    if alias_key in USERNAME_ALIASES:
        return USERNAME_ALIASES[alias_key]
    for candidate in CANONICAL_TM_USERS:
        if alias_key == candidate.lower():
            return candidate
    return normalized


def validate_tm_user(value: Optional[str], *, field_name: str, allow_blank: bool = False, allowed_users: Optional[List[str]] = None) -> Optional[str]:
    canonical = canonicalize_username(value)
    if canonical is None:
        if allow_blank:
            return None
        raise HTTPException(status_code=400, detail=f"{field_name} is required")
    permitted = allowed_users or CANONICAL_TM_USERS
    if canonical not in permitted:
        allowed = ", ".join(permitted)
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}. Allowed: {allowed}")
    return canonical


def validate_assignee_identity(db: Session, value: Optional[str], *, field_name: str, allow_blank: bool = False) -> Optional[str]:
    canonical = canonicalize_username(value)
    if canonical is None:
        if allow_blank:
            return None
        raise HTTPException(status_code=400, detail=f"{field_name} is required")

    if canonical in CANONICAL_TM_USERS or canonical in EWAG_AGENT_USERS:
        return canonical

    email_candidate = canonical.lower()
    public_user = db.query(models.PublicUser).filter(models.PublicUser.email == email_candidate).first()
    if public_user and public_user.status == 'approved' and not public_user.is_owner:
        return public_user.email

    allowed = ", ".join(CANONICAL_TM_USERS + EWAG_AGENT_USERS)
    raise HTTPException(
        status_code=400,
        detail=f"Invalid {field_name}. Use one of: {allowed}, or an approved LIDI user email",
    )


def validate_actor_identity(db: Session, value: Optional[str], *, field_name: str, allow_blank: bool = False) -> Optional[str]:
    normalized = normalize_optional_text(value)
    if normalized is None:
        if allow_blank:
            return None
        raise HTTPException(status_code=400, detail=f"{field_name} is required")

    canonical = canonicalize_username(normalized)
    if canonical in CANONICAL_TM_USERS or canonical in EWAG_AGENT_USERS:
        return canonical

    email_candidate = normalized.lower()
    public_user = db.query(models.PublicUser).filter(models.PublicUser.email == email_candidate).first()
    if public_user and public_user.status == 'approved':
        return public_user.email

    allowed = ", ".join(CANONICAL_TM_USERS + EWAG_AGENT_USERS)
    raise HTTPException(
        status_code=400,
        detail=f"Invalid {field_name}. Use one of: {allowed}, or an approved public user email",
    )


def ensure_issue_is_ewag_scoped(issue: models.Issue):
    repo_slug = normalize_optional_text(issue.repo_slug)
    if not repo_slug or repo_slug not in EWAG_ALLOWED_REPOS:
        allowed = ", ".join(sorted(EWAG_ALLOWED_REPOS))
        raise HTTPException(
            status_code=400,
            detail=f"Issue is outside EWAG scope. Allowed repo_slug values: {allowed}",
        )


def rewrite_historical_usernames(db: Session):
    replacements = {
        "Claw": "Jerry",
        "claw": "Jerry",
        "aaron": "Aaron",
        "taylor": "Taylor",
    }
    for old, new in replacements.items():
        if old == new:
            continue
        db.query(models.Issue).filter(models.Issue.created_by == old).update({models.Issue.created_by: new}, synchronize_session=False)
        db.query(models.Issue).filter(models.Issue.assigned_to == old).update({models.Issue.assigned_to: new}, synchronize_session=False)
        db.query(models.Comment).filter(models.Comment.username == old).update({models.Comment.username: new}, synchronize_session=False)
        db.query(models.IssueImage).filter(models.IssueImage.uploaded_by == old).update({models.IssueImage.uploaded_by: new}, synchronize_session=False)
        db.query(models.IssueActivity).filter(models.IssueActivity.actor == old).update({models.IssueActivity.actor: new}, synchronize_session=False)

    existing_users = {user.username: user for user in db.query(models.User).all()}
    keep = set(CANONICAL_TM_USERS)
    for username in keep:
        if username not in existing_users:
            db.add(models.User(username=username, created_at=datetime.now()))

    db.flush()
    for removable in ["telegram", "aaron", "taylor", "Claw", "claw"]:
        user = db.query(models.User).filter(models.User.username == removable).first()
        if user:
            db.delete(user)


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalize_email(value: str) -> str:
    return (value or "").strip().lower()


def parse_allowed_users_json(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    values: List[str] = []
    for item in parsed:
        normalized = normalize_optional_text(str(item)) if item is not None else None
        if normalized:
            values.append(normalized)
    return values


def all_agent_users() -> List[str]:
    ordered: List[str] = []
    for value in [*EXECUTING_AGENT_USERS, *EWAG_AGENT_USERS]:
        if value and value not in ordered:
            ordered.append(value)
    return ordered


def is_admin_viewer(viewer: Optional[str]) -> bool:
    canonical = canonicalize_username(viewer)
    if canonical in INTERNAL_HUMAN_USERS:
        return True
    return normalize_email(viewer or "") == APPROVER_EMAIL


def collect_sprint_working_agents(sprint: models.Sprint) -> List[str]:
    assigned = []
    for issue in sprint.issues or []:
        assignee = normalize_optional_text(issue.assigned_to)
        if assignee and assignee in all_agent_users() and assignee not in assigned:
            assigned.append(assignee)
    return assigned


def serialize_sprint(sprint: models.Sprint) -> schemas.SprintResponse:
    return schemas.SprintResponse(
        id=sprint.id,
        name=sprint.name,
        is_active=bool(sprint.is_active),
        is_archived=bool(getattr(sprint, "is_archived", False)),
        allowed_users=parse_allowed_users_json(getattr(sprint, "allowed_users_json", None)),
        human_members=INTERNAL_HUMAN_USERS.copy(),
        working_agent_members=collect_sprint_working_agents(sprint),
        started_at=sprint.started_at,
        ended_at=sprint.ended_at,
    )


def normalize_status(value: Optional[str]) -> str:
    status_value = (value or "to_do").strip().lower()
    if status_value not in models.STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {', '.join(sorted(models.STATUS_OPTIONS))}")
    return status_value


def verify_github_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        return True
    signature = (signature_header or "").strip()
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_issue_ids_from_pr(head_ref: str, title: str, body: str) -> List[int]:
    text = "\n".join([head_ref or "", title or "", body or ""])
    ids = set()
    for raw in re.findall(r"(?i)issue-(\d+)", text):
        ids.add(int(raw))
    for raw in re.findall(r"(?i)\b(?:fixes|fixed|closes|closed|resolves|resolved|issue)\s*#?(\d+)\b", text):
        ids.add(int(raw))
    return sorted(ids)


def issue_is_ui_related(issue: models.Issue, repo_slug: Optional[str]) -> bool:
    content = " ".join(
        [
            normalize_optional_text(issue.title) or "",
            normalize_optional_text(issue.description) or "",
            normalize_optional_text(issue.acceptance_criteria) or "",
            normalize_optional_text(repo_slug) or "",
        ]
    ).lower()
    ui_hints = [
        "ios",
        "swiftui",
        "ui",
        "screen",
        "view",
        "ux",
        "widget",
        "layout",
    ]
    return any(hint in content for hint in ui_hints)


def evaluate_pr_contract(
    issue: models.Issue,
    *,
    repo_slug: Optional[str],
    head_ref: Optional[str],
    pr_title: Optional[str],
    pr_body: Optional[str],
    pr_url: Optional[str],
) -> Dict[str, Any]:
    missing: List[str] = []
    normalized_repo = normalize_optional_text(repo_slug)
    normalized_head_ref = normalize_optional_text(head_ref)
    normalized_pr_body = normalize_optional_text(pr_body) or ""

    if not normalize_optional_text(issue.acceptance_criteria):
        missing.append("acceptance_criteria_missing")
    if not normalize_optional_text(issue.branch):
        missing.append("issue_branch_missing")
    if normalize_optional_text(issue.branch) and normalized_head_ref and normalize_optional_text(issue.branch) != normalized_head_ref:
        missing.append("branch_mismatch")
    if not normalize_optional_text(issue.repo_slug) and not normalized_repo:
        missing.append("repo_slug_missing")
    if normalize_optional_text(issue.repo_slug) and normalized_repo and normalize_optional_text(issue.repo_slug) != normalized_repo:
        missing.append("repo_slug_mismatch")
    if len(normalized_pr_body) < 40:
        missing.append("pr_description_too_short")
    if not re.search(r"(?i)(\btest(s)?\b|TEST_STATUS=|pytest|xcodebuild|unit test|ui test)", normalized_pr_body):
        missing.append("test_evidence_missing")

    ui_related = issue_is_ui_related(issue, normalized_repo)
    if ui_related and not re.search(r"(?i)(screenshot|before/after|\.png\b|\.jpg\b|\.jpeg\b|\.webp\b|drive\.google\.com)", normalized_pr_body):
        missing.append("ui_evidence_missing")

    passed = len(missing) == 0
    return {
        "passed": passed,
        "missing": missing,
        "ui_related": ui_related,
        "pr_url": normalize_optional_text(pr_url),
        "pr_title": normalize_optional_text(pr_title),
        "head_ref": normalized_head_ref,
        "repo_slug": normalized_repo,
    }


def validate_story_points(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if value < 1 or value > 21:
        raise HTTPException(status_code=400, detail="story_points must be between 1 and 21")
    return value


def parse_issue_create_form(form) -> schemas.IssueCreate:
    payload = {
        "title": form.get("title"),
        "description": form.get("description"),
        "created_by": form.get("created_by"),
        "assigned_to": form.get("assigned_to") or None,
        "acceptance_criteria": form.get("acceptance_criteria") or None,
        "blocked_reason": form.get("blocked_reason") or None,
        "branch": form.get("branch") or None,
        "repo_slug": form.get("repo_slug") or None,
        "story_points": int(form.get("story_points")) if form.get("story_points") not in (None, "") else None,
        "sprint_id": int(form.get("sprint_id")) if form.get("sprint_id") not in (None, "") else None,
        "auto_launch_enabled": str(form.get("auto_launch_enabled") or "").strip().lower() in {"1", "true", "yes", "on"},
    }
    try:
        return schemas.IssueCreate(**payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


APPROVAL_GATE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bapproval\b",
        r"\bapprove\b",
        r"\bowner\b",
        r"\bhuman\b",
        r"\bmanual\b",
        r"\bwaiting\b",
        r"\bexternal\b",
        r"\bsign[\s-]?off\b",
        r"\bblocked by\b",
        r"\bclient\b",
    ]
]
LAUNCH_TERMINAL_STATES = {"launched", "failed"}
LAUNCH_READY_STATES = {"ready", "queued"}


def build_issue_launch_signature(issue: models.Issue) -> str:
    parts = [
        normalize_optional_text(issue.status) or "",
        normalize_optional_text(issue.assigned_to) or "",
        normalize_optional_text(issue.branch) or "",
        normalize_optional_text(issue.repo_slug) or "",
        normalize_optional_text(issue.acceptance_criteria) or "",
        normalize_optional_text(issue.title) or "",
        normalize_optional_text(issue.description) or "",
        "1" if bool(issue.auto_launch_enabled) else "0",
    ]
    return "|".join(parts)


def issue_combined_launch_text(issue: models.Issue) -> str:
    return " ".join(
        value
        for value in [
            normalize_optional_text(issue.title) or "",
            normalize_optional_text(issue.description) or "",
            normalize_optional_text(issue.acceptance_criteria) or "",
            normalize_optional_text(issue.blocked_reason) or "",
        ]
        if value
    )


def launch_readiness(issue: models.Issue) -> tuple[bool, str]:
    assigned_to = normalize_optional_text(issue.assigned_to)
    acceptance = normalize_optional_text(issue.acceptance_criteria)
    branch = normalize_optional_text(issue.branch)
    repo_slug = normalize_optional_text(issue.repo_slug)
    status = normalize_optional_text(issue.status)
    blocked_reason = normalize_optional_text(issue.blocked_reason)
    combined_text = issue_combined_launch_text(issue)

    if not issue.auto_launch_enabled:
        return False, "auto_launch_disabled"
    if assigned_to not in EXECUTING_AGENT_USERS:
        return False, "assigned_to_not_executing_agent"
    if status != "in_progress":
        return False, f"status_{status or 'missing'}"
    if blocked_reason:
        return False, "blocked_reason_present"
    if any(pattern.search(combined_text) for pattern in APPROVAL_GATE_PATTERNS):
        return False, "approval_gated"
    if not branch:
        return False, "branch_missing"
    if not repo_slug:
        return False, "repo_slug_missing"
    if not acceptance:
        return False, "acceptance_missing"
    if not (normalize_optional_text(issue.title) or normalize_optional_text(issue.description)):
        return False, "goal_missing"
    return True, "ready"


def determine_launch_state(issue: models.Issue) -> str:
    if not issue.auto_launch_enabled:
        return "disabled"
    ready, _ = launch_readiness(issue)
    return "ready" if ready else "waiting"


def record_issue_field_change(
    db: Session,
    issue: models.Issue,
    field_name: str,
    new_value: Optional[object],
    *,
    actor: Optional[str],
) -> bool:
    old_value = getattr(issue, field_name)
    if old_value == new_value:
        return False
    setattr(issue, field_name, new_value)
    log_issue_activity(db, issue.id, "field_changed", actor=actor, field_name=field_name, old_value=old_value, new_value=new_value)
    return True


def build_launch_command(issue_id: int, claim_token: str) -> List[str]:
    return [
        DWIGHT_LAUNCH_WRAPPER,
        DWIGHT_LAUNCH_SCRIPT,
        "--issue-id",
        str(issue_id),
        "--execute",
        "--claim-token",
        claim_token,
        "--claim-source",
        "task_manager_auto",
        "--tm-base",
        TASK_MANAGER_INTERNAL_BASE_URL,
    ]


def start_detached_launch(command: List[str]) -> None:
    if not (os.path.isfile(DWIGHT_LAUNCH_WRAPPER) and os.path.isfile(DWIGHT_LAUNCH_SCRIPT)):
        raise RuntimeError("Dwight launch scripts are not available on this runtime")
    subprocess.Popen(
        command,
        cwd=DWIGHT_WORKSPACE_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def recompute_issue_launch_state(db: Session, issue: models.Issue, *, actor: Optional[str]) -> Optional[List[str]]:
    command: Optional[List[str]] = None
    desired_state = determine_launch_state(issue)
    current_signature = build_issue_launch_signature(issue)

    if desired_state == "ready":
        if issue.launch_signature == current_signature and issue.launch_state in {"queued", "launched", "failed"}:
            return None
        issue.launch_signature = current_signature
        record_issue_field_change(db, issue, "launch_error", None, actor=actor)
        issue.launch_claim_token = None
        issue.launch_claimed_at = None
        record_issue_field_change(db, issue, "launch_state", "ready", actor=actor)

        claim_token = secrets.token_urlsafe(24)
        now = datetime.now()
        issue.launch_claim_token = claim_token
        issue.launch_claimed_at = now
        record_issue_field_change(db, issue, "last_launch_at", now, actor=actor)
        record_issue_field_change(db, issue, "launch_state", "queued", actor=actor)
        command = build_launch_command(issue.id, claim_token)
        return command

    record_issue_field_change(db, issue, "launch_state", desired_state, actor=actor)
    issue.launch_signature = None
    issue.launch_claim_token = None
    issue.launch_claimed_at = None
    if desired_state in {"disabled", "waiting"}:
        record_issue_field_change(db, issue, "launch_error", None, actor=actor)
    return None


def issue_has_recent_execution_evidence(issue: models.Issue) -> bool:
    last_launch_at = issue.last_launch_at
    if last_launch_at is None:
        return False
    for event in issue.activity_events or []:
        if event.created_at is None or event.created_at <= last_launch_at:
            continue
        if event.actor and event.actor not in {"Dwight"}:
            return True
        if event.event_type == "comment_added" and event.actor not in {"Dwight"}:
            return True
    return False


def issue_has_pr_evidence(issue: models.Issue) -> bool:
    pr_pattern = re.compile(r"github\.com/.+/pull/\d+|pr_status=opened", re.IGNORECASE)
    for comment in issue.comments or []:
        if pr_pattern.search(comment.content or ""):
            return True
    for event in issue.activity_events or []:
        if pr_pattern.search((event.new_value or "") + " " + (event.old_value or "")):
            return True
    return False


def mark_issue_launch_start_failure(db: Session, issue: models.Issue, error_message: str, *, actor: str = "Dwight") -> None:
    prior_state = issue.launch_state
    prior_error = issue.launch_error
    issue.launch_state = "failed"
    issue.launch_error = normalize_optional_text(error_message) or "Detached launcher start failed"
    issue.launch_claim_token = None
    issue.launch_claimed_at = None
    issue.updated_at = datetime.now()
    if prior_state != issue.launch_state:
        log_issue_activity(db, issue.id, "field_changed", actor=actor, field_name="launch_state", old_value=prior_state, new_value=issue.launch_state)
    if prior_error != issue.launch_error:
        log_issue_activity(db, issue.id, "field_changed", actor=actor, field_name="launch_error", old_value=prior_error, new_value=issue.launch_error)
    db.commit()
    db.refresh(issue)


def log_issue_activity(db: Session, issue_id: int, event_type: str, actor: Optional[str] = None, field_name: Optional[str] = None, old_value: Optional[object] = None, new_value: Optional[object] = None):
    activity = models.IssueActivity(
        issue_id=issue_id,
        event_type=event_type,
        actor=actor,
        field_name=field_name,
        old_value=None if old_value is None else str(old_value),
        new_value=None if new_value is None else str(new_value),
        created_at=datetime.now(),
    )
    db.add(activity)


def resolve_sprint_name(db: Session, sprint_id: Optional[int]) -> str:
    if sprint_id is None:
        return "Backlog"
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    return sprint.name if sprint else f"Sprint {sprint_id}"


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password), password_hash)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(f"{PASSWORD_RESET_SECRET}:{token}".encode('utf-8')).hexdigest()


def hash_email_verification_token(token: str) -> str:
    return hashlib.sha256(f"{EMAIL_VERIFICATION_SECRET}:{token}".encode('utf-8')).hexdigest()


def hash_public_session_token(token: str) -> str:
    return hashlib.sha256(f"{SESSION_SECRET}:{token}".encode('utf-8')).hexdigest()


def is_auto_approved_email(email: str) -> bool:
    return normalize_email(email) in AUTO_APPROVE_EMAILS


def send_resend_email(*, to_recipients: List[str], subject: str, text: str, html: str, reply_to: Optional[str] = None) -> None:
    if not RESEND_API_KEY:
        return
    payload = {
        "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM_ADDRESS}>",
        "to": to_recipients,
        "subject": subject,
        "text": text,
        "html": html,
    }
    if reply_to:
        payload["reply_to"] = reply_to
    import requests
    response = requests.post(
        'https://api.resend.com/emails',
        headers={
            'Authorization': f'Bearer {RESEND_API_KEY}',
            'Content-Type': 'application/json',
        },
        json=payload,
        timeout=20,
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Email provider rejected the request: {response.text or response.reason}")


def ensure_owner_seed(db: Session):
    owner_email = normalize_email(APPROVER_EMAIL)
    owner = db.query(models.PublicUser).filter(models.PublicUser.email == owner_email).first()
    if owner:
        if not owner.is_owner:
            owner.is_owner = True
        if owner.status != 'approved':
            owner.status = 'approved'
        return owner
    seeded = models.PublicUser(
        email=owner_email,
        full_name='Aaron Horowitz',
        company='LIDI Solutions',
        password_hash=hash_password('change-me-now'),
        status='approved',
        is_owner=True,
        approved_at=datetime.now(),
        approved_by_email=owner_email,
    )
    db.add(seeded)
    db.commit()
    db.refresh(seeded)
    return seeded


def mark_public_user_approved(user: models.PublicUser, *, approved_by_email: str) -> None:
    user.status = 'approved'
    if user.approved_at is None:
        user.approved_at = datetime.now()
    if not user.approved_by_email:
        user.approved_by_email = approved_by_email


def create_email_verification_token(db: Session, user: models.PublicUser) -> str:
    db.query(models.EmailVerificationToken).filter(
        models.EmailVerificationToken.user_id == user.id,
        models.EmailVerificationToken.used_at.is_(None),
    ).update(
        {models.EmailVerificationToken.used_at: datetime.now()},
        synchronize_session=False,
    )
    raw_token = secrets.token_urlsafe(32)
    db.add(models.EmailVerificationToken(
        user_id=user.id,
        token_hash=hash_email_verification_token(raw_token),
        expires_at=datetime.now() + timedelta(hours=EMAIL_VERIFICATION_TTL_HOURS),
        created_at=datetime.now(),
    ))
    return raw_token


def email_verification_url(token: str) -> str:
    return f"{LIDI_TM_BASE_URL}/public-auth?verify_token={token}"


def send_verification_email(*, email: str, full_name: str, token: str) -> None:
    verify_url = email_verification_url(token)
    send_resend_email(
        to_recipients=[email],
        subject='Verify your LIDI Task Manager email',
        text=(
            f"Hi {full_name},\n\n"
            "Verify your email to activate your LIDI Task Manager account.\n\n"
            f"Verify here: {verify_url}\n"
        ),
        html=(
            f"<p>Hi {full_name},</p>"
            "<p>Verify your email to activate your LIDI Task Manager account.</p>"
            f"<p><a href=\"{verify_url}\">Verify your email</a></p>"
        ),
    )


def set_public_user_status(user: models.PublicUser, *, status_value: str, actor_email: str) -> None:
    normalized = (status_value or '').strip().lower()
    if normalized not in models.PUBLIC_USER_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid account status")
    user.status = normalized
    if normalized == 'approved':
        if user.approved_at is None:
            user.approved_at = datetime.now()
        if not user.approved_by_email:
            user.approved_by_email = actor_email


def create_public_session(db: Session, user: models.PublicUser) -> str:
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now()
    session = models.PublicSession(
        user_id=user.id,
        token_hash=hash_public_session_token(raw_token),
        expires_at=now + timedelta(hours=PUBLIC_SESSION_TTL_HOURS),
        last_used_at=now,
        created_at=now,
    )
    db.add(session)
    db.commit()
    return raw_token


def get_bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "").strip()
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    return request.headers.get("x-public-session", "").strip() or None


def require_public_session(request: Request, db: Session, *, owner_only: bool = False) -> models.PublicUser:
    token = get_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required")
    token_hash = hash_public_session_token(token)
    session = (
        db.query(models.PublicSession)
        .filter(models.PublicSession.token_hash == token_hash)
        .first()
    )
    if not session or session.revoked_at is not None or session.expires_at < datetime.now():
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    user = db.query(models.PublicUser).filter(models.PublicUser.id == session.user_id).first()
    if not user or user.status != 'approved':
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    is_approver = (user.email or '').strip().lower() == APPROVER_EMAIL
    if owner_only and not is_approver:
        raise HTTPException(status_code=403, detail="Owner access required")
    session.last_used_at = datetime.now()
    db.commit()
    return user


def assignable_usernames(db: Session) -> List[str]:
    approved_public_users = (
        db.query(models.PublicUser)
        .filter(models.PublicUser.status == 'approved', models.PublicUser.is_owner == False)
        .order_by(models.PublicUser.created_at.asc())
        .all()
    )
    usernames = CANONICAL_TM_USERS.copy()
    for user in approved_public_users:
        if user.email not in usernames:
            usernames.append(user.email)
    return usernames


def resolve_lidi_actor(request: Request, db: Session, requested_by: Optional[str]) -> str:
    try:
        session_user = require_public_session(request, db)
        return session_user.email
    except HTTPException:
        return validate_actor_identity(db, requested_by, field_name="requested_by")


def resolve_request_viewer(request: Request, db: Session) -> Optional[str]:
    token = get_bearer_token(request)
    if token:
        try:
            return require_public_session(request, db).email
        except HTTPException:
            return None
    internal_user = normalize_optional_text(request.headers.get("x-tm-user"))
    if internal_user:
        return validate_actor_identity(db, internal_user, field_name="x-tm-user")
    return None


def parse_and_validate_allowed_users(db: Session, values: Optional[List[str]]) -> List[str]:
    deduped: List[str] = []
    for raw_value in values or []:
        normalized = normalize_optional_text(raw_value)
        if not normalized:
            continue
        identity = validate_actor_identity(db, normalized, field_name="allowed_users")
        if identity not in deduped:
            deduped.append(identity)
    return deduped


def viewer_can_access_sprint(viewer: Optional[str], sprint: models.Sprint) -> bool:
    if is_admin_viewer(viewer):
        return True
    allowed_users = parse_allowed_users_json(getattr(sprint, "allowed_users_json", None))
    if not allowed_users:
        return True
    if viewer is None:
        return False
    viewer_lower = viewer.lower()
    return any(viewer_lower == allowed.lower() for allowed in allowed_users)


def require_sprint_access(request: Request, db: Session, sprint: models.Sprint) -> Optional[str]:
    viewer = resolve_request_viewer(request, db)
    if not viewer_can_access_sprint(viewer, sprint):
        raise HTTPException(status_code=403, detail="You do not have access to this sprint")
    return viewer


def parse_lidi_payload(action: models.LidiActionRequest) -> Dict[str, Any]:
    try:
        payload = json.loads(action.payload_json or "{}")
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def cleanup_lidi_action_requests(db: Session) -> None:
    now = datetime.now()
    retention_cutoff = now - timedelta(days=14)
    db.execute(
        sql_text(
            "UPDATE lidi_action_requests "
            "SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at < :now"
        ),
        {"now": now},
    )
    db.execute(
        sql_text(
            "DELETE FROM lidi_action_requests "
            "WHERE status IN ('approved', 'cancelled', 'expired', 'failed') "
            "AND created_at IS NOT NULL AND created_at < :cutoff"
        ),
        {"cutoff": retention_cutoff},
    )
    db.commit()


def lidi_action_response(action: models.LidiActionRequest) -> schemas.LidiActionResponse:
    return schemas.LidiActionResponse(
        id=action.id,
        action_type=action.action_type,
        status=action.status,
        requested_by=action.requested_by,
        approved_by=action.approved_by,
        preview_text=action.preview_text,
        result_issue_id=action.result_issue_id,
        result_comment_id=action.result_comment_id,
        error_message=action.error_message,
        created_at=action.created_at,
        expires_at=action.expires_at,
        approved_at=action.approved_at,
    )


app = FastAPI(title="Task Manager")

# CORS middleware to allow frontend to communicate with backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes

@app.post("/api/public/signup")
def public_signup(payload: schemas.PublicSignupCreate, db: Session = Depends(get_db)):
    ensure_owner_seed(db)
    email = normalize_email(str(payload.email))
    existing = db.query(models.PublicUser).filter(models.PublicUser.email == email).first()
    submitted_name = payload.full_name.strip()
    submitted_company = (payload.company or '').strip() or None
    submitted_note = (payload.note or '').strip() or None
    now = datetime.now()
    auto_approved = is_auto_approved_email(email)
    if existing:
        if existing.status in {'disabled', 'rejected'}:
            raise HTTPException(status_code=403, detail="This account is unavailable. Contact LIDI support for help.")
        if existing.status == 'approved':
            return {
                "ok": True,
                "message": "Your account already exists. Sign in or use forgot password if you need to reset it.",
                "status": existing.status,
            }

        existing.full_name = submitted_name
        existing.company = submitted_company
        existing.password_hash = hash_password(payload.password)
        existing.updated_at = now
        if auto_approved and existing.email_verified_at is None:
            existing.status = 'pending'
        db.add(models.SignupRequest(
            email=email,
            full_name=submitted_name,
            company=submitted_company,
            note=submitted_note,
            status_snapshot='pending_email_verification' if auto_approved else 'pending',
            requested_at=now,
        ))
        verification_token = None
        if auto_approved and existing.email_verified_at is None:
            verification_token = create_email_verification_token(db, existing)
        db.commit()
        if auto_approved and verification_token:
            send_verification_email(email=email, full_name=submitted_name, token=verification_token)
            response = {
                "ok": True,
                "message": "Check your email to verify your address. Once verified, you can sign in immediately.",
                "status": "pending_email_verification",
            }
            if os.getenv('EXPOSE_EMAIL_VERIFICATION_TOKEN_FOR_TESTS', '').strip().lower() in {'1', 'true', 'yes'}:
                response['verification_token'] = verification_token
            return response
        send_resend_email(
            to_recipients=[email],
            subject="We received your LIDI Task Manager access request",
            text=(
                f"Hi {submitted_name},\n\n"
                f"Your LIDI Task Manager account request is in review.\n"
                "LIDI will review and approve your account before you can sign in.\n\n"
                "You will receive another email as soon as access is approved."
            ),
            html=(
                f"<p>Hi {submitted_name},</p>"
                "<p>Your LIDI Task Manager account request is in review.</p>"
                "<p>LIDI will review and approve your account before you can sign in.</p>"
                "<p>You will receive another email as soon as access is approved.</p>"
            ),
        )
        return {
            "ok": True,
            "message": "Request received. LIDI will review your account before you can sign in.",
            "status": "pending",
        }

    user = models.PublicUser(
        email=email,
        full_name=submitted_name,
        company=submitted_company,
        password_hash=hash_password(payload.password),
        status='pending',
        is_owner=False,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.flush()
    db.add(models.SignupRequest(
        email=email,
        full_name=submitted_name,
        company=submitted_company,
        note=submitted_note,
        status_snapshot='pending_email_verification' if auto_approved else 'pending',
        requested_at=now,
    ))
    verification_token = None
    if auto_approved:
        verification_token = create_email_verification_token(db, user)
    db.commit()
    if auto_approved and verification_token:
        send_verification_email(email=email, full_name=submitted_name, token=verification_token)
        response = {
            "ok": True,
            "message": "Check your email to verify your address. Once verified, you can sign in immediately.",
            "status": "pending_email_verification",
        }
        if os.getenv('EXPOSE_EMAIL_VERIFICATION_TOKEN_FOR_TESTS', '').strip().lower() in {'1', 'true', 'yes'}:
            response['verification_token'] = verification_token
        return response
    send_resend_email(
        to_recipients=[email],
        subject="We received your LIDI Task Manager access request",
        text=(
            f"Hi {submitted_name},\n\n"
            "Your LIDI Task Manager account request is in review.\n"
            "LIDI will review and approve your account before you can sign in.\n\n"
            "You will receive another email as soon as access is approved."
        ),
        html=(
            f"<p>Hi {submitted_name},</p>"
            "<p>Your LIDI Task Manager account request is in review.</p>"
            "<p>LIDI will review and approve your account before you can sign in.</p>"
            "<p>You will receive another email as soon as access is approved.</p>"
        ),
    )
    return {
        "ok": True,
        "message": "Request received. LIDI will review your account before you can sign in.",
        "status": "pending",
    }


@app.post("/api/public/verify-email")
def verify_public_email(payload: dict, db: Session = Depends(get_db)):
    token_value = (payload.get('token') or '').strip()
    if not token_value:
        raise HTTPException(status_code=400, detail="Missing verification token")
    token = db.query(models.EmailVerificationToken).filter(
        models.EmailVerificationToken.token_hash == hash_email_verification_token(token_value)
    ).first()
    if not token or token.used_at is not None or token.expires_at < datetime.now():
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    user = db.query(models.PublicUser).filter(models.PublicUser.id == token.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.status in {'disabled', 'rejected'}:
        raise HTTPException(status_code=403, detail="This account is unavailable. Contact LIDI support for help.")
    now = datetime.now()
    user.email_verified_at = now
    if is_auto_approved_email(user.email):
        mark_public_user_approved(user, approved_by_email=APPROVER_EMAIL)
        db.query(models.SignupRequest).filter(
            models.SignupRequest.email == user.email,
            models.SignupRequest.status_snapshot.in_(['pending', 'pending_email_verification']),
        ).update(
            {models.SignupRequest.status_snapshot: 'approved'},
            synchronize_session=False,
        )
    token.used_at = now
    user.updated_at = now
    db.commit()
    return {
        "ok": True,
        "email": user.email,
        "message": "Email verified. You can sign in now.",
    }


@app.post("/api/public/login", response_model=schemas.PublicAuthResponse)
def public_login(payload: schemas.PublicUserLogin, db: Session = Depends(get_db)):
    ensure_owner_seed(db)
    email = normalize_email(str(payload.email))
    user = db.query(models.PublicUser).filter(models.PublicUser.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.status in {'disabled', 'rejected'}:
        raise HTTPException(status_code=403, detail="This account is unavailable. Contact LIDI support for help.")
    if user.status != 'approved' and is_auto_approved_email(user.email) and user.email_verified_at is not None:
        mark_public_user_approved(user, approved_by_email=APPROVER_EMAIL)
    if user.status != 'approved':
        if is_auto_approved_email(user.email):
            raise HTTPException(status_code=403, detail="Check your email to verify your address before signing in.")
        raise HTTPException(status_code=403, detail="Your account is pending LIDI review. You can sign in after approval.")
    user.last_login_at = datetime.now()
    db.commit()
    db.refresh(user)
    session_token = create_public_session(db, user)
    return schemas.PublicAuthResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        company=user.company,
        status=user.status,
        is_owner=user.is_owner,
        approved_at=user.approved_at,
        approved_by_email=user.approved_by_email,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
        session_token=session_token,
    )


@app.get("/api/public/pending-approvals", response_model=List[schemas.PublicUserResponse])
def list_pending_approvals(request: Request, db: Session = Depends(get_db)):
    require_public_session(request, db, owner_only=True)
    return (
        db.query(models.PublicUser)
        .filter(models.PublicUser.status == 'pending', models.PublicUser.is_owner == False, models.PublicUser.email.notin_(list(AUTO_APPROVE_EMAILS)))
        .order_by(models.PublicUser.created_at.asc())
        .all()
    )


@app.get("/api/public/admin/users", response_model=List[schemas.PublicUserResponse])
def list_public_users_for_admin(request: Request, db: Session = Depends(get_db)):
    require_public_session(request, db, owner_only=True)
    return (
        db.query(models.PublicUser)
        .filter(models.PublicUser.is_owner == False)
        .order_by(models.PublicUser.created_at.asc(), models.PublicUser.id.asc())
        .all()
    )


@app.post("/api/public/users/{user_id}/approve", response_model=schemas.PublicUserResponse)
def approve_public_user(user_id: int, action: schemas.ApprovalAction, request: Request, db: Session = Depends(get_db)):
    approver = require_public_session(request, db, owner_only=True)
    approver_email = approver.email
    user = db.query(models.PublicUser).filter(models.PublicUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.status in {'disabled', 'rejected'}:
        raise HTTPException(status_code=403, detail="This account is unavailable. Contact LIDI support for help.")
    mark_public_user_approved(user, approved_by_email=approver_email)
    db.query(models.SignupRequest).filter(
        models.SignupRequest.email == user.email,
        models.SignupRequest.status_snapshot == 'pending',
    ).update(
        {models.SignupRequest.status_snapshot: 'approved'},
        synchronize_session=False,
    )
    db.commit()
    db.refresh(user)
    send_resend_email(
        to_recipients=[user.email],
        subject='Your LIDI Task Manager account has been approved',
        text=(
            f"Hi {user.full_name},\n\n"
            f"Your LIDI Task Manager account has been approved. You can now sign in at {LIDI_TM_BASE_URL}.\n"
        ),
        html=(
            f"<p>Hi {user.full_name},</p>"
            f"<p>Your LIDI Task Manager account has been approved.</p>"
            f"<p>You can now sign in at <a href=\"{LIDI_TM_BASE_URL}\">{LIDI_TM_BASE_URL}</a>.</p>"
        ),
    )
    return user


@app.post("/api/public/users/{user_id}/status", response_model=schemas.PublicUserResponse)
def update_public_user_status(
    user_id: int,
    payload: schemas.PublicUserStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    approver = require_public_session(request, db, owner_only=True)
    user = db.query(models.PublicUser).filter(models.PublicUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_owner:
        raise HTTPException(status_code=403, detail="Owner account cannot be changed here")
    set_public_user_status(user, status_value=payload.status, actor_email=approver.email)
    user.updated_at = datetime.now()
    if user.status == 'approved':
        db.query(models.SignupRequest).filter(
            models.SignupRequest.email == user.email,
            models.SignupRequest.status_snapshot == 'pending',
        ).update(
            {models.SignupRequest.status_snapshot: 'approved'},
            synchronize_session=False,
        )
    db.commit()
    db.refresh(user)
    return user


@app.delete("/api/public/users/{user_id}")
def delete_public_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    approver = require_public_session(request, db, owner_only=True)
    user = db.query(models.PublicUser).filter(models.PublicUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_owner:
        raise HTTPException(status_code=403, detail="Owner account cannot be deleted here")

    user_email = user.email
    db.query(models.PublicSession).filter(models.PublicSession.user_id == user.id).delete(synchronize_session=False)
    db.query(models.PasswordResetToken).filter(models.PasswordResetToken.user_id == user.id).delete(synchronize_session=False)
    db.query(models.EmailVerificationToken).filter(models.EmailVerificationToken.user_id == user.id).delete(synchronize_session=False)
    db.query(models.SignupRequest).filter(models.SignupRequest.email == user_email).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    return {"ok": True, "deleted_email": user_email, "deleted_by": approver.email}


@app.post("/api/public/forgot-password")
def forgot_password(payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    user = db.query(models.PublicUser).filter(models.PublicUser.email == email).first()
    if not user or user.status != 'approved':
        return {"ok": True}
    raw_token = secrets.token_urlsafe(32)
    db.add(models.PasswordResetToken(
        user_id=user.id,
        token_hash=hash_reset_token(raw_token),
        expires_at=datetime.now() + timedelta(hours=1),
    ))
    db.commit()
    reset_url = f"{LIDI_TM_BASE_URL}/public-auth.html?reset_token={raw_token}"
    send_resend_email(
        to_recipients=[user.email],
        subject='Reset your LIDI Task Manager password',
        text=(
            f"We received a request to reset your password.\n\n"
            f"Reset it here: {reset_url}\n\n"
            f"If you did not request this, you can ignore this email."
        ),
        html=(
            f"<p>We received a request to reset your password.</p>"
            f"<p><a href=\"{reset_url}\">Reset your password</a></p>"
            f"<p>If you did not request this, you can ignore this email.</p>"
        ),
    )
    response = {"ok": True}
    if os.getenv('EXPOSE_RESET_TOKEN_FOR_TESTS', '').strip().lower() in {'1', 'true', 'yes'}:
        response['reset_token'] = raw_token
    return response


@app.post("/api/public/reset-password")
def reset_password(payload: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    token_hash = hash_reset_token(payload.token)
    token = db.query(models.PasswordResetToken).filter(models.PasswordResetToken.token_hash == token_hash).first()
    if not token or token.used_at is not None or token.expires_at < datetime.now():
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user = db.query(models.PublicUser).filter(models.PublicUser.id == token.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(payload.new_password)
    token.used_at = datetime.now()
    db.commit()
    send_resend_email(
        to_recipients=[user.email],
        subject='Your LIDI Task Manager password was changed',
        text=(
            "Your LIDI Task Manager password has been changed successfully.\n\n"
            "If you did not make this change, reset your password again immediately and contact LIDI support."
        ),
        html=(
            "<p>Your LIDI Task Manager password has been changed successfully.</p>"
            "<p>If you did not make this change, reset your password again immediately and contact LIDI support.</p>"
        ),
    )
    return {"ok": True}


@app.post("/api/users/login", response_model=schemas.UserResponse)
def login(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """Login approved Task Manager developers only, while preserving canonical user identities."""
    username = validate_tm_user(user.username, field_name="username", allowed_users=LOGIN_ALLOWED_USERS)
    rewrite_historical_usernames(db)
    db_user = db.query(models.User).filter(models.User.username == username).first()
    if not db_user:
        db_user = models.User(username=username)
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    else:
        db.commit()
    return db_user

@app.get("/api/users/current")
def get_current_user(username: str, db: Session = Depends(get_db)):
    """Get current user info"""
    username = validate_tm_user(username, field_name="username")
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@app.get("/api/users", response_model=List[schemas.UserResponse])
def list_users(db: Session = Depends(get_db)):
    """List canonical Task Manager users plus approved public collaborators."""
    rewrite_historical_usernames(db)
    db.commit()
    canonical_users = {
        user.username: user
        for user in db.query(models.User).filter(models.User.username.in_(CANONICAL_TM_USERS)).all()
    }
    ordered = []
    for username in CANONICAL_TM_USERS:
        user = canonical_users.get(username)
        if user:
            ordered.append(user)

    approved_public_users = (
        db.query(models.PublicUser)
        .filter(models.PublicUser.status == 'approved', models.PublicUser.is_owner == False)
        .order_by(models.PublicUser.created_at.asc())
        .all()
    )
    synthetic_users = [
        {
            "id": -user.id,
            "username": user.email,
            "created_at": user.created_at,
        }
        for user in approved_public_users
    ]
    ewag_agent_users = [
        {
            "id": -(100000 + idx),
            "username": username,
            "created_at": datetime.now(),
        }
        for idx, username in enumerate(EWAG_AGENT_USERS, start=1)
    ]
    return ordered + ewag_agent_users + synthetic_users


@app.get("/api/ewag/config")
def get_ewag_config():
    """Expose EWAG automation boundaries for worker scripts and UI integrations."""
    return {
        "agent_users": EWAG_AGENT_USERS,
        "allowed_repos": sorted(EWAG_ALLOWED_REPOS),
        "scope": "ewag-only",
    }


@app.get("/api/ewag/issues", response_model=List[schemas.IssueResponse])
def get_ewag_issues(
    status_filter: Optional[str] = Query(None, alias="status"),
    assigned_to: Optional[str] = None,
    sprint_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Return issues restricted to EWAG-scoped repositories only."""
    query = db.query(models.Issue).filter(models.Issue.repo_slug.in_(EWAG_ALLOWED_REPOS))

    if status_filter:
        query = query.filter(models.Issue.status == normalize_status(status_filter))

    if assigned_to:
        query = query.filter(models.Issue.assigned_to == validate_assignee_identity(db, assigned_to, field_name="assigned_to"))

    if sprint_id is not None:
        query = query.filter(models.Issue.sprint_id == sprint_id)

    return query.order_by(models.Issue.updated_at.desc(), models.Issue.created_at.desc()).all()


@app.post("/api/ewag/issues/{issue_id}/claim", response_model=schemas.IssueResponse)
def claim_ewag_issue(issue_id: int, assigned_to: str = Query(...), db: Session = Depends(get_db)):
    """Assign an EWAG-scoped issue to an EWAG agent user."""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    ensure_issue_is_ewag_scoped(issue)
    assignee = validate_assignee_identity(db, assigned_to, field_name="assigned_to")
    if assignee not in EWAG_AGENT_USERS:
        raise HTTPException(status_code=400, detail="EWAG claims must assign to an EWAG agent user")

    if issue.assigned_to != assignee:
        old_value = issue.assigned_to
        issue.assigned_to = assignee
        issue.updated_at = datetime.now()
        log_issue_activity(db, issue.id, "field_changed", actor=assignee, field_name="assigned_to", old_value=old_value, new_value=assignee)
        db.commit()
        db.refresh(issue)

    return issue


@app.post("/api/ewag/issues/{issue_id}/agent-comment", response_model=schemas.CommentResponse)
def add_ewag_agent_comment(issue_id: int, comment: schemas.CommentCreate, db: Session = Depends(get_db)):
    """Post a progress update as an EWAG agent on an EWAG-scoped issue."""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    ensure_issue_is_ewag_scoped(issue)
    actor = validate_actor_identity(db, comment.username, field_name="username")
    if actor not in EWAG_AGENT_USERS:
        raise HTTPException(status_code=400, detail="Only EWAG agent users can use this endpoint")

    db_comment = models.Comment(content=comment.content, username=actor, issue_id=issue_id)
    db.add(db_comment)
    db.flush()
    log_issue_activity(db, issue_id, "comment_added", actor=actor, new_value=comment.content[:120])
    issue.updated_at = datetime.now()
    db.commit()
    db.refresh(db_comment)
    return db_comment


@app.post("/api/lidi/actions/draft", response_model=schemas.LidiActionResponse)
def create_lidi_action_draft(payload: schemas.LidiActionDraftCreate, request: Request, db: Session = Depends(get_db)):
    """Create an approval-gated Lidi action draft (no side effects yet)."""
    cleanup_lidi_action_requests(db)
    requester = resolve_lidi_actor(request, db, payload.requested_by)
    action_type = normalize_optional_text(payload.action_type)
    if action_type not in {"create_issue", "comment_issue"}:
        raise HTTPException(status_code=400, detail="Unsupported action_type")

    now = datetime.now()
    expires_at = now + timedelta(hours=2)

    if action_type == "create_issue":
        title = normalize_optional_text(payload.title)
        if not title:
            raise HTTPException(status_code=400, detail="title is required for create_issue")

        draft_assignee = validate_assignee_identity(db, payload.assigned_to, field_name="assigned_to", allow_blank=True)
        draft_repo = normalize_optional_text(payload.repo_slug)
        if draft_repo and draft_repo in EWAG_ALLOWED_REPOS and draft_assignee and draft_assignee not in EWAG_AGENT_USERS:
            raise HTTPException(status_code=400, detail="EWAG issues must be assigned to an EWAG agent user")

        draft_payload = {
            "title": title,
            "description": payload.description or "",
            "assigned_to": draft_assignee,
            "sprint_id": payload.sprint_id,
            "repo_slug": draft_repo,
            "blocked_reason": normalize_optional_text(payload.blocked_reason),
            "source_prompt": payload.source_prompt or "",
        }
        preview = f"Create issue: {title}"
        if draft_assignee:
            preview += f" (assign to {draft_assignee})"
        if draft_repo:
            preview += f" [{draft_repo}]"
    else:
        issue_id = payload.issue_id
        if not issue_id:
            raise HTTPException(status_code=400, detail="issue_id is required for comment_issue")
        issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")

        comment_content = normalize_optional_text(payload.comment_content)
        if not comment_content:
            raise HTTPException(status_code=400, detail="comment_content is required for comment_issue")

        draft_payload = {
            "issue_id": issue_id,
            "comment_content": comment_content,
            "source_prompt": payload.source_prompt or "",
        }
        preview = f"Comment on issue #{issue_id}: {comment_content[:120]}"

    action = models.LidiActionRequest(
        action_type=action_type,
        status="pending",
        requested_by=requester,
        preview_text=preview,
        payload_json=json.dumps(draft_payload, separators=(",", ":")),
        created_at=now,
        expires_at=expires_at,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return lidi_action_response(action)


@app.post("/api/lidi/actions/{action_id}/approve", response_model=schemas.LidiActionResponse)
def approve_lidi_action(action_id: int, request: Request, actor: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Approve and execute a pending Lidi action exactly once."""
    approver = resolve_lidi_actor(request, db, actor)
    action = db.query(models.LidiActionRequest).filter(models.LidiActionRequest.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    if approver != action.requested_by and approver != APPROVER_EMAIL:
        raise HTTPException(status_code=403, detail="Only requester or owner can approve this action")

    now = datetime.now()
    if action.expires_at and action.expires_at < now and action.status == "pending":
        action.status = "expired"
        db.commit()
        db.refresh(action)
        return lidi_action_response(action)

    if action.status in {"approved", "cancelled", "expired", "failed"}:
        return lidi_action_response(action)

    updated = db.execute(
        sql_text(
            "UPDATE lidi_action_requests "
            "SET status = 'executing', approved_by = :approved_by, approved_at = :approved_at "
            "WHERE id = :action_id AND status = 'pending'"
        ),
        {
            "approved_by": approver,
            "approved_at": now,
            "action_id": action_id,
        },
    )
    db.commit()

    if getattr(updated, "rowcount", 0) == 0:
        action = db.query(models.LidiActionRequest).filter(models.LidiActionRequest.id == action_id).first()
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")
        return lidi_action_response(action)

    action = db.query(models.LidiActionRequest).filter(models.LidiActionRequest.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    payload = parse_lidi_payload(action)
    try:
        if action.action_type == "create_issue":
            target_sprint_id = payload.get("sprint_id")
            if target_sprint_id is None:
                active_sprint = db.query(models.Sprint).filter(models.Sprint.is_active == True).first()
                if active_sprint:
                    target_sprint_id = active_sprint.id
            else:
                sprint = db.query(models.Sprint).filter(models.Sprint.id == target_sprint_id).first()
                if not sprint:
                    raise HTTPException(status_code=404, detail="Sprint not found")

            issue = models.Issue(
                title=payload.get("title") or "Untitled",
                description=payload.get("description") or "",
                created_by=action.requested_by,
                assigned_to=payload.get("assigned_to"),
                sprint_id=target_sprint_id,
                repo_slug=payload.get("repo_slug"),
                blocked_reason=payload.get("blocked_reason"),
                status="blocked" if payload.get("blocked_reason") else "to_do",
                updated_at=datetime.now(),
            )
            db.add(issue)
            db.flush()
            log_issue_activity(db, issue.id, "created", actor=action.requested_by, new_value=issue.title)
            action.result_issue_id = issue.id
        elif action.action_type == "comment_issue":
            issue_id = int(payload.get("issue_id"))
            issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
            if not issue:
                raise HTTPException(status_code=404, detail="Issue not found")

            comment = models.Comment(
                content=payload.get("comment_content") or "",
                username=action.requested_by,
                issue_id=issue_id,
            )
            db.add(comment)
            db.flush()
            issue.updated_at = datetime.now()
            log_issue_activity(db, issue_id, "comment_added", actor=action.requested_by, new_value=(comment.content or "")[:120])
            action.result_issue_id = issue_id
            action.result_comment_id = comment.id
        else:
            raise HTTPException(status_code=400, detail="Unsupported action type")

        action.status = "approved"
        action.error_message = None
        db.commit()
    except HTTPException as exc:
        action.status = "failed"
        action.error_message = str(exc.detail)
        db.commit()
    except Exception as exc:
        action.status = "failed"
        action.error_message = str(exc)
        db.commit()

    db.refresh(action)
    return lidi_action_response(action)


@app.post("/api/lidi/actions/{action_id}/cancel", response_model=schemas.LidiActionResponse)
def cancel_lidi_action(action_id: int, request: Request, actor: Optional[str] = Query(None), db: Session = Depends(get_db)):
    """Cancel a pending Lidi action draft."""
    actor = resolve_lidi_actor(request, db, actor)
    action = db.query(models.LidiActionRequest).filter(models.LidiActionRequest.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    if actor != action.requested_by and actor != APPROVER_EMAIL:
        raise HTTPException(status_code=403, detail="Only requester or owner can cancel this action")

    if action.status == "pending":
        action.status = "cancelled"
        action.approved_by = actor
        action.approved_at = datetime.now()
        db.commit()
        db.refresh(action)
    return lidi_action_response(action)

ISSUE_CREATE_DEDUP_WINDOW_SECONDS = int(os.getenv("ISSUE_CREATE_DEDUP_WINDOW_SECONDS", "120"))


def find_recent_duplicate_issue(
    db: Session,
    *,
    title: str,
    description: str,
    created_by: str,
    assigned_to: Optional[str],
    sprint_id: Optional[int],
    acceptance_criteria: Optional[str],
    branch: Optional[str],
    repo_slug: Optional[str],
    story_points: Optional[int],
    blocked_reason: Optional[str],
    auto_launch_enabled: bool,
) -> Optional[models.Issue]:
    """Return a recent issue that matches the create payload exactly.

    This is a narrow idempotency guard for accidental duplicate submissions
    caused by double-clicks or request retries. It intentionally uses an exact
    normalized payload match plus a short time window to avoid swallowing
    legitimately distinct work items with similar titles.
    """
    cutoff = datetime.now() - timedelta(seconds=ISSUE_CREATE_DEDUP_WINDOW_SECONDS)
    query = db.query(models.Issue).filter(
        models.Issue.created_at >= cutoff,
        models.Issue.title == title,
        models.Issue.description == description,
        models.Issue.created_by == created_by,
        models.Issue.assigned_to.is_(None) if assigned_to is None else models.Issue.assigned_to == assigned_to,
        models.Issue.sprint_id.is_(None) if sprint_id is None else models.Issue.sprint_id == sprint_id,
        models.Issue.acceptance_criteria.is_(None) if acceptance_criteria is None else models.Issue.acceptance_criteria == acceptance_criteria,
        models.Issue.branch.is_(None) if branch is None else models.Issue.branch == branch,
        models.Issue.repo_slug.is_(None) if repo_slug is None else models.Issue.repo_slug == repo_slug,
        models.Issue.story_points.is_(None) if story_points is None else models.Issue.story_points == story_points,
        models.Issue.blocked_reason.is_(None) if blocked_reason is None else models.Issue.blocked_reason == blocked_reason,
        models.Issue.auto_launch_enabled == bool(auto_launch_enabled),
    )
    return query.order_by(models.Issue.created_at.desc()).first()


@app.post("/api/issues", response_model=schemas.IssueResponse, status_code=status.HTTP_201_CREATED)
async def create_issue(request: Request, response: Response, db: Session = Depends(get_db)):
    """Create a new issue"""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        issue = schemas.IssueCreate(**(await request.json()))
    elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        issue = parse_issue_create_form(await request.form())
    else:
        raise HTTPException(status_code=415, detail="Unsupported content type")

    target_sprint_id = issue.sprint_id
    target_sprint: Optional[models.Sprint] = None
    if target_sprint_id is None:
        active_sprint = db.query(models.Sprint).filter(models.Sprint.is_active == True).first()
        if active_sprint:
            require_sprint_access(request, db, active_sprint)
            target_sprint = active_sprint
            target_sprint_id = active_sprint.id
    else:
        target_sprint = db.query(models.Sprint).filter(models.Sprint.id == target_sprint_id).first()
        if not target_sprint:
            raise HTTPException(status_code=404, detail="Sprint not found")
        require_sprint_access(request, db, target_sprint)

    created_by = validate_actor_identity(db, issue.created_by, field_name="created_by")
    assigned_to = validate_assignee_identity(db, issue.assigned_to, field_name="assigned_to", allow_blank=True)

    acceptance_criteria = normalize_optional_text(issue.acceptance_criteria)
    branch = normalize_optional_text(issue.branch)
    repo_slug = normalize_optional_text(issue.repo_slug)
    story_points = validate_story_points(issue.story_points)
    blocked_reason = normalize_optional_text(issue.blocked_reason)
    auto_launch_enabled = bool(issue.auto_launch_enabled)
    issue_status = "blocked" if blocked_reason else "to_do"

    duplicate = find_recent_duplicate_issue(
        db,
        title=issue.title,
        description=issue.description,
        created_by=created_by,
        assigned_to=assigned_to,
        sprint_id=target_sprint_id,
        acceptance_criteria=acceptance_criteria,
        branch=branch,
        repo_slug=repo_slug,
        story_points=story_points,
        blocked_reason=blocked_reason,
        auto_launch_enabled=auto_launch_enabled,
    )
    if duplicate:
        response.status_code = status.HTTP_200_OK
        return duplicate

    db_issue = models.Issue(
        title=issue.title,
        description=issue.description,
        acceptance_criteria=acceptance_criteria,
        created_by=created_by,
        assigned_to=assigned_to,
        sprint_id=target_sprint_id,
        branch=branch,
        repo_slug=repo_slug,
        story_points=story_points,
        blocked_reason=blocked_reason,
        auto_launch_enabled=auto_launch_enabled,
        status=issue_status,
        updated_at=datetime.now(),
    )
    db.add(db_issue)
    db.flush()
    log_issue_activity(db, db_issue.id, "created", actor=created_by, new_value=db_issue.title)
    launch_command = recompute_issue_launch_state(db, db_issue, actor=created_by)
    db.commit()
    db.refresh(db_issue)
    if launch_command:
        try:
            start_detached_launch(launch_command)
        except Exception as exc:
            mark_issue_launch_start_failure(db, db_issue, str(exc))
    return db_issue

@app.get("/api/issues", response_model=List[schemas.IssueResponse])
def get_issues(request: Request, sprint_id: int = None, in_backlog: bool = False, db: Session = Depends(get_db)):
    """Get all issues, optionally filtered by sprint or backlog"""
    query = db.query(models.Issue)
    
    if in_backlog:
        query = query.filter(models.Issue.sprint_id == None)
    elif sprint_id is not None:
        sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
        if not sprint:
            raise HTTPException(status_code=404, detail="Sprint not found")
        require_sprint_access(request, db, sprint)
        query = query.filter(models.Issue.sprint_id == sprint_id)
    issues = query.order_by(models.Issue.created_at.desc()).all()
    viewer = resolve_request_viewer(request, db)
    return [
        issue for issue in issues
        if issue.sprint_id is None or viewer_can_access_sprint(viewer, issue.sprint)
    ]

@app.get("/api/issues/search", response_model=List[schemas.IssueResponse])
def search_issues(
    request: Request,
    q: str = "",
    search_in: str = Query("all", description="Where to search: all, title, description, comments"),
    status_filter: Optional[str] = Query(None, alias="status"),
    sprint_id: Optional[int] = None,
    created_by: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    assigned_to: Optional[str] = None,
    min_story_points: Optional[int] = None,
    max_story_points: Optional[int] = None,
    blocked_only: bool = False,
    needs_review: bool = False,
    stale_days: Optional[int] = None,
    in_backlog: bool = False,
    operator_view: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Search issues with filters across title, description, comments, or exact issue ID."""
    query = db.query(models.Issue)

    # --- search term handling ---
    normalized_q = q.strip()
    issue_id_query: Optional[int] = None
    if normalized_q:
        id_candidate = normalized_q[1:] if normalized_q.startswith("#") else normalized_q
        if id_candidate.isdigit():
            issue_id_query = int(id_candidate)

    # --- exact issue number search ---
    if issue_id_query is not None:
        query = query.filter(models.Issue.id == issue_id_query)

    # --- text search ---
    elif normalized_q:
        term = f"%{normalized_q}%"
        if search_in == "title":
            query = query.filter(models.Issue.title.ilike(term))
        elif search_in == "description":
            query = query.filter(models.Issue.description.ilike(term))
        elif search_in == "comments":
            query = query.join(models.Comment).filter(models.Comment.content.ilike(term))
        else:  # "all"
            query = query.outerjoin(models.Comment).filter(
                or_(
                    models.Issue.title.ilike(term),
                    models.Issue.description.ilike(term),
                    models.Comment.content.ilike(term),
                )
            )
            # deduplicate after outer join
            query = query.distinct()

    # --- filters ---
    if status_filter:
        query = query.filter(models.Issue.status == status_filter)

    if sprint_id is not None:
        query = query.filter(models.Issue.sprint_id == sprint_id)

    if in_backlog:
        query = query.filter(models.Issue.sprint_id == None)

    if created_by:
        query = query.filter(models.Issue.created_by == validate_actor_identity(db, created_by, field_name="created_by"))

    if assigned_to:
        query = query.filter(models.Issue.assigned_to == validate_assignee_identity(db, assigned_to, field_name="assigned_to"))

    if min_story_points is not None:
        query = query.filter(models.Issue.story_points >= min_story_points)

    if max_story_points is not None:
        query = query.filter(models.Issue.story_points <= max_story_points)

    if blocked_only:
        query = query.filter(or_(models.Issue.status == "blocked", models.Issue.blocked_reason.isnot(None)))

    if needs_review:
        query = query.filter(models.Issue.status == "in_review")

    if stale_days is not None and stale_days >= 0:
        cutoff = datetime.now().timestamp() - (stale_days * 86400)
        query = query.filter(models.Issue.updated_at.is_not(None))
        query = query.filter(models.Issue.updated_at <= datetime.fromtimestamp(cutoff))

    if date_from:
        try:
            dt = datetime.fromisoformat(date_from)
            query = query.filter(models.Issue.created_at >= dt)
        except ValueError:
            pass

    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            query = query.filter(models.Issue.created_at <= dt)
        except ValueError:
            pass

    issues = query.order_by(models.Issue.created_at.desc()).all()
    viewer = resolve_request_viewer(request, db)
    visible_issues = [
        issue for issue in issues
        if issue.sprint_id is None or viewer_can_access_sprint(viewer, issue.sprint)
    ]
    if operator_view == "ready_not_queued":
        return [issue for issue in visible_issues if bool(issue.auto_launch_enabled) and issue.launch_state == "ready"]
    if operator_view == "active_launch_without_recent_evidence":
        return [
            issue for issue in visible_issues
            if issue.launch_state in {"queued", "launched"} and not issue_has_recent_execution_evidence(issue)
        ]
    if operator_view == "in_progress_no_pr":
        return [
            issue for issue in visible_issues
            if issue.status == "in_progress" and normalize_optional_text(issue.branch) and not issue_has_pr_evidence(issue)
        ]
    return visible_issues


@app.get("/api/issues/{issue_id}", response_model=schemas.IssueResponse)
def get_issue(issue_id: int, request: Request, db: Session = Depends(get_db)):
    """Get a specific issue by ID"""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.sprint_id is not None and issue.sprint is not None:
        require_sprint_access(request, db, issue.sprint)
    return issue

@app.patch("/api/issues/{issue_id}", response_model=schemas.IssueResponse)
def update_issue(issue_id: int, issue_update: schemas.IssueUpdate, request: Request, db: Session = Depends(get_db)):
    """Update an issue"""
    db_issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not db_issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if db_issue.sprint_id is not None and db_issue.sprint is not None:
        require_sprint_access(request, db, db_issue.sprint)

    update_data = issue_update.dict(exclude_unset=True)
    actor = validate_actor_identity(db, update_data.pop("updated_by", None), field_name="updated_by", allow_blank=True)

    normalized_updates = {}
    for field, value in update_data.items():
        if field in {"branch", "repo_slug", "acceptance_criteria", "blocked_reason"}:
            value = normalize_optional_text(value)
        elif field == "assigned_to":
            value = validate_assignee_identity(db, value, field_name="assigned_to", allow_blank=True)
        elif field == "status":
            value = normalize_status(value)
        elif field == "auto_launch_enabled":
            value = bool(value)
        if field == "story_points":
            value = validate_story_points(value)
        normalized_updates[field] = value

    if "sprint_id" in normalized_updates and normalized_updates["sprint_id"] is not None:
        sprint = db.query(models.Sprint).filter(models.Sprint.id == normalized_updates["sprint_id"]).first()
        if not sprint:
            raise HTTPException(status_code=404, detail="Sprint not found")
        require_sprint_access(request, db, sprint)

    if normalized_updates.get("blocked_reason") and "status" not in normalized_updates:
        normalized_updates["status"] = "blocked"
    elif "blocked_reason" in normalized_updates and not normalized_updates.get("blocked_reason") and db_issue.status == "blocked" and "status" not in normalized_updates:
        normalized_updates["status"] = "to_do"

    changed = False
    for field, value in normalized_updates.items():
        old_value = getattr(db_issue, field)
        if old_value != value:
            changed = True
            setattr(db_issue, field, value)
            if field == "sprint_id":
                log_issue_activity(db, issue_id, "field_changed", actor=actor, field_name=field, old_value=resolve_sprint_name(db, old_value), new_value=resolve_sprint_name(db, value))
            else:
                log_issue_activity(db, issue_id, "field_changed", actor=actor, field_name=field, old_value=old_value, new_value=value)

    if changed:
        db_issue.updated_at = datetime.now()
    launch_command = recompute_issue_launch_state(db, db_issue, actor=actor)

    db.commit()
    db.refresh(db_issue)
    if launch_command:
        try:
            start_detached_launch(launch_command)
        except Exception as exc:
            mark_issue_launch_start_failure(db, db_issue, str(exc))
    return db_issue


@app.post("/api/issues/{issue_id}/launch-claim", response_model=schemas.IssueLaunchClaimResponse)
def claim_issue_launch(issue_id: int, payload: schemas.IssueLaunchClaimCreate, db: Session = Depends(get_db)):
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    claimant = validate_actor_identity(db, payload.claimant, field_name="claimant")
    expected_signature = normalize_optional_text(payload.expected_signature)
    current_signature = build_issue_launch_signature(issue)
    if not expected_signature or expected_signature != current_signature:
        raise HTTPException(status_code=409, detail="Launch signature no longer matches the current issue state")

    ready, reason = launch_readiness(issue)
    if not ready and issue.launch_state not in {"queued", "launched"}:
        raise HTTPException(status_code=409, detail=f"Issue is not launch-ready: {reason}")

    if issue.launch_claim_token and issue.launch_signature == expected_signature:
        return schemas.IssueLaunchClaimResponse(
            issue_id=issue.id,
            claim_token=issue.launch_claim_token,
            launch_state=issue.launch_state or "queued",
            launch_signature=issue.launch_signature or expected_signature,
        )

    prior_state = issue.launch_state
    prior_error = issue.launch_error
    issue.launch_signature = expected_signature
    issue.launch_claim_token = secrets.token_urlsafe(24)
    issue.launch_claimed_at = datetime.now()
    issue.last_launch_at = datetime.now()
    issue.launch_state = "queued"
    issue.launch_error = None
    issue.updated_at = datetime.now()
    if prior_state != issue.launch_state:
        log_issue_activity(db, issue.id, "field_changed", actor=claimant, field_name="launch_state", old_value=prior_state, new_value=issue.launch_state)
    if prior_error != issue.launch_error:
        log_issue_activity(db, issue.id, "field_changed", actor=claimant, field_name="launch_error", old_value=prior_error, new_value=issue.launch_error)
    db.commit()
    db.refresh(issue)
    return schemas.IssueLaunchClaimResponse(
        issue_id=issue.id,
        claim_token=issue.launch_claim_token,
        launch_state=issue.launch_state or "queued",
        launch_signature=issue.launch_signature or expected_signature,
    )


@app.delete("/api/issues/{issue_id}/launch-claim")
def release_issue_launch_claim(issue_id: int, claim_token: str = Query(...), db: Session = Depends(get_db)):
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.launch_claim_token and issue.launch_claim_token != claim_token:
        raise HTTPException(status_code=409, detail="Launch claim token mismatch")
    prior_state = issue.launch_state
    issue.launch_claim_token = None
    issue.launch_claimed_at = None
    issue.launch_state = determine_launch_state(issue)
    issue.updated_at = datetime.now()
    if prior_state != issue.launch_state:
        log_issue_activity(db, issue.id, "field_changed", actor="Dwight", field_name="launch_state", old_value=prior_state, new_value=issue.launch_state)
    db.commit()
    return {"issue_id": issue.id, "launch_state": issue.launch_state}


@app.post("/api/issues/{issue_id}/launch-result", response_model=schemas.IssueResponse)
def post_issue_launch_result(issue_id: int, payload: schemas.IssueLaunchResultCreate, db: Session = Depends(get_db)):
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    actor = validate_actor_identity(db, payload.username, field_name="username")
    if payload.claim_token and issue.launch_claim_token and payload.claim_token != issue.launch_claim_token:
        raise HTTPException(status_code=409, detail="Launch claim token mismatch")

    next_launch_state = normalize_optional_text(payload.launch_state)
    if next_launch_state not in LAUNCH_TERMINAL_STATES:
        raise HTTPException(status_code=400, detail="launch_state must be launched or failed")

    prior_state = issue.launch_state
    prior_error = issue.launch_error
    launch_error = normalize_optional_text(payload.launch_error)
    issue.launch_state = next_launch_state
    issue.launch_error = launch_error
    issue.launch_claim_token = None
    issue.launch_claimed_at = None
    if issue.last_launch_at is None:
        issue.last_launch_at = datetime.now()
    issue.updated_at = datetime.now()

    if prior_state != issue.launch_state:
        log_issue_activity(db, issue.id, "field_changed", actor=actor, field_name="launch_state", old_value=prior_state, new_value=issue.launch_state)
    if prior_error != issue.launch_error:
        log_issue_activity(db, issue.id, "field_changed", actor=actor, field_name="launch_error", old_value=prior_error, new_value=issue.launch_error)

    comment_content = normalize_optional_text(payload.comment_content)
    if comment_content:
        db_comment = models.Comment(content=comment_content, username=actor, issue_id=issue_id)
        db.add(db_comment)
        db.flush()
        log_issue_activity(db, issue_id, "comment_added", actor=actor, new_value=comment_content[:120])

    db.commit()
    db.refresh(issue)
    return issue

@app.delete("/api/issues/{issue_id}")
def delete_issue(issue_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete an issue and its related records"""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.sprint_id is not None and issue.sprint is not None:
        require_sprint_access(request, db, issue.sprint)

    uploads_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "uploads")
    for image in issue.images:
        file_path = os.path.join(uploads_dir, image.filename)
        if os.path.exists(file_path):
            os.remove(file_path)

    db.delete(issue)
    db.commit()
    return {"message": "Issue deleted successfully"}

@app.post("/api/issues/{issue_id}/comments", response_model=schemas.CommentResponse)
def add_comment(issue_id: int, comment: schemas.CommentCreate, request: Request, db: Session = Depends(get_db)):
    """Add a comment to an issue"""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.sprint_id is not None and issue.sprint is not None:
        require_sprint_access(request, db, issue.sprint)
    
    comment_username = validate_actor_identity(db, comment.username, field_name="username")
    db_comment = models.Comment(
        content=comment.content,
        username=comment_username,
        issue_id=issue_id
    )
    db.add(db_comment)
    db.flush()
    log_issue_activity(db, issue_id, "comment_added", actor=comment_username, new_value=comment.content[:120])
    db.commit()
    db.refresh(db_comment)
    return db_comment

@app.post("/api/issues/{issue_id}/images", response_model=schemas.IssueImageResponse)
async def upload_image(
    issue_id: int,
    request: Request,
    source_type: str = Query("issue", description="issue, description, or comment"),
    comment_id: Optional[int] = Query(None),
    uploaded_by: Optional[str] = Query(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload an image for an issue"""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.sprint_id is not None and issue.sprint is not None:
        require_sprint_access(request, db, issue.sprint)

    normalized_source = source_type.strip().lower()
    if normalized_source not in {"issue", "description", "comment"}:
        raise HTTPException(status_code=400, detail="Invalid source_type. Allowed: issue, description, comment")

    if normalized_source == "comment":
        if comment_id is None:
            raise HTTPException(status_code=400, detail="comment_id is required when source_type=comment")
        comment = db.query(models.Comment).filter(
            models.Comment.id == comment_id,
            models.Comment.issue_id == issue_id,
        ).first()
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found for this issue")
    else:
        comment_id = None
    
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file upload")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large. Max 10 MB")

    detected_type = imghdr.what(None, h=file_bytes)
    if detected_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid image content. Allowed: jpg, png, gif, webp")

    original_ext = os.path.splitext(file.filename or "")[1].lower()
    normalized_ext = ALLOWED_IMAGE_TYPES[detected_type]
    if original_ext and original_ext not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        raise HTTPException(status_code=400, detail="Invalid file extension. Allowed: jpg, jpeg, png, gif, webp")

    uploads_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "uploads")
    os.makedirs(uploads_path, exist_ok=True)

    unique_filename = f"{uuid.uuid4()}{normalized_ext}"
    file_path = os.path.join(uploads_path, unique_filename)

    with open(file_path, "wb") as buffer:
        buffer.write(file_bytes)
    
    uploaded_by = validate_actor_identity(db, uploaded_by, field_name="uploaded_by", allow_blank=True)

    # Create database record
    db_image = models.IssueImage(
        issue_id=issue_id,
        comment_id=comment_id,
        filename=unique_filename,
        source_type=normalized_source,
        uploaded_by=uploaded_by,
    )
    db.add(db_image)
    db.commit()
    db.refresh(db_image)
    
    return db_image

@app.delete("/api/issues/{issue_id}/images/{image_id}")
def delete_image(issue_id: int, image_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete an image from an issue"""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.sprint_id is not None and issue.sprint is not None:
        require_sprint_access(request, db, issue.sprint)

    image = db.query(models.IssueImage).filter(
        models.IssueImage.id == image_id,
        models.IssueImage.issue_id == issue_id
    ).first()
    
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    # Delete file from filesystem
    file_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "uploads", image.filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # Delete database record
    db.delete(image)
    db.commit()
    
    return {"message": "Image deleted successfully"}

@app.post("/api/sprints", response_model=schemas.SprintResponse)
def create_sprint(sprint: schemas.SprintCreate, db: Session = Depends(get_db)):
    """Create a new sprint"""
    allowed_users = parse_and_validate_allowed_users(db, sprint.allowed_users)
    db_sprint = models.Sprint(
        name=sprint.name,
        is_active=False,
        is_archived=False,
        allowed_users_json=json.dumps(allowed_users) if allowed_users else None,
    )
    db.add(db_sprint)
    db.commit()
    db.refresh(db_sprint)
    return serialize_sprint(db_sprint)

@app.get("/api/sprints", response_model=List[schemas.SprintResponse])
def get_sprints(request: Request, active_only: bool = False, db: Session = Depends(get_db)):
    """Get all sprints"""
    query = db.query(models.Sprint)
    if active_only:
        query = query.filter(models.Sprint.is_active == True)
    viewer = resolve_request_viewer(request, db)
    sprints = query.order_by(models.Sprint.id.desc()).all()
    return [serialize_sprint(sprint) for sprint in sprints if viewer_can_access_sprint(viewer, sprint)]

@app.get("/api/sprints/active", response_model=schemas.SprintResponse)
def get_active_sprint(request: Request, db: Session = Depends(get_db)):
    """Get the currently active sprint"""
    viewer = resolve_request_viewer(request, db)
    sprint = None
    for candidate in db.query(models.Sprint).filter(models.Sprint.is_active == True).order_by(models.Sprint.id.desc()).all():
        if viewer_can_access_sprint(viewer, candidate):
            sprint = candidate
            break
    if not sprint:
        raise HTTPException(status_code=404, detail="No active sprint")
    return serialize_sprint(sprint)

@app.get("/api/sprints/{sprint_id}", response_model=schemas.SprintResponse)
def get_sprint(sprint_id: int, request: Request, db: Session = Depends(get_db)):
    """Get a specific sprint by ID"""
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_sprint_access(request, db, sprint)
    return serialize_sprint(sprint)


@app.patch("/api/sprints/{sprint_id}", response_model=schemas.SprintResponse)
def update_sprint(sprint_id: int, payload: schemas.SprintUpdate, request: Request, db: Session = Depends(get_db)):
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_sprint_access(request, db, sprint)

    changed = False
    if payload.name is not None:
        sprint.name = payload.name
        changed = True
    if payload.is_archived is not None:
        sprint.is_archived = bool(payload.is_archived)
        changed = True
    if payload.allowed_users is not None:
        allowed_users = parse_and_validate_allowed_users(db, payload.allowed_users)
        sprint.allowed_users_json = json.dumps(allowed_users) if allowed_users else None
        changed = True
    if changed:
        db.commit()
        db.refresh(sprint)
    return serialize_sprint(sprint)

@app.post("/api/sprints/{sprint_id}/start")
def start_sprint(sprint_id: int, request: Request, db: Session = Depends(get_db)):
    """Start a sprint"""
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_sprint_access(request, db, sprint)

    active_sprints = db.query(models.Sprint).filter(models.Sprint.is_active == True).all()
    for active in active_sprints:
        active.is_active = False
        active.ended_at = datetime.now()
    
    sprint.is_active = True
    sprint.started_at = datetime.now()
    sprint.is_archived = False
    db.commit()
    return {"message": "Sprint started", "sprint_id": sprint_id}

@app.post("/api/sprints/{sprint_id}/end")
def end_sprint(sprint_id: int, request: Request, db: Session = Depends(get_db)):
    """End a sprint without moving its issues to backlog."""
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_sprint_access(request, db, sprint)

    issue_count = db.query(models.Issue).filter(models.Issue.sprint_id == sprint_id).count()
    sprint.is_active = False
    sprint.ended_at = datetime.now()
    db.commit()
    return {"message": "Sprint ended", "issues_retained": issue_count, "sprint_id": sprint_id}

@app.post("/api/issues/{issue_id}/assign-to-sprint")
def assign_to_sprint(issue_id: int, sprint_id: int, request: Request, db: Session = Depends(get_db)):
    """Assign an issue to a sprint without rewriting its status."""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.sprint_id is not None and issue.sprint is not None:
        require_sprint_access(request, db, issue.sprint)

    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_sprint_access(request, db, sprint)

    old_sprint_id = issue.sprint_id
    issue.sprint_id = sprint_id
    issue.updated_at = datetime.now()
    if old_sprint_id != sprint_id:
        log_issue_activity(db, issue_id, "field_changed", field_name="sprint_id", old_value=resolve_sprint_name(db, old_sprint_id), new_value=resolve_sprint_name(db, sprint_id))
    db.commit()
    return {"message": "Issue assigned to sprint", "sprint_id": sprint_id, "issue_id": issue_id, "status": issue.status}


@app.delete("/api/sprints/{sprint_id}")
def delete_sprint(sprint_id: int, request: Request, db: Session = Depends(get_db)):
    sprint = db.query(models.Sprint).filter(models.Sprint.id == sprint_id).first()
    if not sprint:
        raise HTTPException(status_code=404, detail="Sprint not found")
    require_sprint_access(request, db, sprint)
    db.query(models.Issue).filter(models.Issue.sprint_id == sprint_id).update(
        {models.Issue.sprint_id: None, models.Issue.updated_at: datetime.now()},
        synchronize_session=False,
    )
    db.delete(sprint)
    db.commit()
    return {"message": "Sprint deleted", "sprint_id": sprint_id}


@app.post("/api/issues/{issue_id}/contracts/pr-check")
async def check_issue_pr_contract(issue_id: int, request: Request, db: Session = Depends(get_db)):
    """Evaluate PR evidence contract for one issue and optionally post a contract comment."""
    issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    payload = await request.json()
    contract = evaluate_pr_contract(
        issue,
        repo_slug=payload.get("repo_slug"),
        head_ref=payload.get("head_ref"),
        pr_title=payload.get("pr_title"),
        pr_body=payload.get("pr_body"),
        pr_url=payload.get("pr_url"),
    )

    should_comment = payload.get("comment", True)
    if should_comment:
        contract_line = "PASS" if contract["passed"] else f"FAIL ({', '.join(contract['missing'])})"
        body = [
            f"- changed: PR contract check evaluated for issue #{issue_id}.",
            f"- evidence: contract={contract_line}; head_ref={contract.get('head_ref') or 'n/a'}; repo={contract.get('repo_slug') or normalize_optional_text(issue.repo_slug) or 'n/a'}.",
            "- next step: add missing evidence/metadata before merge if contract is FAIL.",
        ]
        comment = models.Comment(content="\n".join(body), username="EWAG-PM", issue_id=issue_id)
        db.add(comment)
        log_issue_activity(db, issue_id, "comment_added", actor="EWAG-PM", new_value=(comment.content or "")[:120])
        issue.updated_at = datetime.now()
        db.commit()

    return {
        "issue_id": issue_id,
        "contract": contract,
    }


@app.post("/api/integrations/github/webhook")
async def github_webhook(
    request: Request,
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
):
    """Event-driven GitHub PR sync. Updates issue state/comments and enforces PR contract checks."""
    raw_body = await request.body()
    if not verify_github_signature(raw_body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_name = (x_github_event or "").strip().lower()
    if event_name == "ping":
        return {"ok": True, "event": "ping"}
    if event_name != "pull_request":
        return {"ok": True, "ignored": True, "event": event_name or "unknown"}

    action = normalize_optional_text(payload.get("action")) or "unknown"
    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    repo_full_name = normalize_optional_text(repo.get("full_name"))

    if GITHUB_WEBHOOK_TRUSTED_REPOS and repo_full_name and repo_full_name not in GITHUB_WEBHOOK_TRUSTED_REPOS:
        return {"ok": True, "ignored": True, "reason": "repo_not_trusted", "repo": repo_full_name}

    pr_number = pr.get("number")
    pr_title = normalize_optional_text(pr.get("title"))
    pr_body = pr.get("body") or ""
    pr_url = normalize_optional_text(pr.get("html_url"))
    pr_draft = bool(pr.get("draft"))
    pr_merged = bool(pr.get("merged"))
    head_ref = normalize_optional_text((pr.get("head") or {}).get("ref"))

    issue_ids = extract_issue_ids_from_pr(head_ref or "", pr_title or "", pr_body)
    if not issue_ids:
        return {
            "ok": True,
            "event": event_name,
            "action": action,
            "matched_issues": 0,
            "reason": "no_issue_reference",
        }

    updated = []
    for issue_id in issue_ids:
        issue = db.query(models.Issue).filter(models.Issue.id == issue_id).first()
        if not issue:
            continue

        if not normalize_optional_text(issue.branch) and head_ref:
            issue.branch = head_ref
        if not normalize_optional_text(issue.repo_slug) and repo_full_name:
            issue.repo_slug = repo_full_name

        if action in {"opened", "reopened", "synchronize", "ready_for_review"}:
            issue.status = "in_review" if not pr_draft else "in_progress"
        elif action == "closed":
            issue.status = "done" if pr_merged else "in_progress"

        contract = evaluate_pr_contract(
            issue,
            repo_slug=repo_full_name,
            head_ref=head_ref,
            pr_title=pr_title,
            pr_body=pr_body,
            pr_url=pr_url,
        )

        contract_line = "PASS" if contract["passed"] else f"FAIL ({', '.join(contract['missing'])})"
        comment_body = [
            f"- changed: GitHub PR webhook `{action}` received for PR #{pr_number}: {pr_title or 'untitled'}.",
            f"- evidence: pr_url={pr_url or 'n/a'}; branch={head_ref or 'n/a'}; repo={repo_full_name or 'n/a'}; merged={pr_merged}; contract={contract_line}.",
            "- next step: if contract is FAIL, update PR description/evidence and keep issue in review until all requirements pass.",
        ]
        comment = models.Comment(
            content="\n".join(comment_body),
            username="EWAG-PM",
            issue_id=issue.id,
        )
        db.add(comment)
        log_issue_activity(db, issue.id, "comment_added", actor="EWAG-PM", new_value=(comment.content or "")[:120])
        issue.updated_at = datetime.now()
        updated.append({
            "issue_id": issue.id,
            "status": issue.status,
            "contract_passed": contract["passed"],
            "contract_missing": contract["missing"],
        })

    db.commit()
    return {
        "ok": True,
        "event": event_name,
        "action": action,
        "pr_number": pr_number,
        "repo": repo_full_name,
        "matched_issues": len(updated),
        "updated": updated,
    }

# Serve static files
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

    # Serve root-level Apple touch icons so Safari's automatic requests don't 404
    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-120x120.png")
    @app.get("/apple-touch-icon-152x152.png")
    @app.get("/apple-touch-icon-167x167.png")
    @app.get("/apple-touch-icon-180x180.png")
    @app.get("/apple-touch-icon-120x120-precomposed.png")
    def _apple_touch_icon(request: Request):
        filename = request.url.path.lstrip("/")
        file_path = os.path.join(os.path.dirname(__file__), "..", "frontend", filename)
        if os.path.exists(file_path):
            return FileResponse(file_path)
        raise HTTPException(status_code=404, detail="Icon not found")

@app.get("/")
def read_root():
    """Serve the configured landing page for this service instance."""
    app_home = os.getenv("APP_HOME", "legacy").strip().lower()
    landing = "factory-login.html" if app_home == "factory" else os.getenv("PUBLIC_AUTH_LANDING", "public-auth.html")
    frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", landing)
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    return {"message": "Task Manager API is running"}


@app.get("/public-auth")
@app.get("/auth-bridge")
@app.get("/backlog")
@app.get("/sprint")
@app.get("/search")
@app.get("/agents")
@app.get("/issue")
@app.get("/public-approvals")
@app.get("/factory")
@app.get("/miniapp")
def serve_named_frontend_routes(request: Request):
    """Serve frontend routes without requiring explicit .html extensions."""
    route_map = {
        "/public-auth": "public-auth.html",
        "/auth-bridge": "auth-bridge.html",
        "/backlog": "backlog.html",
        "/sprint": "sprint.html",
        "/search": "search.html",
        "/agents": "agents.html",
        "/issue": "issue.html",
        "/public-approvals": "public-approvals.html",
        "/factory": "factory.html",
        "/miniapp": "miniapp.html",
    }
    target = route_map.get(request.url.path)
    if not target:
        raise HTTPException(status_code=404, detail="Not found")
    file_path = os.path.join(os.path.dirname(__file__), "..", "frontend", target)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="Not found")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
