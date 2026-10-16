"""Import every model so SQLAlchemy and Alembic see the full schema."""
from app.models.user import User
from app.models.auth_session import AuthSession
from app.models.candidate import Candidate
from app.models.assessment import Assessment
from app.models.question import Question
from app.models.assignment import AssessmentAssignment
from app.models.invitation import Invitation
from app.models.attempt import Attempt
from app.models.answer import Answer
from app.models.result import Result
from app.models.feedback import Feedback
from app.models.email_log import EmailLog

__all__ = [
    "User",
    "AuthSession",
    "Candidate",
    "Assessment",
    "Question",
    "AssessmentAssignment",
    "Invitation",
    "Attempt",
    "Answer",
    "Result",
    "Feedback",
    "EmailLog",
]
