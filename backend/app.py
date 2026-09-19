from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
import csv, io, os, uuid

from flask import Flask, jsonify, request, Response, send_from_directory
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy import func, or_, inspect, text, case
from email_validator import validate_email, EmailNotValidError
from werkzeug.utils import secure_filename
from PIL import Image, UnidentifiedImageError, ImageOps

from config import Config
from extensions import db, jwt, cors, limiter, client_ip
from models import User, Lead, LeadActivity, Task, Notification, AuditLog, RefreshSession, Avatar, now

PIPELINE = {"new", "contacted", "qualified", "follow_up", "converted"}
APPROVAL = {"pending", "accepted", "rejected"}
PRIORITY = {"low", "medium", "high"}
ACTIVITY = {"note", "call", "meeting", "email", "status_change", "assignment", "system"}
IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}
IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
Image.MAX_IMAGE_PIXELS = 25_000_000


def parse_dt(value):
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_phone(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def valid_email(value):
    """True when the address is syntactically valid.

    check_deliverability is disabled: a DNS lookup on every request is slow,
    can fail on hosting platforms, and must never block a legitimate signup.
    """
    try:
        validate_email(value, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def paginated(query, default=25, max_per=100):
    try:
        page = max(1, int(request.args.get("page", 1)))
        per = min(max(1, int(request.args.get("per_page", default))), max_per)
    except ValueError:
        page, per = 1, default

    total = query.order_by(None).count()
    rows = query.limit(per).offset((page - 1) * per).all()

    return rows, {
        "page": page,
        "per_page": per,
        "total": total,
        "pages": (total + per - 1) // per
    }


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if Config.APP_ENV == "production":
        if Config.SECRET_KEY in ("change-me", "") or Config.JWT_SECRET_KEY in ("change-me-too", ""):
            raise RuntimeError(
                "Production secrets are not configured. Set SECRET_KEY and JWT_SECRET_KEY."
            )

        if "*" in Config.CORS_ORIGINS:
            raise RuntimeError(
                "Production CORS_ORIGINS must not contain '*'."
            )

    db.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)

    # Authentication uses the Authorization header (no cookies), so CORS does
    # not need credentials. Only the configured frontend origin(s) are allowed.
    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": Config.CORS_ORIGINS,
                "allow_headers": ["Authorization", "Content-Type"],
                "methods": ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
                "expose_headers": ["Content-Disposition"],
                "max_age": 86400,
                "supports_credentials": False
            }
        }
    )

    # Legacy folder, only used to serve avatars uploaded by older builds.
    upload_dir = Path(Config.UPLOAD_DIR)
    avatar_dir = upload_dir / "profile"

    try:
        avatar_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    with app.app_context():
        bootstrap_database()

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault(
            "X-Content-Type-Options",
            "nosniff"
        )
        resp.headers.setdefault(
            "X-Frame-Options",
            "DENY"
        )
        resp.headers.setdefault(
            "Referrer-Policy",
            "strict-origin-when-cross-origin"
        )
        resp.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()"
        )

        if Config.APP_ENV == "production":
            resp.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains"
            )

        return resp

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({
            "error": "Request payload is too large."
        }), 413

    @app.errorhandler(429)
    def rate_limited(_):
        return jsonify({
            "error": "Too many requests. Please try again shortly."
        }), 429

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({
            "error": "Not found."
        }), 404

    @app.errorhandler(405)
    def method_not_allowed(_):
        return jsonify({
            "error": "Method not allowed."
        }), 405

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        app.logger.exception("Unhandled server error: %s", error)

        return jsonify({
            "error": "Something went wrong on the server. Please try again."
        }), 500

    @jwt.unauthorized_loader
    def jwt_missing(_):
        return jsonify({
            "error": "Authentication required."
        }), 401

    @jwt.invalid_token_loader
    def jwt_invalid(_):
        return jsonify({
            "error": "Invalid authentication token."
        }), 401

    @jwt.expired_token_loader
    def jwt_expired(_header, _payload):
        return jsonify({
            "error": "Authentication token expired."
        }), 401

    @jwt.revoked_token_loader
    def jwt_revoked(_header, _payload):
        return jsonify({
            "error": "Authentication session revoked."
        }), 401

    @jwt.needs_fresh_token_loader
    def jwt_fresh_required(_header, _payload):
        return jsonify({
            "error": "A fresh authentication token is required."
        }), 401

    def get_user():
        try:
            uid = int(get_jwt_identity())
        except (TypeError, ValueError):
            return None

        return db.session.get(User, uid)

    def require_user(*roles):
        def deco(fn):
            @wraps(fn)
            @jwt_required()
            def wrapped(*args, **kwargs):
                user = get_user()

                if not user or user.account_status != "active":
                    return jsonify({
                        "error": "Account unavailable."
                    }), 403

                if roles and user.role not in roles:
                    return jsonify({
                        "error": "Forbidden."
                    }), 403

                return fn(user, *args, **kwargs)

            return wrapped

        return deco

    def audit(
        user_id,
        action,
        entity_type=None,
        entity_id=None,
        details=None
    ):
        db.session.add(
            AuditLog(
                user_id=user_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=(details or "")[:2000],
                ip_address=client_ip(),
                user_agent=request.headers.get(
                    "User-Agent",
                    ""
                )[:500]
            )
        )

    def notify(
        user_id,
        title,
        message,
        kind="info",
        entity_type=None,
        entity_id=None
    ):
        db.session.add(
            Notification(
                user_id=user_id,
                title=title,
                message=message,
                kind=kind,
                entity_type=entity_type,
                entity_id=entity_id
            )
        )

    def visible_lead(user, lead):
        return user.role == "admin" or lead.ambassador_id == user.id

    def find_duplicate_lead(phone, email):
        norm = normalize_phone(phone)
        dup = None

        if norm:
            dup = Lead.query.filter(
                func.regexp_replace(
                    Lead.phone,
                    r"[^0-9]+",
                    "",
                    "g"
                ) == norm
            ).first()

        if not dup and email:
            dup = Lead.query.filter(
                func.lower(Lead.email) == email
            ).first()

        return dup

    @app.get("/api/health")
    @limiter.exempt
    def health():
        try:
            db.session.execute(text("SELECT 1"))

            return jsonify({
                "status": "ok",
                "database": "ok",
                "service": "WireFizz LMS API"
            })

        except Exception:
            return jsonify({
                "status": "degraded",
                "database": "unavailable",
                "service": "WireFizz LMS API"
            }), 503

    @app.post("/api/auth/register")
    @limiter.limit("5 per minute")
    def register():
        data = request.get_json(silent=True) or {}

        full_name, email, phone, password = [
            str(data.get(k, "")).strip()
            for k in (
                "full_name",
                "email",
                "phone",
                "password"
            )
        ]

        email = email.lower()

        if not full_name or not email or not password:
            return jsonify({
                "error": "Full name, email and password are required."
            }), 400

        if len(password) < 8:
            return jsonify({
                "error": "Password must be at least 8 characters."
            }), 400

        if len(full_name) > 120:
            return jsonify({
                "error": "Full name is too long."
            }), 400

        if not valid_email(email):
            return jsonify({
                "error": "Please provide a valid email address."
            }), 400

        if User.query.filter(
            func.lower(User.email) == email
        ).first():
            return jsonify({
                "error": "An account with this email already exists."
            }), 409

        status = (
            "active"
            if Config.AUTO_APPROVE_REGISTRATION
            else "pending"
        )

        user = User(
            full_name=full_name,
            email=email,
            phone=phone or None,
            role="ambassador",
            account_status=status,
            is_active=(status == "active")
        )

        user.set_password(password)

        db.session.add(user)
        db.session.flush()

        audit(
            user.id,
            "account_registered",
            "user",
            user.id,
            f"Ambassador account created with status {status}"
        )

        db.session.commit()

        if status != "active":
            return jsonify({
                "message": "Registration submitted for admin approval."
            }), 201

        return jsonify({
            "message": "Account created. You can now sign in."
        }), 201

    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    def issue_session(user):
        """Create an access + refresh token pair and persist the refresh session.

        Tokens are returned in the JSON body; the SPA sends the access token in
        the Authorization header. No cookies are involved, so this works for a
        frontend and API on different domains (Vercel + Render).
        """
        access = create_access_token(
            identity=str(user.id),
            additional_claims={
                "role": user.role
            }
        )

        refresh = create_refresh_token(
            identity=str(user.id),
            additional_claims={
                "role": user.role
            }
        )

        refresh_claims = decode_token(refresh)

        db.session.add(
            RefreshSession(
                user_id=user.id,
                jti=refresh_claims["jti"],
                issued_at=now(),
                expires_at=datetime.fromtimestamp(
                    refresh_claims["exp"],
                    tz=timezone.utc
                ),
                user_agent=request.headers.get(
                    "User-Agent",
                    ""
                )[:500],
                ip_address=client_ip()
            )
        )

        db.session.commit()

        return no_store(jsonify({
            "user": user.to_dict(),
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "Bearer",
            "expires_in": int(
                Config.JWT_ACCESS_TOKEN_EXPIRES
            )
        }))

    @app.post("/api/auth/login")
    @limiter.limit("10 per minute")
    def login():
        data = request.get_json(silent=True) or {}

        email = str(
            data.get("email", "")
        ).strip().lower()

        password = str(
            data.get(
                "password",
                ""
            )
        )

        user = User.query.filter(
            func.lower(User.email) == email
        ).first()

        if not user or not user.check_password(password):
            return jsonify({
                "error": "Invalid email or password."
            }), 401

        if user.account_status == "pending":
            return jsonify({
                "error": "Your account is awaiting admin approval."
            }), 403

        if user.account_status == "blocked":
            return jsonify({
                "error": "Your account is blocked. Contact an administrator."
            }), 403

        user.last_login_at = now()

        audit(
            user.id,
            "login",
            "user",
            user.id,
            "Successful login"
        )

        db.session.commit()

        return issue_session(user)

    @app.post("/api/auth/refresh")
    @limiter.limit("60 per minute")
    @jwt_required(refresh=True)
    def refresh():
        claims = get_jwt()
        user = get_user()

        session = RefreshSession.query.filter_by(
            jti=claims.get("jti"),
            user_id=int(get_jwt_identity())
        ).first()

        if (
            not user
            or user.account_status != "active"
            or not session
            or not session.is_active
        ):
            return jsonify({
                "error": "Refresh session unavailable."
            }), 401

        session.revoked_at = now()

        audit(
            user.id,
            "session_rotated",
            "session",
            session.id,
            "Refresh token rotated"
        )

        return issue_session(user)

    @app.post("/api/auth/logout")
    def logout():
        # Revokes the refresh session (sent in the JSON body or as a Bearer
        # token). Idempotent: works even when the access token has expired.
        body = request.get_json(silent=True) or {}
        candidates = [body.get("refresh_token")]
        header = request.headers.get("Authorization", "")

        if header.lower().startswith("bearer "):
            candidates.append(header[7:].strip())

        user = None

        for candidate in candidates:
            if not candidate or not isinstance(candidate, str):
                continue

            try:
                claims = decode_token(
                    candidate,
                    allow_expired=True
                )
            except Exception:
                continue

            if claims.get("type") != "refresh":
                continue

            session = RefreshSession.query.filter_by(
                jti=claims.get("jti")
            ).first()

            if session and not session.revoked_at:
                session.revoked_at = now()

            try:
                user = db.session.get(
                    User,
                    int(claims.get("sub"))
                )
            except (TypeError, ValueError):
                user = None

            break

        if user:
            audit(
                user.id,
                "logout",
                "user",
                user.id,
                "Logout"
            )

        db.session.commit()

        return jsonify({
            "message": "Signed out."
        })

    @app.get("/api/auth/me")
    @require_user("admin", "ambassador")
    def me(user):
        return jsonify({
            "user": user.to_dict()
        })

    @app.get("/api/uploads/profile/<path:filename>")
    @limiter.exempt
    def profile_image(filename):
        safe = secure_filename(filename)

        avatar = db.session.get(
            Avatar,
            safe
        ) if safe else None

        if avatar:
            resp = Response(
                avatar.data,
                mimetype=avatar.mimetype
            )

            resp.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
            )

            resp.headers["Cross-Origin-Resource-Policy"] = (
                "cross-origin"
            )

            return resp

        # Avatars uploaded by older builds lived on disk.
        if safe and (avatar_dir / safe).is_file():
            resp = send_from_directory(
                avatar_dir,
                safe
            )

            resp.headers["Cross-Origin-Resource-Policy"] = (
                "cross-origin"
            )

            return resp

        return jsonify({
            "error": "Image not found."
        }), 404

    @app.get("/api/profile")
    @require_user("admin", "ambassador")
    def profile(user):
        return jsonify({
            "user": user.to_dict()
        })

    @app.patch("/api/profile")
    @require_user("admin", "ambassador")
    def update_profile(user):
        data = request.get_json(silent=True) or {}

        name = str(
            data.get(
                "full_name",
                user.full_name
            )
        ).strip()

        phone = str(
            data.get(
                "phone",
                user.phone or ""
            )
        ).strip()

        if not name:
            return jsonify({
                "error": "Full name is required."
            }), 400

        user.full_name = name[:120]
        user.phone = phone or None

        audit(
            user.id,
            "profile_updated",
            "user",
            user.id,
            "Profile information updated"
        )

        db.session.commit()

        return jsonify({
            "message": "Profile updated.",
            "user": user.to_dict()
        })

    def drop_avatar(url):
        """Delete the stored image behind a /api/uploads/profile/<name> URL."""
        if not url or not url.startswith("/api/uploads/profile/"):
            return

        name = secure_filename(Path(url).name)

        if not name:
            return

        Avatar.query.filter_by(
            filename=name
        ).delete()

        legacy = avatar_dir / name

        try:
            if legacy.is_file():
                legacy.unlink()
        except OSError:
            pass

    @app.post("/api/profile/avatar")
    @require_user("admin", "ambassador")
    def upload_avatar(user):
        f = request.files.get("file")

        if not f or not f.filename:
            return jsonify({
                "error": "Please select an image."
            }), 400

        original = secure_filename(f.filename)
        ext = Path(original).suffix.lower().lstrip(".")

        if (
            ext not in IMAGE_EXT
            or f.mimetype not in IMAGE_MIME
        ):
            return jsonify({
                "error": "Only PNG, JPG, JPEG and WEBP images are allowed."
            }), 400

        try:
            raw = f.read()
            probe = Image.open(io.BytesIO(raw))
            probe.verify()

            # verify() invalidates the object, so re-open to decode.
            image = Image.open(io.BytesIO(raw))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((512, 512))

            has_alpha = (
                image.mode in ("RGBA", "LA")
                or (
                    image.mode == "P"
                    and "transparency" in image.info
                )
            )

            buffer = io.BytesIO()

            if has_alpha:
                image.convert("RGBA").save(
                    buffer,
                    format="PNG",
                    optimize=True
                )

                out_ext, out_mime = "png", "image/png"

            else:
                image.convert("RGB").save(
                    buffer,
                    format="JPEG",
                    quality=88,
                    optimize=True
                )

                out_ext, out_mime = "jpg", "image/jpeg"

        except Exception:
            return jsonify({
                "error": "The uploaded file is not a valid image."
            }), 400

        filename = f"{user.id}_{uuid.uuid4().hex}.{out_ext}"

        db.session.add(
            Avatar(
                filename=filename,
                user_id=user.id,
                mimetype=out_mime,
                data=buffer.getvalue()
            )
        )

        old = user.profile_picture

        user.profile_picture = (
            f"/api/uploads/profile/{filename}"
        )

        drop_avatar(old)

        audit(
            user.id,
            "profile_picture_updated",
            "user",
            user.id,
            "Profile picture uploaded"
        )

        db.session.commit()

        return jsonify({
            "message": "Profile picture updated.",
            "user": user.to_dict()
        })

    @app.delete("/api/profile/avatar")
    @require_user("admin", "ambassador")
    def remove_avatar(user):
        old = user.profile_picture
        user.profile_picture = None

        drop_avatar(old)

        audit(
            user.id,
            "profile_picture_removed",
            "user",
            user.id,
            "Profile picture removed"
        )

        db.session.commit()

        return jsonify({
            "message": "Profile picture removed.",
            "user": user.to_dict()
        })

    @app.post("/api/auth/change-password")
    @require_user("admin", "ambassador")
    @limiter.limit("5 per minute")
    def change_password(user):
        data = request.get_json(silent=True) or {}

        current = data.get(
            "current_password",
            ""
        )

        new = data.get(
            "new_password",
            ""
        )

        if not user.check_password(current):
            return jsonify({
                "error": "Current password is incorrect."
            }), 400

        if len(new) < 8:
            return jsonify({
                "error": "New password must be at least 8 characters."
            }), 400

        user.set_password(new)

        RefreshSession.query.filter_by(
            user_id=user.id,
            revoked_at=None
        ).update({
            "revoked_at": now()
        })

        audit(
            user.id,
            "password_changed",
            "user",
            user.id,
            "Password changed; active refresh sessions revoked"
        )

        db.session.commit()

        return jsonify({
            "message": "Password changed. Please sign in again."
        })

    @app.get("/api/dashboard/stats")
    @require_user("admin", "ambassador")
    def dashboard_stats(user):
        q = (
            Lead.query
            if user.role == "admin"
            else Lead.query.filter_by(
                ambassador_id=user.id
            )
        )

        return jsonify({
            "total": q.count(),
            "new": q.filter_by(status="new").count(),
            "contacted": q.filter_by(status="contacted").count(),
            "qualified": q.filter_by(status="qualified").count(),
            "follow_up": q.filter_by(status="follow_up").count(),
            "converted": q.filter_by(status="converted").count(),
            "pending": q.filter_by(
                approval_status="pending"
            ).count(),
            "accepted": q.filter_by(
                approval_status="accepted"
            ).count(),
            "rejected": q.filter_by(
                approval_status="rejected"
            ).count(),
            "high_priority": q.filter_by(
                priority="high"
            ).count(),
            "due_followups": q.filter(
                Lead.next_follow_up_at <= now(),
                Lead.next_follow_up_at.is_not(None)
            ).count(),
            "ambassadors": (
                User.query.filter_by(
                    role="ambassador",
                    account_status="active"
                ).count()
                if user.role == "admin"
                else 0
            ),
            "blocked": (
                User.query.filter_by(
                    role="ambassador",
                    account_status="blocked"
                ).count()
                if user.role == "admin"
                else 0
            ),
        })

    def leads_query(user):
        return (
            Lead.query
            if user.role == "admin"
            else Lead.query.filter_by(
                ambassador_id=user.id
            )
        )

    @app.get("/api/leads")
    @require_user("admin", "ambassador")
    def list_leads(user):
        q = leads_query(user)

        for param, column, allowed in [
            ("status", Lead.status, PIPELINE),
            ("approval_status", Lead.approval_status, APPROVAL),
            ("priority", Lead.priority, PRIORITY)
        ]:
            v = request.args.get(param)

            if v in allowed:
                q = q.filter(column == v)

        source = request.args.get("source")
        ambassador_id = request.args.get("ambassador_id")

        if source:
            q = q.filter(
                Lead.source == source
            )

        if user.role == "admin" and ambassador_id:
            try:
                q = q.filter(
                    Lead.ambassador_id == int(
                        ambassador_id
                    )
                )
            except ValueError:
                pass

        search = request.args.get(
            "search",
            ""
        ).strip()

        if search:
            p = f"%{search}%"

            q = q.filter(
                or_(
                    Lead.full_name.ilike(p),
                    Lead.email.ilike(p),
                    Lead.phone.ilike(p),
                    Lead.city.ilike(p),
                    Lead.institution.ilike(p),
                    Lead.program_interest.ilike(p)
                )
            )

        rows, meta = paginated(
            q.order_by(
                Lead.created_at.desc()
            )
        )

        return jsonify({
            "leads": [
                x.to_dict()
                for x in rows
            ],
            "pagination": meta
        })

    @app.post("/api/leads")
    @require_user("ambassador")
    def create_lead(user):
        d = request.get_json(silent=True) or {}

        name = str(
            d.get("full_name", "")
        ).strip()

        phone = str(
            d.get("phone", "")
        ).strip()

        email = (
            str(
                d.get("email", "")
            ).strip().lower()
            or None
        )

        if not name or not phone:
            return jsonify({
                "error": "Lead name and phone are required."
            }), 400

        if email and not valid_email(email):
            return jsonify({
                "error": "Lead email is invalid."
            }), 400

        dup = find_duplicate_lead(phone, email)

        if dup:
            return jsonify({
                "error": (
                    f"Possible duplicate lead: "
                    f"{dup.full_name} (Lead #{dup.id})."
                ),
                "duplicate_id": dup.id
            }), 409

        lead = Lead(
            ambassador_id=user.id,
            full_name=name[:120],
            phone=phone[:40],
            email=email,
            city=str(
                d.get("city", "")
            ).strip() or None,
            institution=str(
                d.get("institution", "")
            ).strip() or None,
            program_interest=str(
                d.get("program_interest", "")
            ).strip() or None,
            source=str(
                d.get("source", "")
            ).strip() or None,
            notes=str(
                d.get("notes", "")
            ).strip() or None,
            priority=(
                d.get("priority")
                if d.get("priority") in PRIORITY
                else "medium"
            ),
            next_follow_up_at=parse_dt(
                d.get("next_follow_up_at")
            )
        )

        db.session.add(lead)
        db.session.flush()

        db.session.add(
            LeadActivity(
                lead_id=lead.id,
                user_id=user.id,
                activity_type="system",
                note="Lead submitted for admin review."
            )
        )

        audit(
            user.id,
            "lead_created",
            "lead",
            lead.id,
            f"Submitted {lead.full_name}"
        )

        admins = User.query.filter_by(
            role="admin",
            account_status="active"
        ).all()

        for admin in admins:
            notify(
                admin.id,
                "New lead submitted",
                f"{lead.full_name} was submitted by {user.full_name}.",
                "info",
                "lead",
                lead.id
            )

        db.session.commit()

        return jsonify({
            "message": "Lead submitted for admin review.",
            "lead": lead.to_dict()
        }), 201

    @app.get("/api/leads/<int:lead_id>")
    @require_user("admin", "ambassador")
    def get_lead(user, lead_id):
        lead = db.session.get(
            Lead,
            lead_id
        )

        if not lead or not visible_lead(user, lead):
            return jsonify({
                "error": "Lead not found."
            }), 404

        return jsonify({
            "lead": lead.to_dict(),
            "activities": [
                a.to_dict()
                for a in lead.activities
            ],
            "tasks": [
                t.to_dict()
                for t in lead.tasks
            ]
        })

    @app.post("/api/leads/<int:lead_id>/activities")
    @require_user("admin", "ambassador")
    def add_activity(user, lead_id):
        lead = db.session.get(
            Lead,
            lead_id
        )

        if not lead or not visible_lead(user, lead):
            return jsonify({
                "error": "Lead not found."
            }), 404

        d = request.get_json(silent=True) or {}

        typ = str(
            d.get(
                "activity_type",
                "note"
            )
        )

        note = str(
            d.get("note", "")
        ).strip()

        if typ not in ACTIVITY or not note:
            return jsonify({
                "error": "Valid activity type and note are required."
            }), 400

        a = LeadActivity(
            lead_id=lead.id,
            user_id=user.id,
            activity_type=typ,
            note=note[:5000]
        )

        db.session.add(a)

        if typ in {
            "call",
            "meeting",
            "email"
        }:
            lead.last_contacted_at = now()

        audit(
            user.id,
            "lead_activity_added",
            "lead",
            lead.id,
            f"{typ}: {note[:250]}"
        )

        db.session.commit()

        return jsonify({
            "message": "Activity added.",
            "activity": a.to_dict()
        })

    @app.patch("/api/leads/<int:lead_id>")
    @require_user("admin", "ambassador")
    def update_lead(user, lead_id):
        lead = db.session.get(
            Lead,
            lead_id
        )

        if not lead or not visible_lead(user, lead):
            return jsonify({
                "error": "Lead not found."
            }), 404

        d = request.get_json(silent=True) or {}
        changes = []

        if user.role == "admin":
            if (
                "status" in d
                and d["status"] in PIPELINE
                and d["status"] != lead.status
            ):
                changes.append(
                    f"Status {lead.status} → {d['status']}"
                )

                lead.status = d["status"]

            if (
                "approval_status" in d
                and d["approval_status"] in APPROVAL
                and d["approval_status"] != lead.approval_status
            ):
                old = lead.approval_status

                lead.approval_status = d[
                    "approval_status"
                ]

                changes.append(
                    f"Review {old} → {lead.approval_status}"
                )

                if lead.approval_status == "accepted":
                    lead.rejection_reason = None
                    lead.status = "new"

                if lead.approval_status == "rejected":
                    lead.rejection_reason = (
                        str(
                            d.get(
                                "rejection_reason",
                                ""
                            )
                        ).strip()[:500]
                        or "Rejected by administrator"
                    )

            if (
                "priority" in d
                and d["priority"] in PRIORITY
                and d["priority"] != lead.priority
            ):
                changes.append(
                    f"Priority {lead.priority} → {d['priority']}"
                )

                lead.priority = d["priority"]

            if "ambassador_id" in d:
                try:
                    new_id = int(
                        d["ambassador_id"]
                    )
                except (
                    TypeError,
                    ValueError
                ):
                    return jsonify({
                        "error": "Invalid assignee."
                    }), 400

                assignee = db.session.get(
                    User,
                    new_id
                )

                if (
                    not assignee
                    or assignee.role != "ambassador"
                    or assignee.account_status != "active"
                ):
                    return jsonify({
                        "error": "Assignee is unavailable."
                    }), 400

                if lead.ambassador_id != new_id:
                    old_name = (
                        lead.ambassador.full_name
                        if lead.ambassador
                        else "Unknown"
                    )

                    lead.ambassador_id = new_id

                    changes.append(
                        f"Assigned {old_name} → {assignee.full_name}"
                    )

                    db.session.add(
                        LeadActivity(
                            lead_id=lead.id,
                            user_id=user.id,
                            activity_type="assignment",
                            note=changes[-1]
                        )
                    )

                    notify(
                        assignee.id,
                        "Lead assigned",
                        f"Lead #{lead.id} — {lead.full_name} was assigned to you.",
                        "info",
                        "lead",
                        lead.id
                    )

        for field in [
            "full_name",
            "email",
            "phone",
            "city",
            "institution",
            "program_interest",
            "source",
            "notes"
        ]:
            if field in d:
                value = (
                    str(d[field]).strip()
                    if d[field] is not None
                    else ""
                )

                if field == "email":
                    value = value.lower()

                    if value and not valid_email(value):
                        return jsonify({
                            "error": "Lead email is invalid."
                        }), 400

                if field in {"full_name", "phone"}:
                    setattr(lead, field, value[:120 if field == "full_name" else 40])
                else:
                    setattr(lead, field, value or None)

        if "next_follow_up_at" in d:
            lead.next_follow_up_at = parse_dt(
                d.get("next_follow_up_at")
            )

        if (
            user.role == "admin"
            and "last_contacted_at" in d
        ):
            lead.last_contacted_at = parse_dt(
                d.get("last_contacted_at")
            )

        if not lead.full_name or not lead.phone:
            return jsonify({
                "error": "Lead name and phone are required."
            }), 400

        if changes:
            for c in changes:
                db.session.add(
                    LeadActivity(
                        lead_id=lead.id,
                        user_id=user.id,
                        activity_type="status_change",
                        note=c
                    )
                )

            audit(
                user.id,
                "lead_updated",
                "lead",
                lead.id,
                "; ".join(changes)
            )

            if (
                lead.ambassador
                and lead.ambassador_id != user.id
            ):
                notify(
                    lead.ambassador_id,
                    "Lead updated",
                    f"Lead #{lead.id} was updated by an administrator.",
                    "info",
                    "lead",
                    lead.id
                )

        db.session.commit()

        return jsonify({
            "message": "Lead updated.",
            "lead": lead.to_dict()
        })

    @app.delete("/api/leads/<int:lead_id>")
    @require_user("admin")
    def delete_lead(user, lead_id):
        lead = db.session.get(
            Lead,
            lead_id
        )

        if not lead:
            return jsonify({
                "error": "Lead not found."
            }), 404

        audit(
            user.id,
            "lead_deleted",
            "lead",
            lead.id,
            f"Deleted lead {lead.full_name} (#{lead.id})"
        )

        db.session.delete(lead)
        db.session.commit()

        return jsonify({
            "message": "Lead deleted."
        })

    @app.post("/api/admin/leads/bulk")
    @require_user("admin")
    def bulk_leads(user):
        d = request.get_json(silent=True) or {}

        ids = d.get("lead_ids") or []
        action = d.get("action")

        if (
            not ids
            or action not in {
                "delete",
                "accept",
                "reject",
                "status",
                "priority",
                "assign"
            }
        ):
            return jsonify({
                "error": "Invalid bulk action."
            }), 400

        affected = []

        for raw in ids:
            try:
                lead = db.session.get(
                    Lead,
                    int(raw)
                )
            except (
                TypeError,
                ValueError
            ):
                lead = None

            if not lead:
                continue

            if action == "delete":
                audit(
                    user.id,
                    "lead_deleted",
                    "lead",
                    lead.id,
                    "Bulk deletion"
                )

                db.session.delete(lead)

            elif action == "accept":
                lead.approval_status = "accepted"
                lead.rejection_reason = None
                lead.status = "new"

            elif action == "reject":
                lead.approval_status = "rejected"
                lead.rejection_reason = str(
                    d.get(
                        "reason",
                        "Rejected in bulk"
                    )
                )[:500]

            elif (
                action == "status"
                and d.get("status") in PIPELINE
            ):
                lead.status = d["status"]

            elif (
                action == "priority"
                and d.get("priority") in PRIORITY
            ):
                lead.priority = d["priority"]

            elif action == "assign":
                try:
                    assignee_id = int(
                        d.get(
                            "ambassador_id",
                            0
                        )
                    )
                except (TypeError, ValueError):
                    return jsonify({
                        "error": "Invalid assignee."
                    }), 400

                assignee = db.session.get(
                    User,
                    assignee_id
                )

                if (
                    not assignee
                    or assignee.role != "ambassador"
                    or assignee.account_status != "active"
                ):
                    return jsonify({
                        "error": "Invalid assignee."
                    }), 400

                lead.ambassador_id = assignee.id

                notify(
                    assignee.id,
                    "Leads assigned",
                    f"Lead #{lead.id} was assigned to you.",
                    "info",
                    "lead",
                    lead.id
                )

            affected.append(lead.id)

        audit(
            user.id,
            "bulk_lead_action",
            "lead",
            None,
            f"Action {action} on {len(affected)} leads"
        )

        db.session.commit()

        return jsonify({
            "message": (
                f"Bulk action applied to "
                f"{len(affected)} leads."
            ),
            "affected": affected
        })

    @app.post("/api/leads/<int:lead_id>/tasks")
    @require_user("admin", "ambassador")
    def add_task(user, lead_id):
        lead = db.session.get(
            Lead,
            lead_id
        )

        if not lead or not visible_lead(user, lead):
            return jsonify({
                "error": "Lead not found."
            }), 404

        d = request.get_json(silent=True) or {}

        title = str(
            d.get(
                "title",
                "New follow-up"
            )
        ).strip()

        due = parse_dt(
            d.get("due_at")
        )

        if not title or not due:
            return jsonify({
                "error": "Task title and due date are required."
            }), 400

        assignee_id = user.id

        if user.role == "admin" and d.get("assignee_id"):
            try:
                assignee_id = int(
                    d["assignee_id"]
                )
            except (TypeError, ValueError):
                return jsonify({
                    "error": "Invalid assignee."
                }), 400

            assignee = db.session.get(
                User,
                assignee_id
            )

            if (
                not assignee
                or assignee.role != "ambassador"
                or assignee.account_status != "active"
            ):
                return jsonify({
                    "error": "Invalid assignee."
                }), 400

        task = Task(
            lead_id=lead.id,
            assignee_id=assignee_id,
            title=title[:200],
            due_at=due,
            priority=(
                d.get("priority")
                if d.get("priority") in PRIORITY
                else "medium"
            ),
            notes=str(
                d.get("notes", "")
            ).strip() or None
        )

        db.session.add(task)

        audit(
            user.id,
            "task_created",
            "task",
            None,
            f"Follow-up for lead #{lead.id}"
        )

        db.session.commit()

        return jsonify({
            "task": task.to_dict()
        }), 201

    @app.patch("/api/tasks/<int:task_id>")
    @require_user("admin", "ambassador")
    def update_task(user, task_id):
        task = db.session.get(
            Task,
            task_id
        )

        if (
            not task
            or (
                user.role != "admin"
                and task.assignee_id != user.id
                and task.lead.ambassador_id != user.id
            )
        ):
            return jsonify({
                "error": "Task not found."
            }), 404

        d = request.get_json(silent=True) or {}

        if "completed" in d:
            task.completed_at = (
                now()
                if d["completed"]
                else None
            )

        if "title" in d:
            task.title = str(
                d["title"]
            ).strip()[:200]

        if "due_at" in d:
            dt = parse_dt(
                d["due_at"]
            )

            if not dt:
                return jsonify({
                    "error": "Invalid due date."
                }), 400

            task.due_at = dt

        if (
            "priority" in d
            and d["priority"] in PRIORITY
        ):
            task.priority = d["priority"]

        db.session.commit()

        return jsonify({
            "task": task.to_dict()
        })

    @app.get("/api/tasks")
    @require_user("admin", "ambassador")
    def list_tasks(user):
        q = (
            Task.query
            if user.role == "admin"
            else Task.query.filter_by(
                assignee_id=user.id
            )
        )

        if request.args.get(
            "completed"
        ) in {"true", "false"}:
            if request.args.get(
                "completed"
            ) == "true":
                q = q.filter(
                    Task.completed_at.is_not(None)
                )
            else:
                q = q.filter(
                    Task.completed_at.is_(None)
                )

        rows, meta = paginated(
            q.order_by(
                Task.due_at.asc()
            )
        )

        return jsonify({
            "tasks": [
                x.to_dict()
                for x in rows
            ],
            "pagination": meta
        })

    @app.get("/api/notifications")
    @require_user("admin", "ambassador")
    def notifications(user):
        q = Notification.query.filter_by(
            user_id=user.id
        )

        unread = q.filter_by(
            is_read=False
        ).count()

        rows, meta = paginated(
            q.order_by(
                Notification.created_at.desc()
            ),
            20,
            50
        )

        return jsonify({
            "notifications": [
                x.to_dict()
                for x in rows
            ],
            "unread": unread,
            "pagination": meta
        })

    @app.patch("/api/notifications/<int:notification_id>/read")
    @require_user("admin", "ambassador")
    def mark_notification(
        user,
        notification_id
    ):
        n = db.session.get(
            Notification,
            notification_id
        )

        if not n or n.user_id != user.id:
            return jsonify({
                "error": "Notification not found."
            }), 404

        n.is_read = True

        db.session.commit()

        return jsonify({
            "message": "Marked as read."
        })

    @app.post("/api/notifications/read-all")
    @require_user("admin", "ambassador")
    def mark_all_notifications(user):
        Notification.query.filter_by(
            user_id=user.id,
            is_read=False
        ).update({
            "is_read": True
        })

        db.session.commit()

        return jsonify({
            "message": "Notifications cleared."
        })

    @app.get("/api/admin/users")
    @require_user("admin")
    def admin_users(user):
        q = User.query.filter(
            User.role == "ambassador"
        )

        search = request.args.get(
            "search",
            ""
        ).strip()

        if search:
            q = q.filter(
                or_(
                    User.full_name.ilike(
                        f"%{search}%"
                    ),
                    User.email.ilike(
                        f"%{search}%"
                    ),
                    User.phone.ilike(
                        f"%{search}%"
                    )
                )
            )

        if request.args.get(
            "status"
        ) in {
            "active",
            "pending",
            "blocked"
        }:
            q = q.filter(
                User.account_status
                == request.args["status"]
            )

        rows, meta = paginated(
            q.order_by(
                User.created_at.desc()
            )
        )

        return jsonify({
            "users": [
                x.to_dict()
                for x in rows
            ],
            "pagination": meta
        })

    @app.patch("/api/admin/users/<int:user_id>")
    @require_user("admin")
    def update_user(user, user_id):
        target = db.session.get(
            User,
            user_id
        )

        if not target or target.role != "ambassador":
            return jsonify({
                "error": "Employee not found."
            }), 404

        d = request.get_json(silent=True) or {}

        status = d.get(
            "account_status"
        )

        if status not in {
            "active",
            "pending",
            "blocked"
        }:
            return jsonify({
                "error": "Invalid account status."
            }), 400

        target.account_status = status
        target.is_active = (
            status == "active"
        )

        target.full_name = str(
            d.get(
                "full_name",
                target.full_name
            )
        ).strip()[:120]

        target.phone = (
            str(
                d.get(
                    "phone",
                    target.phone or ""
                )
            ).strip()
            or None
        )

        audit(
            user.id,
            "employee_status_changed",
            "user",
            target.id,
            f"Employee set to {status}"
        )

        if status == "active":
            notify(
                target.id,
                "Account activated",
                "Your WireFizz LMS account is active.",
                "success"
            )

        if status != "active":
            RefreshSession.query.filter_by(
                user_id=target.id,
                revoked_at=None
            ).update({
                "revoked_at": now()
            })

        if status == "blocked":
            notify(
                target.id,
                "Account blocked",
                "Your WireFizz LMS account has been blocked by an administrator.",
                "warning"
            )

        db.session.commit()

        return jsonify({
            "user": target.to_dict()
        })

    @app.delete("/api/admin/users/<int:user_id>")
    @require_user("admin")
    def delete_user(user, user_id):
        target = db.session.get(
            User,
            user_id
        )

        if not target or target.role != "ambassador":
            return jsonify({
                "error": "Employee not found."
            }), 404

        audit(
            user.id,
            "employee_deleted",
            "user",
            target.id,
            f"Deleted employee {target.full_name} and related data"
        )

        db.session.delete(target)
        db.session.commit()

        return jsonify({
            "message": "Employee deleted."
        })

    @app.post("/api/admin/users")
    @require_user("admin")
    def create_employee(user):
        d = request.get_json(silent=True) or {}

        name = str(
            d.get(
                "full_name",
                ""
            )
        ).strip()

        email = str(
            d.get(
                "email",
                ""
            )
        ).strip().lower()

        pwd = str(
            d.get(
                "password",
                ""
            )
        )

        if (
            not name
            or not email
            or len(pwd) < 8
        ):
            return jsonify({
                "error": "Name, email and password (8+ chars) are required."
            }), 400

        if not valid_email(email):
            return jsonify({
                "error": "Invalid email."
            }), 400

        if User.query.filter(
            func.lower(User.email) == email
        ).first():
            return jsonify({
                "error": "Email already exists."
            }), 409

        emp = User(
            full_name=name,
            email=email,
            phone=str(
                d.get(
                    "phone",
                    ""
                )
            ).strip() or None,
            role="ambassador",
            account_status="active"
        )

        emp.set_password(pwd)

        db.session.add(emp)
        db.session.flush()

        audit(
            user.id,
            "employee_created",
            "user",
            emp.id,
            f"Created employee {emp.full_name}"
        )

        db.session.commit()

        return jsonify({
            "user": emp.to_dict()
        }), 201

    @app.get("/api/admin/audit-logs")
    @require_user("admin")
    def audit_logs(user):
        q = AuditLog.query

        action = request.args.get(
            "action"
        )

        search = request.args.get(
            "search",
            ""
        ).strip()

        if action:
            q = q.filter(
                AuditLog.action == action
            )

        if search:
            q = q.filter(
                AuditLog.details.ilike(
                    f"%{search}%"
                )
            )

        rows, meta = paginated(
            q.order_by(
                AuditLog.created_at.desc()
            ),
            30,
            100
        )

        return jsonify({
            "logs": [
                x.to_dict()
                for x in rows
            ],
            "pagination": meta
        })

    @app.get("/api/performance")
    @require_user("ambassador")
    def performance(user):
        q = Lead.query.filter_by(
            ambassador_id=user.id
        )

        total = q.count()
        accepted = q.filter_by(
            approval_status="accepted"
        ).count()

        converted = q.filter_by(
            status="converted"
        ).count()

        pipeline = [
            {
                "status": st,
                "count": q.filter_by(
                    status=st
                ).count()
            }
            for st in [
                "new",
                "contacted",
                "qualified",
                "follow_up",
                "converted"
            ]
        ]

        recent = q.order_by(
            Lead.created_at.desc()
        ).limit(8).all()

        return jsonify({
            "leads": total,
            "accepted": accepted,
            "converted": converted,
            "pending": q.filter_by(
                approval_status="pending"
            ).count(),
            "rejected": q.filter_by(
                approval_status="rejected"
            ).count(),
            "due_followups": q.filter(
                Lead.next_follow_up_at <= now(),
                Lead.next_follow_up_at.is_not(None)
            ).count(),
            "conversion_rate": (
                round(
                    converted / accepted * 100,
                    1
                )
                if accepted
                else 0
            ),
            "pipeline": pipeline,
            "recent": [
                x.to_dict()
                for x in recent
            ]
        })

    @app.get("/api/admin/analytics")
    @require_user("admin")
    def analytics(user):
        end = datetime.now(
            timezone.utc
        ).date()

        start = end - timedelta(
            days=29
        )

        try:
            if request.args.get("from"):
                start = datetime.fromisoformat(
                    request.args["from"]
                ).date()

            if request.args.get("to"):
                end = datetime.fromisoformat(
                    request.args["to"]
                ).date()

        except ValueError:
            return jsonify({
                "error": "Dates must use YYYY-MM-DD."
            }), 400

        if end < start:
            return jsonify({
                "error": "Invalid date range."
            }), 400

        start_dt = datetime.combine(
            start,
            datetime.min.time(),
            tzinfo=timezone.utc
        )

        end_dt = datetime.combine(
            end,
            datetime.max.time(),
            tzinfo=timezone.utc
        )

        q = Lead.query.filter(
            Lead.created_at.between(
                start_dt,
                end_dt
            )
        )

        total = q.count()

        accepted = q.filter_by(
            approval_status="accepted"
        ).count()

        converted = q.filter_by(
            status="converted"
        ).count()

        statuses = [
            {
                "status": s,
                "count": q.filter_by(
                    status=s
                ).count()
            }
            for s in [
                "new",
                "contacted",
                "qualified",
                "follow_up",
                "converted"
            ]
        ]

        sources = (
            db.session.query(
                Lead.source,
                func.count(Lead.id)
            )
            .filter(
                Lead.created_at.between(
                    start_dt,
                    end_dt
                )
            )
            .group_by(
                Lead.source
            )
            .order_by(
                func.count(Lead.id).desc()
            )
            .all()
        )

        emp_rows = (
            db.session.query(
                User.id,
                User.full_name,
                User.profile_picture,
                func.count(Lead.id),
                func.sum(
                    case(
                        (
                            Lead.approval_status == "accepted",
                            1
                        ),
                        else_=0
                    )
                ),
                func.sum(
                    case(
                        (
                            Lead.status == "converted",
                            1
                        ),
                        else_=0
                    )
                )
            )
            .join(
                Lead,
                Lead.ambassador_id == User.id
            )
            .filter(
                User.role == "ambassador",
                Lead.created_at.between(
                    start_dt,
                    end_dt
                )
            )
            .group_by(
                User.id
            )
            .order_by(
                func.count(Lead.id).desc()
            )
            .all()
        )

        daily = []
        cursor = start

        while cursor <= end:
            nxt = cursor + timedelta(
                days=1
            )

            s = datetime.combine(
                cursor,
                datetime.min.time(),
                tzinfo=timezone.utc
            )

            e = datetime.combine(
                nxt,
                datetime.min.time(),
                tzinfo=timezone.utc
            )

            daily.append({
                "date": cursor.isoformat(),
                "count": q.filter(
                    Lead.created_at >= s,
                    Lead.created_at < e
                ).count()
            })

            cursor = nxt

        employees = [
            {
                "id": r[0],
                "name": r[1],
                "profile_picture": r[2],
                "leads": int(r[3] or 0),
                "accepted": int(r[4] or 0),
                "converted": int(r[5] or 0),
                "conversion_rate": (
                    round(
                        (
                            int(r[5] or 0)
                            / int(r[4] or 0)
                        ) * 100,
                        1
                    )
                    if r[4]
                    else 0
                )
            }
            for r in emp_rows
        ]

        return jsonify({
            "range": {
                "from": start.isoformat(),
                "to": end.isoformat()
            },
            "summary": {
                "total": total,
                "accepted": accepted,
                "rejected": q.filter_by(
                    approval_status="rejected"
                ).count(),
                "pending": q.filter_by(
                    approval_status="pending"
                ).count(),
                "converted": converted,
                "qualified": q.filter_by(
                    status="qualified"
                ).count(),
                "high_priority": q.filter_by(
                    priority="high"
                ).count(),
                "conversion_rate": (
                    round(
                        converted / accepted * 100,
                        1
                    )
                    if accepted
                    else 0
                )
            },
            "pipeline": statuses,
            "sources": [
                {
                    "source": s or "Unknown",
                    "count": int(c)
                }
                for s, c in sources
            ],
            "employees": employees[:50],
            "daily": daily
        })

    @app.get("/api/admin/export/leads.csv")
    @require_user("admin")
    def export_csv(user):
        out = io.StringIO()

        writer = csv.writer(out)

        writer.writerow([
            "ID",
            "Lead",
            "Phone",
            "Email",
            "Employee",
            "Institution",
            "Program",
            "Review",
            "Pipeline",
            "Priority",
            "Next Follow-up",
            "Created"
        ])

        for x in Lead.query.order_by(
            Lead.created_at.desc()
        ).all():
            writer.writerow([
                x.id,
                x.full_name,
                x.phone,
                x.email or "",
                (
                    x.ambassador.full_name
                    if x.ambassador
                    else ""
                ),
                x.institution or "",
                x.program_interest or "",
                x.approval_status,
                x.status,
                x.priority,
                (
                    x.next_follow_up_at.isoformat()
                    if x.next_follow_up_at
                    else ""
                ),
                x.created_at.isoformat()
            ])

        audit(
            user.id,
            "lead_exported",
            None,
            None,
            "CSV lead export"
        )

        db.session.commit()

        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition":
                    "attachment; filename=wirefizz_leads.csv"
            }
        )

    @app.post("/api/admin/import/leads")
    @require_user("admin")
    def import_leads(user):
        f = request.files.get("file")

        if not f:
            return jsonify({
                "error": "CSV file is required."
            }), 400

        try:
            textdata = f.read().decode(
                "utf-8-sig"
            )
        except UnicodeDecodeError:
            return jsonify({
                "error": "CSV must be UTF-8 encoded."
            }), 400

        reader = csv.DictReader(
            io.StringIO(textdata)
        )

        required = {
            "full_name",
            "phone"
        }

        if not required.issubset(
            {
                x.strip()
                for x in (
                    reader.fieldnames or []
                )
            }
        ):
            return jsonify({
                "error": (
                    "CSV must include "
                    "full_name and phone columns."
                )
            }), 400

        created = 0
        skipped = 0
        errors = []

        for i, row in enumerate(
            reader,
            start=2
        ):
            if i > 5002:
                break

            name = str(
                row.get(
                    "full_name",
                    ""
                )
            ).strip()

            phone = str(
                row.get(
                    "phone",
                    ""
                )
            ).strip()

            email = (
                str(
                    row.get(
                        "email",
                        ""
                    )
                ).strip().lower()
                or None
            )

            if not name or not phone:
                skipped += 1

                errors.append(
                    f"Row {i}: missing name or phone"
                )

                continue

            if email and not valid_email(email):
                skipped += 1

                errors.append(
                    f"Row {i}: invalid email"
                )

                continue

            if find_duplicate_lead(phone, email):
                skipped += 1
                continue

            assignee_id = None

            if row.get(
                "ambassador_email"
            ):
                a = User.query.filter(
                    func.lower(
                        User.email
                    ) == row[
                        "ambassador_email"
                    ].strip().lower(),
                    User.role == "ambassador"
                ).first()

                assignee_id = (
                    a.id
                    if a
                    else None
                )

            if not assignee_id:
                a = User.query.filter_by(
                    role="ambassador",
                    account_status="active"
                ).order_by(
                    User.id.asc()
                ).first()

                if not a:
                    skipped += 1
                    continue

                assignee_id = a.id

            lead = Lead(
                ambassador_id=assignee_id,
                full_name=name[:120],
                phone=phone[:40],
                email=email,
                city=str(
                    row.get(
                        "city",
                        ""
                    )
                ).strip() or None,
                institution=str(
                    row.get(
                        "institution",
                        ""
                    )
                ).strip() or None,
                program_interest=str(
                    row.get(
                        "program_interest",
                        ""
                    )
                ).strip() or None,
                source=str(
                    row.get(
                        "source",
                        ""
                    )
                ).strip() or "import",
                notes=str(
                    row.get(
                        "notes",
                        ""
                    )
                ).strip() or None,
                approval_status="accepted",
                status=(
                    str(
                        row.get(
                            "status",
                            "new"
                        )
                    )
                    if row.get("status")
                    in PIPELINE
                    else "new"
                ),
                priority=(
                    str(
                        row.get(
                            "priority",
                            "medium"
                        )
                    )
                    if row.get("priority")
                    in PRIORITY
                    else "medium"
                )
            )

            db.session.add(lead)
            db.session.flush()

            created += 1

        audit(
            user.id,
            "leads_imported",
            None,
            None,
            f"Imported {created} leads; skipped {skipped}"
        )

        db.session.commit()

        return jsonify({
            "created": created,
            "skipped": skipped,
            "errors": errors[:20]
        })

    @app.post("/api/admin/leads/merge")
    @require_user("admin")
    def merge_leads(user):
        d = request.get_json(silent=True) or {}

        try:
            keep = db.session.get(
                Lead,
                int(d.get("keep_id"))
            )

            remove = db.session.get(
                Lead,
                int(d.get("remove_id"))
            )

        except (
            TypeError,
            ValueError
        ):
            keep = None
            remove = None

        if (
            not keep
            or not remove
            or keep.id == remove.id
        ):
            return jsonify({
                "error": "Two different valid leads are required."
            }), 400

        # Move the child rows with a bulk UPDATE, then expire the in-memory
        # collections. Otherwise deleting `remove` would cascade to the
        # (already re-parented) rows still listed in remove.activities/tasks
        # and silently destroy the merged history.
        LeadActivity.query.filter_by(
            lead_id=remove.id
        ).update({
            "lead_id": keep.id
        })

        Task.query.filter_by(
            lead_id=remove.id
        ).update({
            "lead_id": keep.id
        })

        db.session.flush()
        db.session.expire(remove, ["activities", "tasks"])
        db.session.expire(keep, ["activities", "tasks"])

        if not keep.email:
            keep.email = remove.email

        if not keep.institution:
            keep.institution = remove.institution

        if not keep.notes:
            keep.notes = remove.notes

        audit(
            user.id,
            "leads_merged",
            "lead",
            keep.id,
            f"Merged lead #{remove.id} into #{keep.id}"
        )

        db.session.delete(remove)
        db.session.commit()

        return jsonify({
            "message": "Leads merged.",
            "lead": keep.to_dict()
        })

    return app


def bootstrap_database():
    """Create tables + run the compatibility migration exactly once at a time.

    Gunicorn starts several workers at the same moment; without a lock they race
    on CREATE TABLE / ALTER TABLE against a fresh database and one of them
    crashes. A PostgreSQL advisory lock serialises the bootstrap.
    """
    lock_id = 727463726  # arbitrary application-wide constant
    connection = db.engine.connect()

    try:
        if db.engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": lock_id})
            connection.commit()

        try:
            db.create_all()
            migrate_legacy_schema()
        finally:
            db.session.remove()

            if db.engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                connection.commit()
    finally:
        connection.close()


def migrate_legacy_schema():
    """
    Idempotent compatibility migration for databases created
    by older LMS builds.

    Flask-SQLAlchemy's create_all() creates missing tables but
    does not add missing columns to existing tables.

    The production build therefore performs a small,
    explicit PostgreSQL migration at startup so existing
    installations keep working.
    """

    inspector = inspect(
        db.engine
    )

    tables = set(
        inspector.get_table_names()
    )

    if "users" in tables:
        cols = {
            c["name"]
            for c in inspector.get_columns(
                "users"
            )
        }

        if "account_status" not in cols:
            if "is_active" in cols:
                db.session.execute(
                    text(
                        "ALTER TABLE users "
                        "ADD COLUMN account_status "
                        "VARCHAR(20) NOT NULL DEFAULT 'active'"
                    )
                )

                db.session.execute(
                    text(
                        "UPDATE users SET account_status = "
                        "CASE WHEN COALESCE(is_active, TRUE) "
                        "THEN 'active' ELSE 'blocked' END"
                    )
                )

            else:
                db.session.execute(
                    text(
                        "ALTER TABLE users "
                        "ADD COLUMN account_status "
                        "VARCHAR(20) NOT NULL DEFAULT 'active'"
                    )
                )

        if "is_active" not in cols:
            db.session.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN is_active "
                    "BOOLEAN NOT NULL DEFAULT TRUE"
                )
            )

            db.session.execute(
                text(
                    "UPDATE users SET is_active = "
                    "(account_status = 'active')"
                )
            )

        if "updated_at" not in cols:
            db.session.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN updated_at TIMESTAMPTZ"
                )
            )

        if "last_login_at" not in cols:
            db.session.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN last_login_at TIMESTAMPTZ"
                )
            )

        # Keep the legacy flag synchronized enough for old
        # rows/apps while the LMS uses account_status for
        # authorization decisions.
        db.session.execute(
            text(
                "UPDATE users SET is_active = "
                "(account_status = 'active') "
                "WHERE is_active IS DISTINCT FROM "
                "(account_status = 'active')"
            )
        )

    if "leads" in tables:
        cols = {
            c["name"]
            for c in inspector.get_columns(
                "leads"
            )
        }

        adds = {
            "approval_status":
                "VARCHAR(30) NOT NULL DEFAULT 'accepted'",
            "rejection_reason":
                "VARCHAR(500)",
            "priority":
                "VARCHAR(20) NOT NULL DEFAULT 'medium'",
            "last_contacted_at":
                "TIMESTAMPTZ",
            "next_follow_up_at":
                "TIMESTAMPTZ",
        }

        for name, ddl in adds.items():
            if name not in cols:
                db.session.execute(
                    text(
                        f"ALTER TABLE leads "
                        f"ADD COLUMN {name} {ddl}"
                    )
                )

    if "audit_logs" in tables:
        cols = {
            c["name"]
            for c in inspector.get_columns(
                "audit_logs"
            )
        }

        if "ip_address" not in cols:
            db.session.execute(
                text(
                    "ALTER TABLE audit_logs "
                    "ADD COLUMN ip_address VARCHAR(64)"
                )
            )

        if "user_agent" not in cols:
            db.session.execute(
                text(
                    "ALTER TABLE audit_logs "
                    "ADD COLUMN user_agent VARCHAR(500)"
                )
            )

    if "refresh_sessions" in tables:
        db.session.execute(
            text(
                "DELETE FROM refresh_sessions "
                "WHERE expires_at < NOW() "
                "- INTERVAL '30 days'"
            )
        )

    db.session.commit()


if __name__ == "__main__":
    create_app().run(
        host=os.getenv(
            "HOST",
            "127.0.0.1"
        ),
        port=int(
            os.getenv(
                "PORT",
                "5000"
            )
        ),
        debug=Config.APP_ENV != "production"
    )