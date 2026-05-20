from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum as SAEnum, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.dialects.postgresql import ENUM
import enum


class Base(DeclarativeBase):
    pass


class UserRole(str, enum.Enum):
    PATIENT = "patient"
    DOCTOR = "doctor"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(
        ENUM(UserRole, name="userrole", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=UserRole.PATIENT,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    specialty = Column(String(100), nullable=False)
    is_active = Column(String(10), nullable=False, default="true")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    appointments = relationship("Appointment", back_populates="doctor")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(20), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    appointments = relationship("Appointment", back_populates="patient")


class AppointmentStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    appointment_time = Column(DateTime, nullable=False, index=True)
    status = Column(
        ENUM(AppointmentStatus, name="appointmentstatus", create_type=False, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
    )
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    doctor = relationship("Doctor", back_populates="appointments")
    patient = relationship("Patient", back_populates="appointments")
