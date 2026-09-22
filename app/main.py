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
