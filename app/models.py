from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class AdminSettings(Base):
    """Single-row table holding the master admin PIN."""
    __tablename__ = "admin_settings"
    id = Column(Integer, primary_key=True, default=1)
    pin = Column(String, default="1234")


class Teacher(Base):
    __tablename__ = "teachers"
    id = Column(Integer, primary_key=True, index=True)
    teacher_code = Column(String, unique=True, index=True)  # e.g. T001
    name = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    pin = Column(String, nullable=False)  # 4-digit access code
    checked_in = Column(Boolean, default=False)
    check_in_time = Column(String, default="-")
    class_name = Column(String, default="")  # e.g. "SS1", "JSS 2A" — the class this teacher handles
    subjects = Column(String, default="Mathematics,English Language,Basic Science,Social Studies,Civic Education")

    students = relationship("Student", back_populates="teacher")
    messages = relationship("Message", back_populates="teacher")


class Student(Base):
    __tablename__ = "students"
    id = Column(Integer, primary_key=True, index=True)
    student_code = Column(String, unique=True, index=True)  # e.g. S001
    name = Column(String, nullable=False)
    class_name = Column(String, default="JSS 2A")
    fees_due = Column(Float, default=67000)
    fees_paid = Column(Float, default=0)
    result_access = Column(String, default="pending")  # pending | approved
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    parent_email = Column(String, nullable=True)
    parent_id = Column(Integer, ForeignKey("parents.id"), nullable=True)

    teacher = relationship("Teacher", back_populates="students")
    parent = relationship("Parent", back_populates="children")
    attendance_records = relationship("Attendance", back_populates="student")
    results = relationship("Result", back_populates="student")


class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    date = Column(String)  # YYYY-MM-DD
    time_in = Column(String, nullable=True)  # HH:MM
    status = Column(String, default="Present")  # Present | Absent
    late = Column(Boolean, default=False)

    student = relationship("Student", back_populates="attendance_records")


class Result(Base):
    __tablename__ = "results"
    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("students.id"))
    subject = Column(String)
    ca = Column(Float, default=0)
    exam = Column(Float, default=0)
    total = Column(Float, default=0)
    grade = Column(String, default="")

    student = relationship("Student", back_populates="results")


class FeeItem(Base):
    __tablename__ = "fee_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    amount = Column(Float, nullable=False)


class ResultLock(Base):
    """Single-row table: global lock for all result downloads."""
    __tablename__ = "result_lock"
    id = Column(Integer, primary_key=True, default=1)
    locked = Column(Boolean, default=True)


class Geofence(Base):
    """Single-row table: school location + radius for attendance check-in."""
    __tablename__ = "geofence"
    id = Column(Integer, primary_key=True, default=1)
    latitude = Column(Float, default=7.4341)
    longitude = Column(Float, default=3.9357)
    radius_m = Column(Integer, default=200)


class Parent(Base):
    __tablename__ = "parents"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    salt = Column(String, nullable=False)

    children = relationship("Student", back_populates="parent")


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"))
    sender = Column(String, default="admin")  # admin | teacher
    subject = Column(String, default="General")
    body = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    teacher = relationship("Teacher", back_populates="messages")
