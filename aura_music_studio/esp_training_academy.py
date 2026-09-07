from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .audit import AuditLedger
from .esp_command_center import EspStore, esp
from .esp_niche import require_esp_hub_member
from .esp_product_workflows import _roles

router = APIRouter(tags=["ESP Training Academy"])

CourseStatus = Literal["draft", "published", "retired"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _clean(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


class TrainingQuestion(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(min_length=2, max_length=12)
    correct_options: list[int] = Field(min_length=1, max_length=12)


class TrainingLesson(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default="", max_length=20000)
    resource_refs: list[str] = Field(default_factory=list, max_length=50)


class CreateCourse(BaseModel):
    slug: str = Field(min_length=2, max_length=120)
    title: str = Field(min_length=2, max_length=240)
    description: str = Field(default="", max_length=4000)
    role_scopes: list[Literal["creator", "agent", "both"]] = Field(default_factory=lambda: ["creator"])
    region_scopes: list[str] = Field(default_factory=list, max_length=40)
    niche_scopes: list[str] = Field(default_factory=list, max_length=80)
    required: bool = False
    pass_percent: int = Field(default=80, ge=0, le=100)
    lessons: list[TrainingLesson] = Field(default_factory=list, max_length=100)
    questions: list[TrainingQuestion] = Field(default_factory=list, max_length=200)


class CreateCourseVersion(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=4000)
    required: bool | None = None
    pass_percent: int | None = Field(default=None, ge=0, le=100)
    lessons: list[TrainingLesson] | None = Field(default=None, max_length=100)
    questions: list[TrainingQuestion] | None = Field(default=None, max_length=200)


class UpdateCourseVersion(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=2, max_length=240)
    description: str = Field(default="", max_length=4000)
    required: bool = False
    pass_percent: int = Field(default=80, ge=0, le=100)
    lessons: list[TrainingLesson] = Field(default_factory=list, max_length=100)
    questions: list[TrainingQuestion] = Field(default_factory=list, max_length=200)


class PublishCourseVersion(BaseModel):
    confirm_publish: bool = False
    reason: str = Field(default="", max_length=1000)


class ExamSubmission(BaseModel):
    answers: dict[str, list[int]] = Field(default_factory=dict)


class TrainingAcademyStore:
    """Versioned Academy content that preserves historical learner completion.

    Course identity is stable while published versions are immutable. Editing a published course
    requires a new draft version, so past attempts/certificates keep the exact content version they
    were earned against.
    """

    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self.audit = AuditLedger(self.esp.accounts)
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_training_courses_v2 (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    role_scopes_json TEXT NOT NULL DEFAULT '[]',
                    region_scopes_json TEXT NOT NULL DEFAULT '[]',
                    niche_scopes_json TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    retired_at TEXT
                );

                CREATE TABLE IF NOT EXISTS esp_training_course_versions_v2 (
                    course_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'draft',
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    required INTEGER NOT NULL DEFAULT 0,
                    pass_percent INTEGER NOT NULL DEFAULT 80,
                    lessons_json TEXT NOT NULL DEFAULT '[]',
                    questions_json TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT,
                    retired_at TEXT,
                    PRIMARY KEY(course_id,version),
                    FOREIGN KEY(course_id) REFERENCES esp_training_courses_v2(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat9_training_versions
                    ON esp_training_course_versions_v2(status,course_id,version DESC);

                CREATE TABLE IF NOT EXISTS esp_training_lesson_progress_v2 (
                    user_id TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    lesson_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY(user_id,course_id,version,lesson_id),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id,version) REFERENCES esp_training_course_versions_v2(course_id,version) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS esp_training_exam_attempts_v2 (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    answers_json TEXT NOT NULL,
                    score_percent REAL NOT NULL,
                    passed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id,version) REFERENCES esp_training_course_versions_v2(course_id,version) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat9_training_attempts
                    ON esp_training_exam_attempts_v2(user_id,course_id,version,created_at DESC);

                CREATE TABLE IF NOT EXISTS esp_training_certificates_v2 (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    issued_at TEXT NOT NULL,
                    source_attempt_id TEXT,
                    UNIQUE(user_id,course_id,version),
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(course_id,version) REFERENCES esp_training_course_versions_v2(course_id,version) ON DELETE CASCADE,
                    FOREIGN KEY(source_attempt_id) REFERENCES esp_training_exam_attempts_v2(id) ON DELETE SET NULL
                );
                """
            )

    @staticmethod
    def _validate_content(lessons: list[TrainingLesson], questions: list[TrainingQuestion]) -> None:
        lesson_ids = [item.id.strip() for item in lessons]
        if len(lesson_ids) != len(set(lesson_ids)):
            raise ValueError("Lesson IDs must be unique within a course version")
        question_ids = [item.id.strip() for item in questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Question IDs must be unique within a course version")
        for question in questions:
            if any(index < 0 or index >= len(question.options) for index in question.correct_options):
                raise ValueError(f"Question {question.id} has a correct option outside its option list")

    def create_course(self, body: CreateCourse, *, actor: str) -> dict:
        slug = body.slug.strip().lower().replace(" ", "-")
        if not slug or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in slug):
            raise ValueError("Course slug may contain only letters, numbers, hyphens and underscores")
        self._validate_content(body.lessons, body.questions)
        course_id = uuid4().hex
        now = _now()
        try:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    """INSERT INTO esp_training_courses_v2
                       (id,slug,role_scopes_json,region_scopes_json,niche_scopes_json,created_by,created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        course_id, slug, _json(body.role_scopes), _json([_clean(v,120) for v in body.region_scopes]),
                        _json([_clean(v,120) for v in body.niche_scopes]), actor, now,
                    ),
                )
                con.execute(
                    """INSERT INTO esp_training_course_versions_v2
                       (course_id,version,revision,status,title,description,required,pass_percent,lessons_json,questions_json,
                        created_by,created_at,updated_at)
                       VALUES (?,1,1,'draft',?,?,?,?,?,?,?,?,?)""",
                    (
                        course_id, _clean(body.title,240), body.description.strip()[:4000], 1 if body.required else 0,
                        body.pass_percent, _json([item.model_dump() for item in body.lessons]),
                        _json([item.model_dump() for item in body.questions]), actor, now, now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc).upper():
                raise FileExistsError("training_course_slug_exists") from exc
            raise
        self.audit.append(actor=actor, action="chat9.training_course_created", details={"course_id":course_id,"slug":slug,"version":1})
        return self.course_version(course_id, 1, include_answers=True)

    def _course_row(self, con: sqlite3.Connection, course_id: str):
        return con.execute("SELECT * FROM esp_training_courses_v2 WHERE id=?", (course_id,)).fetchone()

    def course_version(self, course_id: str, version: int, *, include_answers: bool = False) -> dict:
        with self._connect() as con:
            course = self._course_row(con, course_id)
            row = con.execute(
                "SELECT * FROM esp_training_course_versions_v2 WHERE course_id=? AND version=?",
                (course_id, version),
            ).fetchone()
        if course is None or row is None:
            raise KeyError("Training course/version not found")
        item = dict(row)
        item.update({
            "slug": course["slug"],
            "role_scopes": _loads(course["role_scopes_json"], []),
            "region_scopes": _loads(course["region_scopes_json"], []),
            "niche_scopes": _loads(course["niche_scopes_json"], []),
        })
        item["required"] = bool(item["required"])
        item["lessons"] = _loads(item.pop("lessons_json"), [])
        questions = _loads(item.pop("questions_json"), [])
        if not include_answers:
            questions = [
                {key:value for key,value in question.items() if key != "correct_options"}
                for question in questions
            ]
        item["questions"] = questions
        for key in ("role_scopes_json","region_scopes_json","niche_scopes_json"):
            item.pop(key, None)
        return item

    def latest_published(self, course_id: str) -> dict:
        with self._connect() as con:
            row = con.execute(
                """SELECT version FROM esp_training_course_versions_v2
                   WHERE course_id=? AND status='published' ORDER BY version DESC LIMIT 1""",
                (course_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Published training course not found")
        return self.course_version(course_id, int(row["version"]), include_answers=False)

    @staticmethod
    def audience_matches(course: dict, membership: dict, niche: str = "") -> bool:
        roles = _roles(membership)
        role_scopes = set(course.get("role_scopes") or [])
        if "owner" not in roles and role_scopes and not roles.intersection(role_scopes):
            return False
        regions = {str(v).strip().lower() for v in course.get("region_scopes") or [] if str(v).strip()}
        if regions and str(membership.get("region") or "").strip().lower() not in regions:
            return False
        niches = {str(v).strip().lower() for v in course.get("niche_scopes") or [] if str(v).strip()}
        if niches and (niche or "").strip().lower() not in niches:
            return False
        return True

    def list_published(self, membership: dict, *, niche: str = "") -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT c.id,MAX(v.version) version FROM esp_training_courses_v2 c
                   JOIN esp_training_course_versions_v2 v ON v.course_id=c.id
                   WHERE c.retired_at IS NULL AND v.status='published' GROUP BY c.id ORDER BY c.slug"""
            ).fetchall()
        result=[]
        for row in rows:
            item=self.course_version(row["id"],int(row["version"]),include_answers=False)
            if self.audience_matches(item,membership,niche=niche):
                result.append(item)
        return result

    def new_version(self, course_id: str, body: CreateCourseVersion, *, actor: str) -> dict:
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            course=self._course_row(con,course_id)
            if course is None:
                raise KeyError("Training course not found")
            previous=con.execute(
                "SELECT * FROM esp_training_course_versions_v2 WHERE course_id=? ORDER BY version DESC LIMIT 1",
                (course_id,),
            ).fetchone()
            if previous is None:
                raise KeyError("Training course version not found")
            version=int(previous["version"])+1
            lessons=[TrainingLesson(**item) for item in (_loads(previous["lessons_json"],[]) if body.lessons is None else [x.model_dump() for x in body.lessons])]
            questions=[TrainingQuestion(**item) for item in (_loads(previous["questions_json"],[]) if body.questions is None else [x.model_dump() for x in body.questions])]
            self._validate_content(lessons,questions)
            now=_now()
            con.execute(
                """INSERT INTO esp_training_course_versions_v2
                   (course_id,version,revision,status,title,description,required,pass_percent,lessons_json,questions_json,
                    created_by,created_at,updated_at)
                   VALUES (?,?,1,'draft',?,?,?,?,?,?,?,?,?)""",
                (
                    course_id,version,_clean(body.title if body.title is not None else previous["title"],240),
                    (body.description if body.description is not None else previous["description"]).strip()[:4000],
                    int(body.required if body.required is not None else bool(previous["required"])),
                    int(body.pass_percent if body.pass_percent is not None else previous["pass_percent"]),
                    _json([x.model_dump() for x in lessons]),_json([x.model_dump() for x in questions]),actor,now,now,
                ),
            )
        self.audit.append(actor=actor,action="chat9.training_version_created",details={"course_id":course_id,"version":version})
        return self.course_version(course_id,version,include_answers=True)

    def update_draft(self, course_id: str, version: int, body: UpdateCourseVersion, *, actor: str) -> dict:
        self._validate_content(body.lessons, body.questions)
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row=con.execute(
                "SELECT * FROM esp_training_course_versions_v2 WHERE course_id=? AND version=?",
                (course_id,version),
            ).fetchone()
            if row is None:
                raise KeyError("Training course/version not found")
            if row["status"] != "draft":
                raise PermissionError("Published/retired course versions are immutable; create a new draft version")
            if int(row["revision"]) != body.expected_revision:
                raise RuntimeError("stale_version")
            con.execute(
                """UPDATE esp_training_course_versions_v2 SET revision=revision+1,title=?,description=?,required=?,
                   pass_percent=?,lessons_json=?,questions_json=?,updated_at=? WHERE course_id=? AND version=?""",
                (
                    _clean(body.title,240),body.description.strip()[:4000],1 if body.required else 0,body.pass_percent,
                    _json([x.model_dump() for x in body.lessons]),_json([x.model_dump() for x in body.questions]),
                    _now(),course_id,version,
                ),
            )
        self.audit.append(actor=actor,action="chat9.training_draft_updated",details={"course_id":course_id,"version":version})
        return self.course_version(course_id,version,include_answers=True)

    def publish(self, course_id: str, version: int, *, actor: str, confirmation: PublishCourseVersion) -> dict:
        if not confirmation.confirm_publish:
            raise PermissionError("high_impact_confirmation_required")
        now=_now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row=con.execute(
                "SELECT * FROM esp_training_course_versions_v2 WHERE course_id=? AND version=?",
                (course_id,version),
            ).fetchone()
            if row is None:
                raise KeyError("Training course/version not found")
            if row["status"] != "draft":
                raise PermissionError("Only draft course versions can be published")
            con.execute(
                "UPDATE esp_training_course_versions_v2 SET status='published',published_at=?,updated_at=? WHERE course_id=? AND version=?",
                (now,now,course_id,version),
            )
        self.audit.append(actor=actor,action="chat9.training_version_published",details={"course_id":course_id,"version":version,"reason":confirmation.reason[:300]})
        return self.course_version(course_id,version,include_answers=True)

    def complete_lesson(self, user_id: str, course_id: str, version: int, lesson_id: str) -> dict:
        course=self.course_version(course_id,version,include_answers=False)
        if course["status"] != "published":
            raise PermissionError("Training progress can only be recorded against a published version")
        if lesson_id not in {str(item.get("id")) for item in course["lessons"]}:
            raise KeyError("Training lesson not found")
        with self._connect() as con:
            con.execute(
                """INSERT INTO esp_training_lesson_progress_v2(user_id,course_id,version,lesson_id,completed_at)
                   VALUES (?,?,?,?,?) ON CONFLICT(user_id,course_id,version,lesson_id) DO NOTHING""",
                (user_id,course_id,version,lesson_id,_now()),
            )
        return self.progress(user_id,course_id,version)

    def progress(self,user_id:str,course_id:str,version:int)->dict:
        course=self.course_version(course_id,version,include_answers=False)
        with self._connect() as con:
            lessons=con.execute(
                "SELECT lesson_id,completed_at FROM esp_training_lesson_progress_v2 WHERE user_id=? AND course_id=? AND version=? ORDER BY completed_at",
                (user_id,course_id,version),
            ).fetchall()
            attempts=con.execute(
                "SELECT id,score_percent,passed,created_at FROM esp_training_exam_attempts_v2 WHERE user_id=? AND course_id=? AND version=? ORDER BY created_at DESC",
                (user_id,course_id,version),
            ).fetchall()
            certificate=con.execute(
                "SELECT id,issued_at,source_attempt_id FROM esp_training_certificates_v2 WHERE user_id=? AND course_id=? AND version=?",
                (user_id,course_id,version),
            ).fetchone()
        total=len(course["lessons"])
        done=len(lessons)
        return {"course_id":course_id,"version":version,"lessons_completed":done,"lessons_total":total,"lesson_percent":round((done/total)*100,1) if total else 100.0,"completed_lessons":[dict(r) for r in lessons],"attempts":[{**dict(r),"passed":bool(r["passed"])} for r in attempts],"certificate":dict(certificate) if certificate else None}

    def submit_exam(self,user_id:str,course_id:str,version:int,answers:dict[str,list[int]])->dict:
        course=self.course_version(course_id,version,include_answers=True)
        if course["status"] != "published":
            raise PermissionError("Only published course versions accept exam attempts")
        questions=course["questions"]
        correct=0
        for question in questions:
            expected=sorted(set(int(v) for v in question.get("correct_options") or []))
            supplied=sorted(set(int(v) for v in answers.get(str(question.get("id")),[]) if isinstance(v,int)))
            if supplied == expected:
                correct += 1
        score=round((correct/len(questions))*100,2) if questions else 100.0
        passed=score >= float(course["pass_percent"])
        attempt_id=uuid4().hex
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """INSERT INTO esp_training_exam_attempts_v2(id,user_id,course_id,version,answers_json,score_percent,passed,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (attempt_id,user_id,course_id,version,_json(answers),score,1 if passed else 0,_now()),
            )
            certificate_id=None
            if passed:
                existing=con.execute(
                    "SELECT id FROM esp_training_certificates_v2 WHERE user_id=? AND course_id=? AND version=?",
                    (user_id,course_id,version),
                ).fetchone()
                if existing:
                    certificate_id=existing["id"]
                else:
                    certificate_id=uuid4().hex
                    con.execute(
                        """INSERT INTO esp_training_certificates_v2(id,user_id,course_id,version,issued_at,source_attempt_id)
                           VALUES (?,?,?,?,?,?)""",
                        (certificate_id,user_id,course_id,version,_now(),attempt_id),
                    )
        return {"attempt_id":attempt_id,"course_id":course_id,"version":version,"score_percent":score,"pass_percent":course["pass_percent"],"passed":passed,"certificate_id":certificate_id}


academy=TrainingAcademyStore()


def _member(request:Request):
    return require_esp_hub_member(request)


def _owner(request:Request):
    member,membership=require_esp_hub_member(request)
    if "owner" not in _roles(membership):
        raise HTTPException(403,"ESP Owner authority is required")
    return member,membership


@router.get("/command-center/api/training/courses")
def published_courses(request:Request,niche:str=""):
    _member_row,membership=_member(request)
    return {"courses":academy.list_published(membership,niche=niche),"answer_keys_exposed":False}


@router.get("/command-center/api/training/courses/{course_id}")
def course_detail(course_id:str,request:Request,niche:str=""):
    _member_row,membership=_member(request)
    try:
        course=academy.latest_published(course_id)
    except KeyError as exc:
        raise HTTPException(404,"Published training course not found") from exc
    if not academy.audience_matches(course,membership,niche=niche):
        raise HTTPException(404,"Published training course not found")
    return {"course":course,"answer_keys_exposed":False}


@router.post("/command-center/api/training/courses/{course_id}/versions/{version}/lessons/{lesson_id}/complete")
def complete_lesson(course_id:str,version:int,lesson_id:str,request:Request):
    member,membership=_member(request)
    try:
        course=academy.course_version(course_id,version,include_answers=False)
        if not academy.audience_matches(course,membership):
            raise HTTPException(404,"Training course not found")
        return academy.complete_lesson(member.user_id,course_id,version,lesson_id)
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.post("/command-center/api/training/courses/{course_id}/versions/{version}/exam")
def submit_exam(course_id:str,version:int,body:ExamSubmission,request:Request):
    member,membership=_member(request)
    try:
        course=academy.course_version(course_id,version,include_answers=False)
        if not academy.audience_matches(course,membership):
            raise HTTPException(404,"Training course not found")
        return academy.submit_exam(member.user_id,course_id,version,body.answers)
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409,str(exc)) from exc


@router.get("/command-center/api/training/courses/{course_id}/versions/{version}/progress")
def training_progress(course_id:str,version:int,request:Request):
    member,membership=_member(request)
    try:
        course=academy.course_version(course_id,version,include_answers=False)
        if not academy.audience_matches(course,membership):
            raise HTTPException(404,"Training course not found")
        return academy.progress(member.user_id,course_id,version)
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from exc


@router.post("/command-center/api/training/owner/courses")
def owner_create_course(body:CreateCourse,request:Request):
    member,_membership=_owner(request)
    try:
        return {"course":academy.create_course(body,actor=member.user_id)}
    except FileExistsError as exc:
        raise HTTPException(409,{"code":"training_course_slug_exists"}) from exc
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@router.post("/command-center/api/training/owner/courses/{course_id}/versions")
def owner_new_version(course_id:str,body:CreateCourseVersion,request:Request):
    member,_membership=_owner(request)
    try:
        return {"course":academy.new_version(course_id,body,actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@router.put("/command-center/api/training/owner/courses/{course_id}/versions/{version}")
def owner_update_version(course_id:str,version:int,body:UpdateCourseVersion,request:Request):
    member,_membership=_owner(request)
    try:
        return {"course":academy.update_draft(course_id,version,body,actor=member.user_id)}
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409,{"code":"stale_version"}) from exc
    except PermissionError as exc:
        raise HTTPException(409,str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@router.post("/command-center/api/training/owner/courses/{course_id}/versions/{version}/publish")
def owner_publish_version(course_id:str,version:int,body:PublishCourseVersion,request:Request):
    member,_membership=_owner(request)
    try:
        return {"course":academy.publish(course_id,version,actor=member.user_id,confirmation=body)}
    except KeyError as exc:
        raise HTTPException(404,str(exc)) from exc
    except PermissionError as exc:
        code="high_impact_confirmation_required" if "high_impact" in str(exc) else "invalid_training_state"
        raise HTTPException(409,{"code":code,"message":str(exc)}) from exc


@router.get("/command-center/training",response_class=HTMLResponse,include_in_schema=False)
def training_page(request:Request,niche:str=""):
    member,membership=_member(request)
    courses=academy.list_published(membership,niche=niche)
    cards="".join(
        f"<article class='card'><div class='pill'>Version {int(course['version'])} · {'Required' if course['required'] else 'Optional'}</div><h2>{escape(course['title'])}</h2><p>{escape(course['description'])}</p><p class='muted'>{len(course['lessons'])} lessons · pass mark {int(course['pass_percent'])}%</p></article>"
        for course in courses
    ) or "<div class='card muted'>No published training is assigned to this role/region/niche yet.</div>"
    return HTMLResponse(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>ESP Training Academy</title><style>:root{{color-scheme:dark;--gold:#efc86f;--line:#483550;--muted:#c8bfd2}}*{{box-sizing:border-box}}body{{margin:0;background:#09060e;color:#fff;font-family:Inter,system-ui,sans-serif}}main{{max-width:1000px;margin:auto;padding:24px}}.card{{border:1px solid var(--line);border-radius:16px;padding:18px;margin:12px 0;background:#17101e}}.pill{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:5px 9px;color:var(--gold)}}.muted{{color:var(--muted)}}a{{color:var(--gold)}}:focus-visible{{outline:3px solid #fff;outline-offset:3px}}</style></head><body><main><a href='/command-center/level-up'>← Level Up</a><h1>ESP Training Academy</h1><p class='muted'>Published course versions are immutable. Your progress and certificate always retain the exact version completed.</p>{cards}</main></body></html>""",headers={"Cache-Control":"no-store"})


__all__=["router","TrainingAcademyStore","academy"]
