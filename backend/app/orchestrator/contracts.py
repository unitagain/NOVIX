"""Public Orchestrator application contracts."""

from enum import Enum


class SessionStatus(str, Enum):
    IDLE = "idle"
    GENERATING_BRIEF = "generating_brief"
    WRITING_DRAFT = "writing_draft"
    EDITING = "editing"
    WAITING_FEEDBACK = "waiting_feedback"
    WAITING_USER_INPUT = "waiting_user_input"
    COMPLETED = "completed"
    ERROR = "error"
