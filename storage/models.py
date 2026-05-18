from datetime import datetime

from sqlmodel import Field, SQLModel


class Trend(SQLModel, table=True):
    __tablename__ = "trends"

    id: int | None = Field(default=None, primary_key=True)
    source: str
    keyword: str
    score: float = 1.0
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    is_processed: bool = False


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    trend_id: int = Field(foreign_key="trends.id")
    status: str = "pending"  # pending|generating|uploading|done|failed
    workflow_json: str | None = None  # JSON string
    image_path: str | None = None
    title: str | None = None
    keywords: str | None = None  # JSON array string
    hashtags: str | None = None  # JSON array string
    category: str | None = None
    error_msg: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    uploaded_at: datetime | None = None


class UploadResult(SQLModel, table=True):
    __tablename__ = "upload_results"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id")
    stock: str  # shutterstock|adobe
    external_id: str | None = None
    review_status: str = "pending"  # pending|approved|rejected
    reject_reason: str | None = None
    checked_at: datetime | None = None
