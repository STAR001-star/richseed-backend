import random
import io
import csv
import math
from datetime import datetime, date
from typing import List

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from . import models, schemas
from .database import engine, get_db, Base

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Richseed Private School API")

# Allow the frontend (running on any origin during development) to call this API.
# Once deployed, replace "*" with your actual site URL for safety.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Startup: seed default rows so the API is usable immediately ----------
@app.on_event("startup")
def seed_defaults():
    db = next(get_db())
    if not db.query(models.AdminSettings).first():
        db.add(models.AdminSettings(id=1, pin="1234"))
    if not db.query(models.ResultLock).first():
        db.add(models.ResultLock(id=1, locked=True))
    if not db.query(models.Geofence).first():
        db.add(models.Geofence(id=1, latitude=7.4341, longitude=3.9357, radius_m=200))
    if not db.query(models.FeeItem).first():
        db.add_all([
            models.FeeItem(name="Tuition Fee", amount=45000),
            models.FeeItem(name="Development Levy", amount=8000),
            models.FeeItem(name="Exam Fee", amount=5000),
            models.FeeItem(name="Uniform", amount=6000),
            models.FeeItem(name="ICT Fee", amount=3000),
        ])
    if not db.query(models.Teacher).first():
        db.add_all([
            models.Teacher(teacher_code="T001", name="Mrs. Adaeze Okafor", subject="Mathematics", pin="2847"),
            models.Teacher(teacher_code="T002", name="Mr. Emeka Nwachukwu", subject="English Language", pin="3619"),
            models.Teacher(teacher_code="T003", name="Miss Fatima Bello", subject="Basic Science", pin="7134"),
        ])
        db.commit()
    if not db.query(models.Student).first():
        first_teacher = db.query(models.Teacher).first()
        db.add_all([
            models.Student(student_code="S001", name="Chukwuemeka Obi", class_name="JSS 2A",
                            fees_due=67000, fees_paid=45000, result_access="pending",
                            teacher_id=first_teacher.id if first_teacher else None),
            models.Student(student_code="S002", name="Amina Yusuf", class_name="JSS 2A",
                            fees_due=67000, fees_paid=67000, result_access="approved",
                            teacher_id=first_teacher.id if first_teacher else None),
        ])
    db.commit()
    db.close()


# =========================================================
# AUTH
# =========================================================
@app.post("/api/admin/login")
def admin_login(payload: schemas.PinCheck, db: Session = Depends(get_db)):
    settings = db.query(models.AdminSettings).first()
    if payload.pin != settings.pin:
        raise HTTPException(status_code=401, detail="Incorrect PIN")
    return {"success": True, "message": "Welcome, Admin"}


@app.post("/api/admin/change-pin")
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
@app.get("/api/admin/overview")
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


@app.get("/api/admin/teachers", response_model=List[schemas.TeacherOut])
def list_teachers(db: Session = Depends(get_db)):
    return db.query(models.Teacher).all()


@app.post("/api/admin/teachers", response_model=schemas.TeacherOut)
def enroll_teacher(payload: schemas.TeacherCreate, db: Session = Depends(get_db)):
    count = db.query(models.Teacher).count()
    if count >= MAX_TEACHERS:
        raise HTTPException(status_code=400, detail=f"Maximum of {MAX_TEACHERS} teachers reached")
    code = f"T{count + 1:03d}"
    pin = f"{random.randint(0, 9999):04d}"
    teacher = models.Teacher(teacher_code=code, name=payload.name, subject=payload.subject, pin=pin)
    db.add(teacher)
    db.commit()
    db.refresh(teacher)
    return teacher


@app.delete("/api/admin/teachers/{teacher_id}")
def remove_teacher(teacher_id: int, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    db.delete(teacher)
    db.commit()
    return {"success": True}


@app.put("/api/admin/teachers/{teacher_id}", response_model=schemas.TeacherOut)
def edit_teacher(teacher_id: int, payload: schemas.TeacherCreate, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    teacher.name = payload.name
    teacher.subject = payload.subject
    db.commit()
    db.refresh(teacher)
    return teacher


@app.get("/api/admin/teachers/{teacher_id}/profile")
def teacher_profile(teacher_id: int, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    students = db.query(models.Student).filter(models.Student.teacher_id == teacher_id).all()
    return {
        "teacher": schemas.TeacherOut.from_orm(teacher),
        "students": [schemas.StudentOut.from_orm(s) for s in students],
    }


@app.get("/api/admin/teachers/export.csv")
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
@app.get("/api/admin/fees", response_model=List[schemas.FeeItemOut])
def get_fee_structure(db: Session = Depends(get_db)):
    return db.query(models.FeeItem).all()


@app.post("/api/admin/fees", response_model=schemas.FeeItemOut)
def add_fee_item(payload: schemas.FeeItemCreate, db: Session = Depends(get_db)):
    item = models.FeeItem(name=payload.name, amount=payload.amount)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.put("/api/admin/fees/{fee_id}", response_model=schemas.FeeItemOut)
def edit_fee_item(fee_id: int, payload: schemas.FeeItemCreate, db: Session = Depends(get_db)):
    item = db.query(models.FeeItem).filter(models.FeeItem.id == fee_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Fee item not found")
    item.name = payload.name
    item.amount = payload.amount
    db.commit()
    db.refresh(item)
    return item


@app.delete("/api/admin/fees/{fee_id}")
def delete_fee_item(fee_id: int, db: Session = Depends(get_db)):
    item = db.query(models.FeeItem).filter(models.FeeItem.id == fee_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Fee item not found")
    db.delete(item)
    db.commit()
    return {"success": True}


@app.get("/api/admin/payments")
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
@app.get("/api/admin/results/lock")
def get_result_lock(db: Session = Depends(get_db)):
    lock = db.query(models.ResultLock).first()
    return {"locked": lock.locked}


@app.post("/api/admin/results/lock")
def set_result_lock(locked: bool, db: Session = Depends(get_db)):
    lock = db.query(models.ResultLock).first()
    lock.locked = locked
    db.commit()
    return {"locked": lock.locked}


@app.post("/api/admin/students/{student_id}/result-access")
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
@app.get("/api/admin/geofence")
def get_geofence(db: Session = Depends(get_db)):
    g = db.query(models.Geofence).first()
    return {"latitude": g.latitude, "longitude": g.longitude, "radius_m": g.radius_m}


@app.post("/api/admin/geofence")
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
@app.get("/api/admin/messages", response_model=List[schemas.MessageOut])
def list_messages(db: Session = Depends(get_db)):
    return db.query(models.Message).order_by(models.Message.created_at.desc()).all()


@app.post("/api/admin/messages", response_model=schemas.MessageOut)
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
@app.get("/api/admin/students", response_model=List[schemas.StudentOut])
def list_students(db: Session = Depends(get_db)):
    return db.query(models.Student).all()


@app.post("/api/admin/students", response_model=schemas.StudentOut)
def add_student(payload: schemas.StudentCreate, db: Session = Depends(get_db)):
    count = db.query(models.Student).count()
    code = f"S{count + 1:03d}"
    student = models.Student(
        student_code=code,
        name=payload.name,
        class_name=payload.class_name or "JSS 2A",
        fees_due=payload.fees_due if payload.fees_due is not None else 67000,
        fees_paid=0,
        result_access="pending",
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


@app.put("/api/admin/students/{student_id}", response_model=schemas.StudentOut)
def edit_student(student_id: int, payload: schemas.StudentEdit, db: Session = Depends(get_db)):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if payload.name is not None:
        student.name = payload.name
    if payload.class_name is not None:
        student.class_name = payload.class_name
    if payload.fees_due is not None:
        student.fees_due = payload.fees_due
    db.commit()
    db.refresh(student)
    return student


@app.delete("/api/admin/students/{student_id}")
def delete_student(student_id: int, db: Session = Depends(get_db)):
    student = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    db.delete(student)
    db.commit()
    return {"success": True}


@app.get("/")
def root():
    return {"status": "Richseed API is running"}


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


@app.post("/api/teacher/{teacher_id}/login")
def teacher_login(teacher_id: int, payload: schemas.TeacherLoginCheck, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher or payload.pin != teacher.pin:
        raise HTTPException(status_code=401, detail="Incorrect access code")
    return {
        "id": teacher.id,
        "teacher_code": teacher.teacher_code,
        "name": teacher.name,
        "subject": teacher.subject,
        "checked_in": teacher.checked_in,
        "check_in_time": teacher.check_in_time,
    }


@app.post("/api/teacher/{teacher_id}/checkin")
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


@app.get("/api/teacher/{teacher_id}/students", response_model=List[schemas.StudentOut])
def teacher_students(teacher_id: int, db: Session = Depends(get_db)):
    return db.query(models.Student).filter(models.Student.teacher_id == teacher_id).all()


@app.post("/api/teacher/{teacher_id}/students", response_model=schemas.StudentOut)
def teacher_register_student(teacher_id: int, payload: schemas.StudentCreate, db: Session = Depends(get_db)):
    teacher = db.query(models.Teacher).filter(models.Teacher.id == teacher_id).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    count = db.query(models.Student).count()
    code = f"S{count + 1:03d}"
    student = models.Student(
        student_code=code,
        name=payload.name,
        class_name=payload.class_name or "JSS 2A",
        fees_due=payload.fees_due if payload.fees_due is not None else 67000,
        fees_paid=0,
        result_access="pending",
        teacher_id=teacher_id,
    )
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


@app.post("/api/teacher/{teacher_id}/students/{student_id}/attendance")
def mark_student_attendance(teacher_id: int, student_id: int, payload: schemas.AttendanceMark, db: Session = Depends(get_db)):
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.teacher_id == teacher_id).first()
    if not student:
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


@app.get("/api/teacher/{teacher_id}/students/{student_id}/attendance")
def get_student_attendance(teacher_id: int, student_id: int, db: Session = Depends(get_db)):
    records = db.query(models.Attendance).filter(models.Attendance.student_id == student_id).order_by(models.Attendance.date.desc()).all()
    return [{"date": r.date, "status": r.status, "time_in": r.time_in, "late": r.late} for r in records]


@app.get("/api/teacher/{teacher_id}/results/{student_id}")
def get_student_results(teacher_id: int, student_id: int, db: Session = Depends(get_db)):
    results = db.query(models.Result).filter(models.Result.student_id == student_id).all()
    return [{"subject": r.subject, "ca": r.ca, "exam": r.exam, "total": r.total, "grade": r.grade} for r in results]


@app.post("/api/teacher/{teacher_id}/results")
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


@app.get("/api/teacher/{teacher_id}/messages")
def teacher_messages(teacher_id: int, db: Session = Depends(get_db)):
    msgs = db.query(models.Message).filter(models.Message.teacher_id == teacher_id).order_by(models.Message.created_at.asc()).all()
    return [{"id": m.id, "sender": m.sender, "subject": m.subject, "body": m.body, "created_at": m.created_at.isoformat()} for m in msgs]


@app.post("/api/teacher/{teacher_id}/messages/reply")
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
    existing = db.query(models.Parent).filter(models.Parent.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    salt = secrets.token_hex(16)
    parent = models.Parent(email=payload.email, salt=salt, password_hash=_hash_password(payload.password, salt))
    db.add(parent)
    db.commit()
    db.refresh(parent)

    linked = []
    count = db.query(models.Student).count()
    for name in payload.children_names:
        name = name.strip()
        if not name:
            continue
        # If a student with this exact name already exists and has no parent yet, link them.
        existing_student = db.query(models.Student).filter(models.Student.name == name, models.Student.parent_id.is_(None)).first()
        if existing_student:
            existing_student.parent_id = parent.id
            linked.append(existing_student)
        else:
            count += 1
            new_student = models.Student(
                student_code=f"S{count:03d}", name=name, class_name="JSS 2A",
                fees_due=67000, fees_paid=0, result_access="pending", parent_id=parent.id,
            )
            db.add(new_student)
            linked.append(new_student)
    db.commit()
    for s in linked:
        db.refresh(s)

    return {
        "parent_id": parent.id,
        "email": parent.email,
        "children": [{"id": s.id, "student_code": s.student_code, "name": s.name, "class_name": s.class_name} for s in linked],
    }


@app.post("/api/parent/login")
def parent_login(payload: schemas.ParentLogin, db: Session = Depends(get_db)):
    parent = db.query(models.Parent).filter(models.Parent.email == payload.email).first()
    if not parent or _hash_password(payload.password, parent.salt) != parent.password_hash:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    children = db.query(models.Student).filter(models.Student.parent_id == parent.id).all()
    return {
        "parent_id": parent.id,
        "email": parent.email,
        "children": [{"id": s.id, "student_code": s.student_code, "name": s.name, "class_name": s.class_name} for s in children],
    }


@app.get("/api/parent/{parent_id}/children")
def parent_children(parent_id: int, db: Session = Depends(get_db)):
    children = db.query(models.Student).filter(models.Student.parent_id == parent_id).all()
    return [{"id": s.id, "student_code": s.student_code, "name": s.name, "class_name": s.class_name} for s in children]


@app.get("/api/parent/children/{student_id}/overview")
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


@app.get("/api/parent/children/{student_id}/attendance")
def child_attendance(student_id: int, db: Session = Depends(get_db)):
    records = db.query(models.Attendance).filter(models.Attendance.student_id == student_id).order_by(models.Attendance.date.desc()).all()
    present = sum(1 for r in records if r.status == "Present")
    return {
        "present": present, "absent": len(records) - present,
        "rate": round(100 * present / len(records)) if records else 0,
        "records": [{"date": r.date, "status": r.status, "time_in": r.time_in} for r in records],
    }


@app.get("/api/parent/fees")
def parent_fee_structure(db: Session = Depends(get_db)):
    """Same fee structure the admin set — shared across the whole school."""
    items = db.query(models.FeeItem).all()
    return [{"name": f.name, "amount": f.amount} for f in items]


@app.get("/api/parent/children/{student_id}/results")
def child_results(student_id: int, db: Session = Depends(get_db)):
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    lock = db.query(models.ResultLock).first()
    unlocked = (not lock.locked) and s.result_access == "approved"
    if not unlocked:
        return {"unlocked": False, "results": []}
    results = db.query(models.Result).filter(models.Result.student_id == student_id).all()
    return {"unlocked": True, "results": [{"subject": r.subject, "ca": r.ca, "exam": r.exam, "total": r.total, "grade": r.grade} for r in results]}


@app.post("/api/parent/children/{student_id}/record-payment")
def record_payment(student_id: int, payload: schemas.PaymentIn, db: Session = Depends(get_db)):
    """
    TEMPORARY endpoint used while we wait for the school's Paystack keys.
    Once Paystack is connected, this will only run AFTER Paystack confirms the
    transaction succeeded server-side — never trust a payment amount from the
    browser alone in the final version.
    """
    s = db.query(models.Student).filter(models.Student.id == student_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Student not found")
    s.fees_paid += payload.amount
    db.commit()
    return {"student_id": s.id, "fees_due": s.fees_due, "fees_paid": s.fees_paid}
