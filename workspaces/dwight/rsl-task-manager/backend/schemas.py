from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional, List

class UserCreate(BaseModel):
    username: str

class UserResponse(BaseModel):
    id: int
    username: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class IssueCreate(BaseModel):
    title: str
    description: str
    created_by: str
    assigned_to: Optional[str] = None
    sprint_id: Optional[int] = None
    branch: Optional[str] = None
    repo_slug: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    story_points: Optional[int] = None
    blocked_reason: Optional[str] = None

class IssueUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    status: Optional[str] = None
    sprint_id: Optional[int] = None
    assigned_to: Optional[str] = None
    branch: Optional[str] = None
    repo_slug: Optional[str] = None
    story_points: Optional[int] = None
    blocked_reason: Optional[str] = None
    updated_by: Optional[str] = None

class CommentCreate(BaseModel):
    content: str
    username: str

class IssueImageResponse(BaseModel):
    id: int
    filename: str
    issue_id: int
    comment_id: Optional[int] = None
    source_type: str
    uploaded_by: Optional[str] = None
    uploaded_at: datetime
    
    class Config:
        from_attributes = True

class CommentResponse(BaseModel):
    id: int
    content: str
    username: str
    created_at: datetime
    images: List[IssueImageResponse] = []
    
    class Config:
        from_attributes = True

class IssueActivityResponse(BaseModel):
    id: int
    event_type: str
    field_name: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    actor: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class IssueResponse(BaseModel):
    id: int
    title: str
    description: str
    acceptance_criteria: Optional[str] = None
    status: str
    sprint_id: Optional[int]
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: str
    assigned_to: Optional[str] = None
    branch: Optional[str] = None
    repo_slug: Optional[str] = None
    story_points: Optional[int] = None
    blocked_reason: Optional[str] = None
    comments: List[CommentResponse] = []
    images: List[IssueImageResponse] = []
    activity_events: List[IssueActivityResponse] = []
    
    class Config:
        from_attributes = True

class SprintCreate(BaseModel):
    name: str

class SprintResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class PublicSignupCreate(BaseModel):
    email: EmailStr
    full_name: str
    company: Optional[str] = None
    note: Optional[str] = None
    password: str


class PublicUserLogin(BaseModel):
    email: EmailStr
    password: str


class PublicUserResponse(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    company: Optional[str] = None
    status: str
    is_owner: bool
    approved_at: Optional[datetime] = None
    approved_by_email: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PublicAuthResponse(PublicUserResponse):
    session_token: str


class ApprovalAction(BaseModel):
    pass


class PublicUserStatusUpdate(BaseModel):
    status: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class LidiActionDraftCreate(BaseModel):
    action_type: str
    requested_by: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    assigned_to: Optional[str] = None
    sprint_id: Optional[int] = None
    repo_slug: Optional[str] = None
    blocked_reason: Optional[str] = None
    issue_id: Optional[int] = None
    comment_content: Optional[str] = None
    source_prompt: Optional[str] = None


class LidiActionResponse(BaseModel):
    id: int
    action_type: str
    status: str
    requested_by: str
    approved_by: Optional[str] = None
    preview_text: str
    result_issue_id: Optional[int] = None
    result_comment_id: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    expires_at: datetime
    approved_at: Optional[datetime] = None

    class Config:
        from_attributes = True
