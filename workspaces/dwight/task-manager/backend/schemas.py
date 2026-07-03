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
    auto_launch_enabled: bool = False

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
    auto_launch_enabled: Optional[bool] = None
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
    auto_launch_enabled: bool = False
    launch_state: Optional[str] = None
    launch_error: Optional[str] = None
    last_launch_at: Optional[datetime] = None
    comments: List[CommentResponse] = []
    images: List[IssueImageResponse] = []
    activity_events: List[IssueActivityResponse] = []
    
    class Config:
        from_attributes = True

class SprintCreate(BaseModel):
    name: str
    allowed_users: Optional[List[str]] = None


class SprintUpdate(BaseModel):
    name: Optional[str] = None
    is_archived: Optional[bool] = None
    allowed_users: Optional[List[str]] = None

class SprintResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    is_archived: bool = False
    allowed_users: List[str] = []
    human_members: List[str] = []
    working_agent_members: List[str] = []
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


class AgentSessionCreate(BaseModel):
    username: str


class AgentSessionResponse(BaseModel):
    username: str
    session_token: str
    expires_at: datetime


class AgentAccountResponse(BaseModel):
    username: str
    is_human: bool
    created_at: datetime


class ApprovalAction(BaseModel):
    pass


class PublicUserStatusUpdate(BaseModel):
    status: str


class PublicUserOwnerUpdate(BaseModel):
    is_owner: bool


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


class IssueLaunchClaimCreate(BaseModel):
    claimant: str
    source: Optional[str] = None
    expected_signature: str


class IssueLaunchClaimResponse(BaseModel):
    issue_id: int
    claim_token: str
    launch_state: str
    launch_signature: str


class IssueLaunchResultCreate(BaseModel):
    launch_state: str
    launch_error: Optional[str] = None
    comment_content: Optional[str] = None
    username: str
    claim_token: Optional[str] = None
