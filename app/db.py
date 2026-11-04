"""SQLAlchemy models. Every revision of a question is kept as a Version row."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import config


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class School(Base):
    __tablename__ = "schools"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    address: Mapped[str] = mapped_column(String(400), default="")
    logo_path: Mapped[str] = mapped_column(String(400), default="")
    allow_external_ai: Mapped[bool] = mapped_column(default=True)


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), index=True)
    subject: Mapped[str] = mapped_column(String(40), default="Physics", index=True)
    class_name: Mapped[str] = mapped_column(String(20), default="", index=True)
    chapter: Mapped[str] = mapped_column(String(120), default="", index=True)
    topic: Mapped[str] = mapped_column(String(120), default="")
    concepts: Mapped[list] = mapped_column(JSON, default=list)
    difficulty: Mapped[str] = mapped_column(String(10), default="Medium", index=True)
    marks: Mapped[int] = mapped_column(Integer, default=1)
    question_type: Mapped[str] = mapped_column(String(30), default="Numerical")
    status: Mapped[str] = mapped_column(String(24), default="uploaded", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(80), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    source_image: Mapped[str] = mapped_column(String(400), default="")
    figure_image: Mapped[str] = mapped_column(String(400), default="")
    regions: Mapped[list] = mapped_column(JSON, default=list)  # detected/corrected text & figure boxes
    versions: Mapped[list["Version"]] = relationship(
        back_populates="question", order_by="Version.number", cascade="all, delete-orphan")

    @property
    def code(self) -> str:
        return f"{(self.subject[:3] or 'GEN').upper()}-{self.id:06d}"


class Version(Base):
    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    diagram: Mapped[dict] = mapped_column(JSON, default=dict)
    engine: Mapped[str] = mapped_column(String(20), default="manual")
    note: Mapped[str] = mapped_column(String(400), default="")
    author: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    question: Mapped[Question] = relationship(back_populates="versions")


class Paper(Base):
    __tablename__ = "papers"
    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    class_name: Mapped[str] = mapped_column(String(20), default="")
    subject: Mapped[str] = mapped_column(String(40), default="")
    duration: Mapped[str] = mapped_column(String(40), default="3 Hours")
    question_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


config.STORAGE_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if not s.query(School).first():
            s.add(School(name="Default School"))
            s.commit()


def get_session():
    with SessionLocal() as s:
        yield s
