import os
import re
import hmac
import json
import hashlib
import urllib.request
import urllib.error
import urllib.parse
import random
import io
import csv
import math
from datetime import datetime, date
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from . import models, schemas, auth
from .database import engine, get_db, Base

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Richseed Private School API")

# Allow the frontend (running on any origin during development) to call this API.
# Once deployed, replace "*" with your actual site URL for safety.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _next_code_number(db, column, prefix):
    """Next free number for codes like S001 / T001.
    Uses the highest existing number (not the row count), so deleting a
    student or teacher can never cause a duplicate code."""
    nums = []
    for (c,) in db.query(column).all():
        if c and c.startswith(prefix) and c[len(prefix):].isdigit():
            nums.append(int(c[len(prefix):]))
    return (max(nums) if nums else 0) + 1


# ---------- Startup: seed default rows so the API is usable immediately ----------
@app.on_event("startup")
def seed_defaults():
    """Creates only what the system needs. Demo teachers/students are created ONLY
    if SEED_DEMO=true is set AND the database is brand new, so deleting them
    can never bring them back after a restart."""
    db = next(get_db())
    first_run = not db.query(models.AdminSettings).first()
    if first_run:
        db.add(models.AdminSettings(id=1, pin=os.environ.get("ADMIN_PIN", "1234")))
        db.add_all([
            models.FeeItem(name="Tuition Fee", amount=45000),
            models.FeeItem(name="Development Levy", amount=8000),
            models.FeeItem(name="Exam Fee", amount=5000),
            models.FeeItem(name="Uniform", amount=6000),
            models.FeeItem(name="ICT Fee", amount=3000),
        ])
    if not db.query(models.ResultLock).first():
        db.add(models.ResultLock(id=1, locked=True))
    if not db.query(models.Geofence).first():
        db.add(models.Geofence(id=1, latitude=7.4341, longitude=3.9357, radius_m=200))
    if not db.query(models.ClassLevel).first():
        names = (["Nursery %d" % i for i in range(1, 4)] + ["Primary %d" % i for i in range(1, 7)]
                 + ["JSS %d" % i for i in range(1, 4)] + ["SS %d" % i for i in range(1, 4)])
        db.add_all([models.ClassLevel(name=n, position=i + 1, fee=0) for i, n in enumerate(names)])
    if not db.query(models.ProgrammeFee).first():
        db.add_all([
            models.ProgrammeFee(id=1, name="Crèche", age_text="From 3 months", fee=35000),
            models.ProgrammeFee(id=2, name="Nursery School", age_text="From age 2", fee=40000),
            models.ProgrammeFee(id=3, name="Primary School", age_text="From age 5", fee=45000),
            models.ProgrammeFee(id=4, name="Junior Secondary", age_text="From age 10", fee=55000),
            models.ProgrammeFee(id=5, name="Senior Secondary", age_text="From age 13", fee=65000),
        ])
    db.commit()
    _sync_fees(db)
    if first_run and os.environ.get("SEED_DEMO", "").lower() == "true":
        db.add_all([
            models.Teacher(teacher_code="T001", name="Mrs. Adaeze Okafor", subject="Mathematics", pin="2847"),
            models.Teacher(teacher_code="T002", name="Mr. Emeka Nwachukwu", subject="English Language", pin="3619"),
            models.Teacher(teacher_code="T003", name="Miss Fatima Bello", subject="Basic Science", pin="7134"),
        ])
        db.commit()
        t1 = db.query(models.Teacher).first()
        db.add_all([
            models.Student(student_code="S001", name="Chukwuemeka Obi", class_name="JSS 2", fees_due=0,
                           fees_paid=45000, result_access="pending", teacher_id=t1.id),
            models.Student(student_code="S002", name="Amina Yusuf", class_name="JSS 2", fees_due=0,
                           fees_paid=67000, result_access="approved", teacher_id=t1.id),
        ])
        db.commit()
    db.close()


# =========================================================
# AUTH
# =========================================================
@app.post("/api/admin/login")
def admin_login(payload: schemas.PinCheck, db: Session = Depends(get_db)):
    key = "admin-fail"
    auth.guard(key, 8, 600)
    settings = db.query(models.AdminSettings).first()
    if not hmac.compare_digest(str(payload.pin), str(settings.pin)):
        auth.record(key)
        raise HTTPException(status_code=401, detail="Incorrect PIN")
    auth.clear(key)
    return {"success": True, "message": "Welcome, Admin", "token": auth.make_token("admin")}


@app.post("/api/admin/change-pin", dependencies=[Depends(auth.require_admin)])
def change_admin_pin(payload: schemas.PinChange, db: Session = Depends(get_db)):
    settings = db.query(models.AdminSettings).first()
    if payload.current_pin != settings.pin:
        raise HTTPException(status_code=401, detail="Current PIN is incorrect")
    if len(payload.new_pin) != 4 or not payload.new_pin.isdigit():
        raise HTTPException(status_code=400, detail="New PIN must be exactly 4 digits")
    settings.pin = payload.new_pin
    db.commit()
    return {"success": True}


# =========================================================
# OVERVIEW
# =========================================================
@app.get("/api/admin/overview", dependencies=[Depends(auth.require_admin)])
def get_overview(db: Session = Depends(get_db)):
    teachers = db.query(models.Teacher).all()
    students = db.query(models.Student).all()
    present_today = sum(1 for t in teachers if t.checked_in)
    today = date.today().isoformat()
    student_attendance_today = (
        db.query(models.Attendance).filter(models.Attendance.date == today).all()
    )
    return {
        "teacher_count": len(teachers),
        "student_count": len(students),
        "present_today": present_today,
        "teachers": [
            {"name": t.name, "subject": t.subject, "checked_in": t.checked_in, "time": t.check_in_time}
            for t in teachers
        ],
        "student_attendance_today": [
            {
                "student_id": a.student_id,
                "time_in": a.time_in,
                "status": a.status,
                "late": a.late,
            }
            for a in student_attendance_today
        ],
    }


# =========================================================
# TEACHERS
# =========================================================
MAX_TEACHERS = 50


@app.get("/api/admin/teachers", response_model=List[schemas.TeacherOut], dependencies=[Depends(auth.require_admin)])
def list_teachers(db: Session = Depends(get_db)):
    return db.query(models.Teacher).all()


@app.post("/api/admin/teachers", response_model=schemas.TeacherOut, dependencies=[Depends(auth.require_admin)])
def enroll_teacher(payload: schemas.TeacherCreate, db: Session = Depends(get_db)):
    count = db.query(models.Teacher).count()
    if count >= MAX_TEACHERS:
        raise HTTPException(status_code=400, detail=f"Maximum of {MAX_TEACHERS} teachers reached")
    code = f"T{_next_code_number(db, models.Teacher.teacher_code, 'T'):03d}"
    pin = f"{random.randint(0, 9999):04d}"
    teacher = models.Teacher(teacher_code=code, name=payload.name, subject=payload.subject, pin=pin)
    db.add(teacher)
    db.commit()
    db.refresh(teacher)
    return teacher


@app.delete("/api/admin/teachers/{teacher_id}", dependencies=[Depends(auth.require_admin)])
def remove_teacher(teacher_id: int, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    db.delete(teacher)
    db.commit()
    return {"success": True}


@app.put("/api/admin/teachers/{teacher_id}", response_model=schemas.TeacherOut, dependencies=[Depends(auth.require_admin)])
def edit_teacher(teacher_id: int, payload: schemas.TeacherCreate, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    teacher.name = payload.name
    teacher.subject = payload.subject
    db.commit()
    db.refresh(teacher)
    return teacher


@app.get("/api/admin/teachers/{teacher_id}/profile", dependencies=[Depends(auth.require_admin)])
def teacher_profile(teacher_id: int, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    students = _teacher_students(db, teacher)
    return {
        "teacher": schemas.TeacherOut.from_orm(teacher),
        "students": [schemas.StudentOut.from_orm(s) for s in students],
    }


@app.get("/api/admin/teachers/export.csv", dependencies=[Depends(auth.require_admin)])
def export_teachers_csv(db: Session = Depends(get_db)):
    teachers = db.query(models.Teacher).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Name", "Subject", "ID", "PIN"])
    for t in teachers:
        writer.writerow([t.name, t.subject, t.teacher_code, t.pin])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=richseed-teacher-codes.csv"},
    )


# =========================================================
# FINANCE
# =========================================================
def _norm(x) -> str:
    return re.sub(r"\s+", " ", (x or "")).strip().lower()


def _clean_text(x: str, n: int = 60) -> str:
    return re.sub(r'[<>"\'`&\\]', "", x or "").strip()[:n]


def _class_row(db: Session, name):
    n = _norm(name)
    if not n:
        return None
    for c in db.query(models.ClassLevel).all():
        if _norm(c.name) == n:
            return c
    return None


def _require_class(db: Session, name) -> "models.ClassLevel":
    c = _class_row(db, name)
    if not c:
        raise HTTPException(status_code=400, detail="Please choose a class from the list")
    return c


def _fee_total(db: Session, class_name=None) -> float:
    """Common fees (charged to everyone) + the fee for this student's class."""
    common = float(sum(f.amount for f in db.query(models.FeeItem).all()))
    c = _class_row(db, class_name) if class_name else None
    return common + (float(c.fee) if c else 0.0)


def _sync_fees(db: Session):
    """Recalculate every student's total from the fee list and their class."""
    common = float(sum(f.amount for f in db.query(models.FeeItem).all()))
    fees = {_norm(c.name): float(c.fee or 0) for c in db.query(models.ClassLevel).all()}
    for st in db.query(models.Student).all():
        st.fees_due = common + fees.get(_norm(st.class_name), 0.0)
    db.commit()


def _teacher_students(db: Session, teacher):
    cn = _norm(teacher.class_name)
    return [s for s in db.query(models.Student).all()
            if s.teacher_id == teacher.id or (cn and _norm(s.class_name) == cn)]


@app.get("/api/admin/fees", response_model=List[schemas.FeeItemOut], dependencies=[Depends(auth.require_admin)])
def get_fee_structure(db: Session = Depends(get_db)):
    return db.query(models.FeeItem).all()


@app.post("/api/admin/fees", response_model=schemas.FeeItemOut, dependencies=[Depends(auth.require_admin)])
def add_fee_item(payload: schemas.FeeItemCreate, db: Session = Depends(get_db)):
    item = models.FeeItem(name=payload.name, amount=payload.amount)
    db.add(item)
    db.commit()
    _sync_fees(db)
    db.refresh(item)
    return item


@app.put("/api/admin/fees/{fee_id}", response_model=schemas.FeeItemOut, dependencies=[Depends(auth.require_admin)])
def edit_fee_item(fee_id: int, payload: schemas.FeeItemCreate, db: Session = Depends(get_db)):
    item = db.query(models.FeeItem).filter(models.FeeItem.id == fee_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Fee item not found")
    item.name = payload.name
    item.amount = payload.amount
    db.commit()
    _sync_fees(db)
    db.refresh(item)
    return item


@app.delete("/api/admin/fees/{fee_id}", dependencies=[Depends(auth.require_admin)])
def delete_fee_item(fee_id: int, db: Session = Depends(get_db)):
    item = db.query(models.FeeItem).filter(models.FeeItem.id == fee_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Fee item not found")
    db.delete(item)
    db.commit()
    _sync_fees(db)
    return {"success": True}


@app.get("/api/admin/payments", dependencies=[Depends(auth.require_admin)])
def payment_status(db: Session = Depends(get_db)):
    students = db.query(models.Student).all()
    return [
        {
            "student_id": s.id,
            "name": s.name,
            "fees_due": s.fees_due,
            "fees_paid": s.fees_paid,
            "owing": s.fees_due - s.fees_paid,
            "status": "Paid" if s.fees_paid >= s.fees_due else ("Partial" if s.fees_paid > 0 else "Pending"),
        }
        for s in students
    ]


# =========================================================
# RESULTS ACCESS CONTROL
# =========================================================
@app.get("/api/admin/results/lock", dependencies=[Depends(auth.require_admin)])
def get_result_lock(db: Session = Depends(get_db)):
    lock = db.query(models.ResultLock).first()
    return {"locked": lock.locked}


@app.post("/api/admin/results/lock", dependencies=[Depends(auth.require_admin)])
def set_result_lock(locked: bool, db: Session = Depends(get_db)):
    lock = db.query(models.ResultLock).first()
    lock.locked = locked
    db.commit()
    return {"locked": lock.locked}


@app.post("/api/admin/students/{student_id}/result-access", dependencies=[Depends(auth.require_admin)])
def set_student_result_access(student_id: int, payload: schemas.ResultAccessUpdate, db: Session = Depends(get_db)):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    student.result_access = payload.result_access
    db.commit()
    return {"student_id": student.id, "result_access": student.result_access}


# =========================================================
# LOCATION / GEOFENCE
# =========================================================
@app.get("/api/admin/geofence", dependencies=[Depends(auth.require_admin)])
def get_geofence(db: Session = Depends(get_db)):
    g = db.query(models.Geofence).first()
    return {"latitude": g.latitude, "longitude": g.longitude, "radius_m": g.radius_m}


@app.post("/api/admin/geofence", dependencies=[Depends(auth.require_admin)])
def set_geofence(payload: schemas.GeofenceUpdate, db: Session = Depends(get_db)):
    g = db.query(models.Geofence).first()
    g.latitude = payload.latitude
    g.longitude = payload.longitude
    g.radius_m = payload.radius_m
    db.commit()
    return {"latitude": g.latitude, "longitude": g.longitude, "radius_m": g.radius_m}


def _distance_meters(lat1, lon1, lat2, lon2):
    """Haversine formula — used to check a teacher is within the school radius."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# =========================================================
# MESSAGES
# =========================================================
@app.get("/api/admin/messages", response_model=List[schemas.MessageOut], dependencies=[Depends(auth.require_admin)])
def list_messages(db: Session = Depends(get_db)):
    return db.query(models.Message).order_by(models.Message.created_at.desc()).all()


@app.post("/api/admin/messages", response_model=schemas.MessageOut, dependencies=[Depends(auth.require_admin)])
def send_message_to_teacher(payload: schemas.MessageCreate, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == payload.teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    msg = models.Message(teacher_id=payload.teacher_id, sender="admin", subject=payload.subject, body=payload.body)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


# =========================================================
# STUDENTS (used by finance/results/teacher endpoints above)
# =========================================================
@app.get("/api/admin/students", response_model=List[schemas.StudentOut], dependencies=[Depends(auth.require_admin)])
def list_students(db: Session = Depends(get_db)):
    return db.query(models.Student).all()


@app.post("/api/admin/students", response_model=schemas.StudentOut, dependencies=[Depends(auth.require_admin)])
def add_student(payload: schemas.StudentCreate, db: Session = Depends(get_db)):
    cls = _require_class(db, payload.class_name)
    code = f"S{_next_code_number(db, models.Student.student_code, 'S'):03d}"
    student = models.Student(
        student_code=code,
        name=_clean_text(payload.name, 80),
        class_name=cls.name,
        fees_due=_fee_total(db, cls.name),
        fees_paid=0,
        result_access="pending",
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


@app.put("/api/admin/students/{student_id}", response_model=schemas.StudentOut, dependencies=[Depends(auth.require_admin)])
def edit_student(student_id: int, payload: schemas.StudentEdit, db: Session = Depends(get_db)):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if payload.name is not None:
        student.name = _clean_text(payload.name, 80) or student.name
    if payload.class_name is not None:
        student.class_name = _require_class(db, payload.class_name).name
    student.fees_due = _fee_total(db, student.class_name)
    db.commit()
    db.refresh(student)
    return student


@app.delete("/api/admin/students/{student_id}", dependencies=[Depends(auth.require_admin)])
def delete_student(student_id: int, db: Session = Depends(get_db)):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    # Remove everything that belongs to this student first, otherwise the database
    # refuses the delete (payments are linked to the student).
    db.query(models.Payment).filter(models.Payment.student_id == student_id).delete(synchronize_session=False)
    db.query(models.Attendance).filter(models.Attendance.student_id == student_id).delete(synchronize_session=False)
    db.query(models.Result).filter(models.Result.student_id == student_id).delete(synchronize_session=False)
    db.delete(student)
    db.commit()
    return {"success": True}


@app.get("/")
def root():
    return {"status": "Richseed API is running"}


@app.get("/api/debug/db-info")
def db_info(db: Session = Depends(get_db)):
    """Open this address in a browser to see where your data is really stored.
    It shows no passwords or secrets, only whether storage is permanent."""
    is_postgres = str(engine.url).startswith("postgresql")
    return {
        "storage": "PERMANENT (Postgres / Neon) - data is safe across restarts" if is_postgres
                   else "TEMPORARY (SQLite file) - data is LOST whenever Render restarts",
        "DATABASE_URL_found": bool(os.environ.get("DATABASE_URL")),
        "AUTH_SECRET_set": auth.AUTH_SECRET_IS_SET,
        "PAYSTACK_SECRET_KEY_set": bool(os.environ.get("PAYSTACK_SECRET_KEY")),
    }


# =========================================================
# TEACHER PORTAL
# =========================================================

def _grade_for(total: float) -> str:
    if total >= 70:
        return "A"
    if total >= 60:
        return "B"
    if total >= 50:
        return "C"
    if total >= 45:
        return "D"
    if total >= 40:
        return "E"
    return "F"


@app.get("/api/teacher/list", response_model=List[schemas.TeacherPublicOut])
def teacher_public_list(db: Session = Depends(get_db)):
    """Names only — no PINs — used to populate the 'select your name' screen."""
    return db.query(models.Teacher).all()


@app.put("/api/teacher/{teacher_id}/class", dependencies=[Depends(auth.require_teacher)])
def set_teacher_class(teacher_id: int, payload: schemas.TeacherClassUpdate, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    teacher.class_name = _require_class(db, payload.class_name).name
    db.commit()
    return {"class_name": teacher.class_name}


@app.get("/api/teacher/{teacher_id}/subjects", dependencies=[Depends(auth.require_teacher)])
def get_teacher_subjects(teacher_id: int, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return {"subjects": [s for s in teacher.subjects.split(",") if s]}


@app.put("/api/teacher/{teacher_id}/subjects", dependencies=[Depends(auth.require_teacher)])
def set_teacher_subjects(teacher_id: int, payload: schemas.TeacherSubjectsUpdate, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    teacher.subjects = ",".join(s.strip() for s in payload.subjects if s.strip())
    db.commit()
    return {"subjects": [s for s in teacher.subjects.split(",") if s]}


@app.post("/api/teacher/{teacher_id}/login")
def teacher_login(teacher_id: int, payload: schemas.TeacherLoginCheck, db: Session = Depends(get_db)):
    key = "teacher-fail:%s" % teacher_id
    auth.guard(key, 5, 300)
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher or not hmac.compare_digest(str(payload.pin), str(teacher.pin)):
        auth.record(key)
        raise HTTPException(status_code=401, detail="Incorrect access code")
    auth.clear(key)
    return {
        "token": auth.make_token("teacher", teacher.id),
        "id": teacher.id,
        "teacher_code": teacher.teacher_code,
        "name": teacher.name,
        "subject": teacher.subject,
        "checked_in": teacher.checked_in,
        "check_in_time": teacher.check_in_time,
        "class_name": teacher.class_name,
    }


@app.post("/api/teacher/{teacher_id}/checkin", dependencies=[Depends(auth.require_teacher)])
def teacher_checkin(teacher_id: int, payload: schemas.CheckInRequest, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    geo = db.query(models.Geofence).first()
    distance = _distance_meters(payload.latitude, payload.longitude, geo.latitude, geo.longitude)
    if distance > geo.radius_m:
        raise HTTPException(
            status_code=403,
            detail=f"You are {int(distance)}m from the school (must be within {geo.radius_m}m). Check-in blocked.",
        )
    teacher.checked_in = True
    teacher.check_in_time = datetime.utcnow().strftime("%I:%M %p")
    db.commit()
    return {"checked_in": True, "check_in_time": teacher.check_in_time, "distance_m": int(distance)}


@app.get("/api/teacher/{teacher_id}/students", response_model=List[schemas.StudentOut], dependencies=[Depends(auth.require_teacher)])
def teacher_students(teacher_id: int, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return _teacher_students(db, teacher)


@app.post("/api/teacher/{teacher_id}/students", response_model=schemas.StudentOut, dependencies=[Depends(auth.require_teacher)])
def teacher_register_student(teacher_id: int, payload: schemas.StudentCreate, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    cls = _require_class(db, payload.class_name or teacher.class_name)
    code = f"S{_next_code_number(db, models.Student.student_code, 'S'):03d}"
    student = models.Student(
        student_code=code,
        name=_clean_text(payload.name, 80),
        class_name=cls.name,
        fees_due=_fee_total(db, cls.name),
        fees_paid=0,
        result_access="pending",
        teacher_id=teacher_id,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


@app.post("/api/teacher/{teacher_id}/students/{student_id}/attendance", dependencies=[Depends(auth.require_teacher)])
def mark_student_attendance(teacher_id: int, student_id: int, payload: schemas.AttendanceMark, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not teacher or not student or student not in _teacher_students(db, teacher):
        raise HTTPException(status_code=404, detail="Student not found in this teacher's class")
    today = date.today().isoformat()
    record = db.query(models.Attendance).filter(models.Attendance.student_id == student_id, models.Attendance.date == today).first()
    now_time = datetime.utcnow().strftime("%I:%M %p")
    is_late = datetime.utcnow().hour >= 8  # after 8am counts as late — adjust as needed
    if record:
        record.status = payload.status
        record.time_in = now_time if payload.status == "Present" else None
        record.late = is_late if payload.status == "Present" else False
    else:
        record = models.Attendance(
            student_id=student_id, date=today, status=payload.status,
            time_in=now_time if payload.status == "Present" else None,
            late=is_late if payload.status == "Present" else False,
        )
        db.add(record)
    db.commit()
    return {"student_id": student_id, "date": today, "status": payload.status, "time_in": record.time_in, "late": record.late}


@app.get("/api/teacher/{teacher_id}/students/{student_id}/attendance", dependencies=[Depends(auth.require_teacher)])
def get_student_attendance(teacher_id: int, student_id: int, db: Session = Depends(get_db)):
    records = db.query(models.Attendance).filter(models.Attendance.student_id == student_id).order_by(models.Attendance.date.desc()).all()
    return [{"date": r.date, "status": r.status, "time_in": r.time_in, "late": r.late} for r in records]


@app.get("/api/teacher/{teacher_id}/results/{student_id}", dependencies=[Depends(auth.require_teacher)])
def get_student_results(teacher_id: int, student_id: int, db: Session = Depends(get_db)):
    results = db.query(models.Result).filter(models.Result.student_id == student_id).all()
    return [{"subject": r.subject, "ca": r.ca, "exam": r.exam, "total": r.total, "grade": r.grade} for r in results]


@app.post("/api/teacher/{teacher_id}/results", dependencies=[Depends(auth.require_teacher)])
def save_result(teacher_id: int, payload: schemas.ResultEntry, db: Session = Depends(get_db)):
    total = payload.ca + payload.exam
    grade = _grade_for(total)
    existing = db.query(models.Result).filter(
        models.Result.student_id == payload.student_id, models.Result.subject == payload.subject
    ).first()
    if existing:
        existing.ca = payload.ca
        existing.exam = payload.exam
        existing.total = total
        existing.grade = grade
    else:
        existing = models.Result(student_id=payload.student_id, subject=payload.subject, ca=payload.ca, exam=payload.exam, total=total, grade=grade)
        db.add(existing)
    db.commit()
    return {"subject": payload.subject, "ca": payload.ca, "exam": payload.exam, "total": total, "grade": grade}


@app.get("/api/teacher/{teacher_id}/messages", dependencies=[Depends(auth.require_teacher)])
def teacher_messages(teacher_id: int, db: Session = Depends(get_db)):
    msgs = db.query(models.Message).filter(models.Message.teacher_id == teacher_id).order_by(models.Message.created_at.asc()).all()
    return [{"id": m.id, "sender": m.sender, "subject": m.subject, "body": m.body, "created_at": m.created_at.isoformat()} for m in msgs]


@app.post("/api/teacher/{teacher_id}/messages/reply", dependencies=[Depends(auth.require_teacher)])
def teacher_reply(teacher_id: int, payload: schemas.TeacherMessageReply, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    msg = models.Message(teacher_id=teacher_id, sender="teacher", subject="Reply", body=payload.body)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"id": msg.id, "sender": msg.sender, "subject": msg.subject, "body": msg.body}


# =========================================================
# PARENT PORTAL
# =========================================================
import hashlib
import secrets


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


@app.post("/api/parent/signup")
def parent_signup(payload: schemas.ParentSignup, db: Session = Depends(get_db)):
    if re.search(r'[<>"\'`&\\\s]', payload.email) or "@" not in payload.email or len(payload.email) > 200:
        raise HTTPException(status_code=400, detail="Please enter a valid email address")
    if len(payload.children_names) > 10:
        raise HTTPException(status_code=400, detail="Too many children on one account")
    existing = db.query(models.Parent).filter(models.Parent.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    salt = secrets.token_hex(16)
    parent = models.Parent(email=payload.email, salt=salt, password_hash=_hash_password(payload.password, salt))
    db.add(parent)
    db.commit()
    db.refresh(parent)

    wanted = []
    for ch in payload.children:
        wanted.append((_clean_text(ch.name, 80), ch.class_name))
    for nm in payload.children_names:
        wanted.append((_clean_text(nm, 80), None))
    linked = []
    next_num = _next_code_number(db, models.Student.student_code, 'S') - 1
    for name, class_name in wanted:
        if not name:
            continue
        # If the school already registered this child (same name, no parent yet), link that record.
        existing_student = next((st for st in db.query(models.Student).filter(models.Student.parent_id.is_(None)).all()
                                 if _norm(st.name) == _norm(name)), None)
        if existing_student:
            existing_student.parent_id = parent.id
            existing_student.fees_due = _fee_total(db, existing_student.class_name)
            linked.append(existing_student)
        else:
            cls = _class_row(db, class_name)
            if not cls:
                db.rollback()  # undo any links made so far, then remove the half-made account
                db.delete(parent)
                db.commit()
                raise HTTPException(status_code=400, detail="Please choose the class for %s" % name)
            next_num += 1
            new_student = models.Student(
                student_code=f"S{next_num:03d}", name=name, class_name=cls.name,
                fees_due=_fee_total(db, cls.name), fees_paid=0, result_access="pending", parent_id=parent.id,
            )
            db.add(new_student)
            linked.append(new_student)
    db.commit()
    for s in linked:
        db.refresh(s)

    return {
        "token": auth.make_token("parent", parent.id),
        "parent_id": parent.id,
        "email": parent.email,
        "children": [{"id": s.id, "student_code": s.student_code, "name": s.name, "class_name": s.class_name} for s in linked],
    }


@app.post("/api/parent/login")
def parent_login(payload: schemas.ParentLogin, db: Session = Depends(get_db)):
    key = "parent-fail:%s" % payload.email.lower()
    auth.guard(key, 6, 300)
    parent = db.query(models.Parent).filter(models.Parent.email == payload.email).first()
    if not parent or not hmac.compare_digest(_hash_password(payload.password, parent.salt), parent.password_hash):
        auth.record(key)
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    auth.clear(key)
    children = db.query(models.Student).filter(models.Student.parent_id == parent.id).all()
    return {
        "token": auth.make_token("parent", parent.id),
        "parent_id": parent.id,
        "email": parent.email,
        "children": [{"id": s.id, "student_code": s.student_code, "name": s.name, "class_name": s.class_name} for s in children],
    }


@app.post("/api/parent/reset-password")
def parent_reset_password(payload: schemas.ParentResetPassword, db: Session = Depends(get_db)):
    """
    No email service is set up yet, so as a lightweight verification step we
    require the parent to also know one of their child's Student IDs — this
    proves they're the actual parent without needing to send an email.
    """
    key = "reset-fail:%s" % payload.email.lower()
    auth.guard(key, 5, 600)
    parent = db.query(models.Parent).filter(models.Parent.email == payload.email).first()
    if not parent:
        raise HTTPException(status_code=404, detail="No account found with that email")
    child = db.query(models.Student).filter(
        models.Student.parent_id == parent.id,
        models.Student.student_code == payload.student_code.upper()
    ).first()
    if not child:
        auth.record(key)
        raise HTTPException(status_code=401, detail="That Student ID doesn't match a child on this account")
    if len(payload.new_password) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters")
    salt = secrets.token_hex(16)
    parent.salt = salt
    parent.password_hash = _hash_password(payload.new_password, salt)
    db.commit()
    return {"success": True}


@app.get("/api/parent/{parent_id}/children", dependencies=[Depends(auth.require_parent_self)])
def parent_children(parent_id: int, db: Session = Depends(get_db)):
    children = db.query(models.Student).filter(models.Student.parent_id == parent_id).all()
    return [{"id": s.id, "student_code": s.student_code, "name": s.name, "class_name": s.class_name} for s in children]


@app.get("/api/parent/children/{student_id}/overview", dependencies=[Depends(auth.require_child_access)])
def child_overview(student_id: int, db: Session = Depends(get_db)):
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    results = db.query(models.Result).filter(models.Result.student_id == student_id).all()
    attendance = db.query(models.Attendance).filter(models.Attendance.student_id == student_id).all()
    avg = round(sum(r.total for r in results) / len(results)) if results else 0
    att_rate = round(100 * sum(1 for a in attendance if a.status == "Present") / len(attendance)) if attendance else 0
    return {
        "name": s.name, "class_name": s.class_name,
        "average_score": avg, "attendance_rate": att_rate,
        "fees_due": s.fees_due, "fees_paid": s.fees_paid,
        "recent_results": [{"subject": r.subject, "total": r.total, "grade": r.grade} for r in results],
    }


@app.get("/api/parent/children/{student_id}/attendance", dependencies=[Depends(auth.require_child_access)])
def child_attendance(student_id: int, db: Session = Depends(get_db)):
    records = db.query(models.Attendance).filter(models.Attendance.student_id == student_id).order_by(models.Attendance.date.desc()).all()
    present = sum(1 for r in records if r.status == "Present")
    return {
        "present": present, "absent": len(records) - present,
        "rate": round(100 * present / len(records)) if records else 0,
        "records": [{"date": r.date, "status": r.status, "time_in": r.time_in} for r in records],
    }


@app.get("/api/parent/fees", dependencies=[Depends(auth.require_parent)])
def parent_fee_structure(db: Session = Depends(get_db)):
    """Same fee structure the admin set — shared across the whole school."""
    items = db.query(models.FeeItem).all()
    return [{"name": f.name, "amount": f.amount} for f in items]


@app.get("/api/parent/children/{student_id}/fees", dependencies=[Depends(auth.require_child_access)])
def child_fee_breakdown(student_id: int, db: Session = Depends(get_db)):
    """The fees for THIS child: the school's common fees plus the fee for the child's class."""
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    out = []
    c = _class_row(db, s.class_name)
    if c and c.fee:
        out.append({"name": "%s fee" % c.name, "amount": c.fee})
    out += [{"name": f.name, "amount": f.amount} for f in db.query(models.FeeItem).all()]
    return out


@app.get("/api/parent/children/{student_id}/results", dependencies=[Depends(auth.require_child_access)])
def child_results(student_id: int, db: Session = Depends(get_db)):
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    lock = db.query(models.ResultLock).first()
    unlocked = (not lock.locked) and s.result_access == "approved"
    if not unlocked:
        return {"unlocked": False, "results": [], "reason": "global" if lock.locked else "student"}
    results = db.query(models.Result).filter(models.Result.student_id == student_id).all()
    return {"unlocked": True, "results": [{"subject": r.subject, "ca": r.ca, "exam": r.exam, "total": r.total, "grade": r.grade} for r in results]}


# =========================================================
# PAYMENTS (Paystack) - the server checks every payment itself
# =========================================================
class PaymentVerifyIn(schemas.BaseModel):
    reference: str


def _paystack_verify(reference: str) -> dict:
    """Ask Paystack directly whether this payment really happened."""
    key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Payment confirmation is not set up yet")
    req = urllib.request.Request(
        "https://api.paystack.co/transaction/verify/" + urllib.parse.quote(reference, safe=""),
        headers={"Authorization": "Bearer " + key, "User-Agent": "RichseedSchool/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise HTTPException(status_code=404, detail="Paystack has no payment with that reference")
        try:
            print("PAYSTACK VERIFY ERROR", e.code, e.read()[:300])
        except Exception:
            print("PAYSTACK VERIFY ERROR", e.code)
        raise HTTPException(status_code=502, detail="Could not reach Paystack, try again in a moment")
    except Exception as e:
        print("PAYSTACK VERIFY EXCEPTION", repr(e))
        raise HTTPException(status_code=502, detail="Could not reach Paystack, try again in a moment")


def _record_payment(db: Session, reference: str, student_id: int, amount_naira: float, source: str):
    """Save a payment exactly once. Returns (payment, newly_recorded)."""
    existing = db.query(models.Payment).filter(models.Payment.reference == reference).first()
    if existing:
        return existing, False
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    pay = models.Payment(reference=reference, student_id=student_id, amount=amount_naira, source=source)
    db.add(pay)
    student.fees_paid += amount_naira
    try:
        db.commit()
    except IntegrityError:
        # the other path (browser confirmation vs Paystack's webhook) won the race
        db.rollback()
        return db.query(models.Payment).filter(models.Payment.reference == reference).first(), False
    return pay, True


@app.post("/api/parent/children/{student_id}/verify-payment", dependencies=[Depends(auth.require_child_access)])
def verify_payment(student_id: int, payload: PaymentVerifyIn, db: Session = Depends(get_db)):
    body = _paystack_verify(payload.reference.strip())
    data = body.get("data") or {}
    if not body.get("status") or data.get("status") != "success":
        raise HTTPException(status_code=402, detail="Payment was not successful (status: %s)" % data.get("status", "unknown"))
    if data.get("currency") != "NGN":
        raise HTTPException(status_code=400, detail="Unexpected currency")
    meta = data.get("metadata")
    meta_sid = meta.get("student_id") if isinstance(meta, dict) else None
    if str(meta_sid) != str(student_id):
        raise HTTPException(status_code=400, detail="This payment does not belong to this student")
    amount = float(data["amount"]) / 100.0
    pay, created = _record_payment(db, data["reference"], student_id, amount, "verify")
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    return {"recorded": created, "amount": pay.amount, "fees_due": student.fees_due, "fees_paid": student.fees_paid}


@app.post("/api/paystack/webhook")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    """Paystack calls this itself after a payment, so a payment is still
    recorded even if the parent closes the page or the server was asleep."""
    key = os.environ.get("PAYSTACK_SECRET_KEY")
    if not key:
        raise HTTPException(status_code=503, detail="Payment confirmation is not set up yet")
    raw = await request.body()
    expected = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("x-paystack-signature", "")):
        raise HTTPException(status_code=401, detail="Bad signature")
    event = json.loads(raw or b"{}")
    if event.get("event") == "charge.success":
        data = event.get("data") or {}
        meta = data.get("metadata")
        sid = meta.get("student_id") if isinstance(meta, dict) else None
        if data.get("status") == "success" and data.get("currency") == "NGN" and sid is not None:
            try:
                _record_payment(db, data["reference"], int(sid), float(data["amount"]) / 100.0, "webhook")
            except HTTPException:
                pass  # unknown student etc. - still answer 200 so Paystack stops retrying
    return {"received": True}


# =========================================================
# STUDENT PORTAL (public lookup by Student ID, no login)
# =========================================================
@app.get("/api/student/lookup/{query}")
def student_lookup(query: str, request: Request, db: Session = Depends(get_db)):
    ip = "lookup:" + auth.client_ip(request)
    auth.guard(ip, 20, 300)
    auth.record(ip)
    query = query.strip()
    # Exact Student ID only (names are not searchable, so nobody can browse other children)
    s = db.query(models.Student).filter(models.Student.student_code == query.upper()).first()
    if not s:
        raise HTTPException(status_code=404, detail="No student found with that Student ID")
    lock = db.query(models.ResultLock).first()
    unlocked = (not lock.locked) and s.result_access == "approved"
    results = db.query(models.Result).filter(models.Result.student_id == s.id).all()
    avg = round(sum(r.total for r in results) / len(results)) if results else 0
    return {
        "student_code": s.student_code,
        "name": s.name,
        "class_name": s.class_name,
        "average_score": avg,
        "unlocked": unlocked,
        "reason": None if unlocked else ("global" if lock.locked else "student"),
        "results": [{"subject": r.subject, "ca": r.ca, "exam": r.exam, "total": r.total, "grade": r.grade} for r in results] if unlocked else [],
    }


# =========================================================
# CONTACT + ADMISSION FORMS (public to send, admin to read)
# =========================================================
from pydantic import BaseModel as _BM, Field as _F


class ContactIn(_BM):
    name: str = _F(min_length=1, max_length=120)
    email: str = _F(min_length=3, max_length=200)
    subject: str = _F(default="", max_length=200)
    message: str = _F(min_length=1, max_length=3000)


class ApplyIn(_BM):
    child_name: str = _F(min_length=1, max_length=120)
    parent_name: str = _F(default="", max_length=120)
    email: str = _F(default="", max_length=200)
    phone: str = _F(default="", max_length=40)
    programme: str = _F(default="", max_length=120)
    message: str = _F(default="", max_length=3000)


def _limit_public(request: Request):
    key = "public-form:" + auth.client_ip(request)
    auth.guard(key, 6, 3600)
    auth.record(key)


@app.post("/api/public/contact")
def public_contact(payload: ContactIn, request: Request, db: Session = Depends(get_db)):
    _limit_public(request)
    db.add(models.Inquiry(kind="contact", name=payload.name.strip(), email=payload.email.strip(),
                          subject=payload.subject.strip(), message=payload.message.strip()))
    db.commit()
    return {"received": True}


@app.post("/api/public/apply")
def public_apply(payload: ApplyIn, request: Request, db: Session = Depends(get_db)):
    _limit_public(request)
    db.add(models.Inquiry(kind="application", name=payload.parent_name.strip(), email=payload.email.strip(),
                          phone=payload.phone.strip(), subject=payload.programme.strip(),
                          message=payload.message.strip(), extra=payload.child_name.strip()))
    db.commit()
    return {"received": True}


@app.get("/api/admin/inbox", dependencies=[Depends(auth.require_admin)])
def admin_inbox(db: Session = Depends(get_db)):
    rows = db.query(models.Inquiry).order_by(models.Inquiry.id.desc()).limit(200).all()
    return [{"id": r.id, "kind": r.kind, "name": r.name, "email": r.email, "phone": r.phone,
             "subject": r.subject, "message": r.message, "child": r.extra,
             "created": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""} for r in rows]


@app.delete("/api/admin/inbox/{item_id}", dependencies=[Depends(auth.require_admin)])
def admin_inbox_delete(item_id: int, db: Session = Depends(get_db)):
    r = db.query(models.Inquiry).filter(models.Inquiry.id == item_id).first()
    if r:
        db.delete(r)
        db.commit()
    return {"success": True}


# =========================================================
# ADMISSION PRICES PER CLASS (public to read, admin to change)
# =========================================================
class ProgrammeFeeIn(_BM):
    fee: float = _F(ge=0, le=100000000)


@app.get("/api/public/programmes")
def public_programmes(db: Session = Depends(get_db)):
    rows = db.query(models.ProgrammeFee).order_by(models.ProgrammeFee.id).all()
    return [{"n": r.id, "name": r.name, "from": r.age_text, "fee": r.fee} for r in rows]


@app.put("/api/admin/programmes/{stage}", dependencies=[Depends(auth.require_admin)])
def admin_set_programme_fee(stage: int, payload: ProgrammeFeeIn, db: Session = Depends(get_db)):
    row = db.query(models.ProgrammeFee).filter(models.ProgrammeFee.id == stage).first()
    if not row:
        raise HTTPException(status_code=404, detail="Class level not found")
    row.fee = payload.fee
    db.commit()
    return {"n": row.id, "name": row.name, "from": row.age_text, "fee": row.fee}


# =========================================================
# CLASSES (Nursery 1 ... SS 3), each with its own fee
# =========================================================
class ClassIn(_BM):
    name: str = _F(min_length=1, max_length=40)
    fee: float = _F(default=0, ge=0, le=100000000)


class ClassEdit(_BM):
    name: Optional[str] = _F(default=None, max_length=40)
    fee: Optional[float] = _F(default=None, ge=0, le=100000000)


@app.get("/api/public/classes")
def public_classes(db: Session = Depends(get_db)):
    rows = db.query(models.ClassLevel).order_by(models.ClassLevel.position, models.ClassLevel.id).all()
    return [{"id": r.id, "name": r.name} for r in rows]


@app.get("/api/admin/classes", dependencies=[Depends(auth.require_admin)])
def admin_classes(db: Session = Depends(get_db)):
    rows = db.query(models.ClassLevel).order_by(models.ClassLevel.position, models.ClassLevel.id).all()
    students = db.query(models.Student).all()
    return [{"id": r.id, "name": r.name, "fee": r.fee,
             "students": sum(1 for st in students if _norm(st.class_name) == _norm(r.name))} for r in rows]


@app.post("/api/admin/classes", dependencies=[Depends(auth.require_admin)])
def admin_add_class(payload: ClassIn, db: Session = Depends(get_db)):
    name = _clean_text(payload.name, 40)
    if not name:
        raise HTTPException(status_code=400, detail="Enter a class name")
    if _class_row(db, name):
        raise HTTPException(status_code=400, detail="That class already exists")
    pos = (db.query(models.ClassLevel).count() or 0) + 1
    row = models.ClassLevel(name=name, position=pos, fee=payload.fee)
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name, "fee": row.fee, "students": 0}


@app.put("/api/admin/classes/{class_id}", dependencies=[Depends(auth.require_admin)])
def admin_edit_class(class_id: int, payload: ClassEdit, db: Session = Depends(get_db)):
    row = db.query(models.ClassLevel).filter(models.ClassLevel.id == class_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Class not found")
    if payload.name is not None:
        new = _clean_text(payload.name, 40)
        other = _class_row(db, new)
        if not new or (other and other.id != row.id):
            raise HTTPException(status_code=400, detail="Choose a different class name")
        old = row.name
        row.name = new
        for st in db.query(models.Student).all():
            if _norm(st.class_name) == _norm(old):
                st.class_name = new
        for t in db.query(models.Teacher).all():
            if _norm(t.class_name) == _norm(old):
                t.class_name = new
    if payload.fee is not None:
        row.fee = payload.fee
    db.commit()
    _sync_fees(db)
    return {"id": row.id, "name": row.name, "fee": row.fee}


@app.delete("/api/admin/classes/{class_id}", dependencies=[Depends(auth.require_admin)])
def admin_delete_class(class_id: int, db: Session = Depends(get_db)):
    row = db.query(models.ClassLevel).filter(models.ClassLevel.id == class_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Class not found")
    n = sum(1 for st in db.query(models.Student).all() if _norm(st.class_name) == _norm(row.name))
    if n:
        raise HTTPException(status_code=400, detail="%d student(s) are in this class. Move them to another class first." % n)
    db.delete(row)
    db.commit()
    return {"success": True}
