from pydantic import BaseModel
from typing import Optional, List


class PinCheck(BaseModel):
    pin: str


class PinChange(BaseModel):
    current_pin: str
    new_pin: str


class TeacherCreate(BaseModel):
    name: str
    subject: str


class TeacherOut(BaseModel):
    id: int
    teacher_code: str
    name: str
    subject: str
    pin: str
    checked_in: bool
    check_in_time: str

    class Config:
        from_attributes = True


class TeacherCheckIn(BaseModel):
    latitude: float
    longitude: float


class StudentOut(BaseModel):
    id: int
    student_code: str
    name: str
    class_name: str
    fees_due: float
    fees_paid: float
    result_access: str

    class Config:
        from_attributes = True


class FeeItemCreate(BaseModel):
    name: str
    amount: float


class FeeItemOut(BaseModel):
    id: int
    name: str
    amount: float

    class Config:
        from_attributes = True


class ResultAccessUpdate(BaseModel):
    result_access: str  # pending | approved


class GeofenceUpdate(BaseModel):
    latitude: float
    longitude: float
    radius_m: int


class MessageCreate(BaseModel):
    teacher_id: int
    body: str
    subject: Optional[str] = "General"


class MessageOut(BaseModel):
    id: int
    teacher_id: int
    sender: str
    subject: str
    body: str

    class Config:
        from_attributes = True


class PaymentRecord(BaseModel):
    student_id: int
    amount: float


class StudentCreate(BaseModel):
    name: str
    class_name: Optional[str] = "JSS 2A"
    fees_due: Optional[float] = 67000


class StudentEdit(BaseModel):
    name: Optional[str] = None
    class_name: Optional[str] = None
    fees_due: Optional[float] = None


# ---------- Teacher portal ----------
class TeacherPublicOut(BaseModel):
    id: int
    teacher_code: str
    name: str
    subject: str

    class Config:
        from_attributes = True


class TeacherLoginCheck(BaseModel):
    pin: str


class CheckInRequest(BaseModel):
    latitude: float
    longitude: float


class AttendanceMark(BaseModel):
    status: str  # "Present" | "Absent"


class ResultEntry(BaseModel):
    student_id: int
    subject: str
    ca: float
    exam: float


class TeacherMessageReply(BaseModel):
    body: str


# ---------- Parent portal ----------
class ParentSignup(BaseModel):
    email: str
    password: str
    children_names: List[str]


class ParentLogin(BaseModel):
    email: str
    password: str


class PaymentIn(BaseModel):
    amount: float

