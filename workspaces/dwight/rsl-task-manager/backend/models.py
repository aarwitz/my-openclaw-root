from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

STATUS_OPTIONS = {"to_do", "in_progress", "in_review", "done", "blocked"}
PUBLIC_USER_STATUSES = {"pending", "approved", "rejected", "disabled"}


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.now)

class Issue(Base):
    __tablename__ = "issues"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    acceptance_criteria = Column(Text, nullable=True)
    status = Column(String, default="to_do")  # to_do, in_progress, in_review, done, blocked
    sprint_id = Column(Integer, ForeignKey("sprints.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_by = Column(String)
    assigned_to = Column(String, nullable=True)
    branch = Column(String, nullable=True)
    repo_slug = Column(String, nullable=True)
    story_points = Column(Integer, nullable=True)
    blocked_reason = Column(Text, nullable=True)
    auto_launch_enabled = Column(Boolean, default=False, nullable=False)
    launch_state = Column(String, nullable=True)
    launch_error = Column(Text, nullable=True)
    last_launch_at = Column(DateTime, nullable=True)
    launch_signature = Column(Text, nullable=True)
    launch_claim_token = Column(String, nullable=True)
    launch_claimed_at = Column(DateTime, nullable=True)
    
    comments = relationship("Comment", back_populates="issue", cascade="all, delete-orphan")
    images = relationship("IssueImage", back_populates="issue", cascade="all, delete-orphan")
    sprint = relationship("Sprint", back_populates="issues")
    activity_events = relationship("IssueActivity", back_populates="issue", cascade="all, delete-orphan", order_by="desc(IssueActivity.created_at)")

class Comment(Base):
    __tablename__ = "comments"
    
    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text)
    issue_id = Column(Integer, ForeignKey("issues.id"))
    username = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    
    issue = relationship("Issue", back_populates="comments")
    images = relationship("IssueImage", back_populates="comment")

class Sprint(Base):
    __tablename__ = "sprints"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    is_active = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    allowed_users_json = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    
    issues = relationship("Issue", back_populates="sprint")

class IssueImage(Base):
    __tablename__ = "issue_images"
    
    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("issues.id"))
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=True)
    filename = Column(String)
    source_type = Column(String, default="issue")
    uploaded_by = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.now)
    
    issue = relationship("Issue", back_populates="images")
    comment = relationship("Comment", back_populates="images")

class IssueActivity(Base):
    __tablename__ = "issue_activity"

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False)
    field_name = Column(String, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    actor = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    issue = relationship("Issue", back_populates="activity_events")


class LidiActionRequest(Base):
    __tablename__ = "lidi_action_requests"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending, executing, approved, cancelled, expired, failed
    requested_by = Column(String, nullable=False)
    approved_by = Column(String, nullable=True)
    preview_text = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=False)
    result_issue_id = Column(Integer, ForeignKey("issues.id"), nullable=True)
    result_comment_id = Column(Integer, ForeignKey("comments.id"), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)
    approved_at = Column(DateTime, nullable=True)


class PublicUser(Base):
    __tablename__ = "public_users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    company = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)
    is_owner = Column(Boolean, default=False, nullable=False)
    approved_at = Column(DateTime, nullable=True)
    approved_by_email = Column(String, nullable=True)
    email_verified_at = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class SignupRequest(Base):
    __tablename__ = "signup_requests"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    company = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    status_snapshot = Column(String, default="pending", nullable=False)
    requested_at = Column(DateTime, default=datetime.now)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("public_users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("PublicUser")


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("public_users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("PublicUser")


class PublicSession(Base):
    __tablename__ = "public_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("public_users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("PublicUser")
