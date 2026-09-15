import os
from getpass import getpass
from app import create_app
from extensions import db
from models import User
from sqlalchemy import func

app=create_app()
with app.app_context():
    email=os.getenv("ADMIN_EMAIL") or input("Admin email: ").strip().lower()
    name=os.getenv("ADMIN_NAME") or input("Admin name: ").strip()
    password=os.getenv("ADMIN_PASSWORD") or getpass("Admin password (8+ chars): ")
    user=User.query.filter(func.lower(User.email)==email).first()
    if not user:
        user=User(full_name=name,email=email,role="admin",account_status="active");user.set_password(password);db.session.add(user)
    else:
        user.role="admin";user.account_status="active";user.full_name=name or user.full_name
        if password:user.set_password(password)
    db.session.commit();print(f"Admin ready: {email}")
