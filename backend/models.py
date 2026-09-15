from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db
from sqlalchemy import text

def now():
    return datetime.now(timezone.utc)

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(40))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="ambassador", index=True)
    account_status = db.Column(db.String(20), nullable=False, default="active", index=True)  # active, pending, blocked
    # Legacy compatibility: older WireFizz databases contain this column. Account status is the canonical field.
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=text("TRUE"), index=True)
    profile_picture = db.Column(db.String(300))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now, onupdate=now)
    last_login_at = db.Column(db.DateTime(timezone=True))

    leads = db.relationship("Lead", back_populates="ambassador", foreign_keys="Lead.ambassador_id", cascade="all, delete-orphan")
    tasks = db.relationship("Task", back_populates="assignee", foreign_keys="Task.assignee_id")
    notifications = db.relationship("Notification", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, value): self.password_hash = generate_password_hash(value)
    def check_password(self, value): return check_password_hash(self.password_hash, value)
    def to_dict(self):
        return {"id": self.id, "full_name": self.full_name, "email": self.email, "phone": self.phone,
                "role": self.role, "account_status": self.account_status, "is_active": self.is_active,
                "profile_picture": self.profile_picture, "created_at": self.created_at.isoformat() if self.created_at else None,
                "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
                "lead_count": len(self.leads or [])}

class Lead(db.Model):
    __tablename__ = "leads"
    id = db.Column(db.Integer, primary_key=True)
    ambassador_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), index=True)
    phone = db.Column(db.String(40), nullable=False, index=True)
    city = db.Column(db.String(100))
    institution = db.Column(db.String(200))
    program_interest = db.Column(db.String(200))
    source = db.Column(db.String(100), index=True)
    notes = db.Column(db.Text)
    status = db.Column(db.String(40), nullable=False, default="new", index=True)
    approval_status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    rejection_reason = db.Column(db.String(500))
    priority = db.Column(db.String(20), nullable=False, default="medium", index=True)
    last_contacted_at = db.Column(db.DateTime(timezone=True))
    next_follow_up_at = db.Column(db.DateTime(timezone=True), index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now, index=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now, onupdate=now)

    ambassador = db.relationship("User", back_populates="leads", foreign_keys=[ambassador_id])
    activities = db.relationship("LeadActivity", back_populates="lead", cascade="all, delete-orphan", order_by="LeadActivity.created_at.desc()")
    tasks = db.relationship("Task", back_populates="lead", cascade="all, delete-orphan", order_by="Task.due_at.asc()")

    def to_dict(self):
        return {"id": self.id, "ambassador_id": self.ambassador_id,
                "ambassador_name": self.ambassador.full_name if self.ambassador else None,
                "ambassador_picture": self.ambassador.profile_picture if self.ambassador else None,
                "full_name": self.full_name, "email": self.email, "phone": self.phone,
                "city": self.city, "institution": self.institution, "program_interest": self.program_interest,
                "source": self.source, "notes": self.notes, "status": self.status,
                "approval_status": self.approval_status, "rejection_reason": self.rejection_reason,
                "priority": self.priority, "last_contacted_at": self.last_contacted_at.isoformat() if self.last_contacted_at else None,
                "next_follow_up_at": self.next_follow_up_at.isoformat() if self.next_follow_up_at else None,
                "created_at": self.created_at.isoformat() if self.created_at else None,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None}

class LeadActivity(db.Model):
    __tablename__ = "lead_activities"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    activity_type = db.Column(db.String(40), nullable=False, default="note")
    note = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now, index=True)
    lead = db.relationship("Lead", back_populates="activities")
    user = db.relationship("User")
    def to_dict(self):
        return {"id": self.id, "lead_id": self.lead_id, "user_id": self.user_id,
                "user_name": self.user.full_name if self.user else "System", "activity_type": self.activity_type,
                "note": self.note, "created_at": self.created_at.isoformat() if self.created_at else None}

class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title = db.Column(db.String(200), nullable=False)
    due_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    completed_at = db.Column(db.DateTime(timezone=True))
    priority = db.Column(db.String(20), nullable=False, default="medium")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    lead = db.relationship("Lead", back_populates="tasks")
    assignee = db.relationship("User", back_populates="tasks", foreign_keys=[assignee_id])
    @property
    def is_completed(self): return self.completed_at is not None
    def to_dict(self):
        return {"id": self.id, "lead_id": self.lead_id, "assignee_id": self.assignee_id,
                "assignee_name": self.assignee.full_name if self.assignee else None, "title": self.title,
                "due_at": self.due_at.isoformat(), "completed_at": self.completed_at.isoformat() if self.completed_at else None,
                "priority": self.priority, "notes": self.notes, "is_completed": self.is_completed}

class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    kind = db.Column(db.String(40), default="info")
    entity_type = db.Column(db.String(40))
    entity_id = db.Column(db.Integer)
    is_read = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now, index=True)
    user = db.relationship("User", back_populates="notifications")
    def to_dict(self):
        return {"id": self.id, "title": self.title, "message": self.message, "kind": self.kind,
                "entity_type": self.entity_type, "entity_id": self.entity_id, "is_read": self.is_read,
                "created_at": self.created_at.isoformat()}

class RefreshSession(db.Model):
    __tablename__ = "refresh_sessions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    jti = db.Column(db.String(64), nullable=False, unique=True, index=True)
    issued_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    revoked_at = db.Column(db.DateTime(timezone=True))
    user_agent = db.Column(db.String(500))
    ip_address = db.Column(db.String(64))
    user = db.relationship("User")

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > now()

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False, index=True)
    entity_type = db.Column(db.String(50))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.String(2000))
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=now, index=True)
    user = db.relationship("User")
    def to_dict(self):
        return {"id": self.id, "user_id": self.user_id, "user_name": self.user.full_name if self.user else "System",
                "action": self.action, "entity_type": self.entity_type, "entity_id": self.entity_id,
                "details": self.details, "ip_address": self.ip_address, "created_at": self.created_at.isoformat()}
