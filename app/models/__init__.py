# app/models/__init__.py
"""Import every model class here so SQLAlchemy's mapper registry always
sees the full set of classes, regardless of which entrypoint imports
first.

This matters because relationships are often declared with string
forward-references (e.g. relationship("Organization")) — SQLAlchemy only
resolves those against classes that have actually been imported somewhere
in the process. The FastAPI API process happens to import most models
indirectly through its routers, but the Celery worker process has its own
separate import chain (workers/tasks.py / celery_app.py) that may only
import the specific models a given task needs — e.g. User — without ever
touching organization.py. That's exactly what caused:

    InvalidRequestError: ... expression 'Organization' failed to locate
    a name ('Organization') ...

in the worker but not the API. Importing app.models (this file) from
wherever the worker sets up its DB session/models — or simply from
database.py's Base setup — guarantees this doesn't happen again for any
future model, in either process.
"""
from app.models.organization import Organization
from app.models.user import User, UserRole
from app.models.project import Project, ProjectMember, ProjectStatus, ReportFormat, ProjectMemberRole
from app.models.floor import Floor
from app.models.floor_ai_summary import FloorAISummary
from app.models.zone import Zone
from app.models.task import TaskTemplate, TaskCategory, ZoneTask
from app.models.report import Report, ReportPhoto, Approval, ReportStatus, ApprovalAction
from app.models.model_file import ModelFile

__all__ = [
    "Organization",
    "User",
    "UserRole",
    "Project",
    "ProjectMember",
    "ProjectStatus",
    "ReportFormat",
    "ProjectMemberRole",
    "Floor",
    "FloorAISummary",
    "Zone",
    "TaskTemplate",
    "TaskCategory",
    "ZoneTask",
    "Report",
    "ReportPhoto",
    "Approval",
    "ReportStatus",
    "ApprovalAction",
    "ModelFile",
]