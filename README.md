# WireFizz LMS — Production-Ready Full Build

A complete campus ambassador, admissions lead and CRM workspace built around the WireFizz visual language: dark charcoal surfaces, warm ivory paper, lime accent, bold editorial headings and concise uppercase operational labels. The branding direction is based on the current WireFizz site. See `README` and the deployment notes below.

## Included stages

1. Project foundation + PostgreSQL
2. Authentication + role separation
3. Lead capture + approval workflow
4. Lead operations + activities + follow-ups
5. Admin analytics + reporting
6. Advanced operations: assignment, bulk actions, CSV import/export, duplicate detection and merge
7. Profile management + picture upload + password change
8. JWT access/refresh session architecture with HttpOnly cookies + CSRF protection
9. Notifications + audit trail + account blocking/deletion
10. Production packaging with Gunicorn, Nginx and Docker Compose

## Role rules

### Employee / Campus Ambassador
- Sees only leads assigned to themselves.
- Creates leads; new submissions enter `pending` review.
- Can edit contact/context fields, add activities and follow-up tasks.
- Cannot approve/reject a lead, change official pipeline stage, reassign leads, delete leads or manage employees.

### Admin
- Sees every lead and every employee.
- Accepts/rejects leads, sets pipeline stage and priority.
- Assigns/reassigns leads.
- Creates, blocks, unblocks and permanently deletes employees.
- Permanently deletes leads.
- Imports/exports CSV and can merge duplicate leads.
- Views analytics, audit logs, notifications and follow-up queues.

## Authentication model

The SPA uses short-lived JWT access cookies and rotated JWT refresh cookies. Both tokens are HttpOnly; JWT CSRF protection uses a readable CSRF cookie and `X-CSRF-TOKEN` header. Refresh JTIs are persisted server-side and revoked on rotation, logout, employee blocking, and password changes. The frontend first checks `/api/auth/me` and only attempts refresh when the refresh-CSRF cookie exists, so an unauthenticated page load does not generate an expected `/api/auth/refresh` 401. A new browser tab can reuse the authenticated browser session without another login. Different accounts should be tested in separate browser profiles/incognito sessions because cookies are intentionally shared by tabs of the same browser profile.

## Local development

### 1. PostgreSQL
Create a PostgreSQL database named `wirefizz_lms` and note the `postgres` password.

### 2. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
```

Edit `backend/.env` with your database URL and strong secrets.

Create an admin:

```powershell
python seed_admin.py
```

Run:

```powershell
python run.py
```

The API runs on `http://127.0.0.1:5000`.

### 3. Frontend

Open a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Existing database compatibility

The backend includes a one-time compatibility bootstrap that adds the newer columns used by older WireFizz LMS databases. For a brand-new production environment, create the database first and deploy the application as documented.

## Production deployment with Docker

Copy `.env.example` to `.env` at the project root and provide strong random values.

```bash
docker compose up -d --build
```

The frontend is served by Nginx on port 80 and proxies `/api/*` to the backend. PostgreSQL and avatar uploads use persistent Docker volumes.

### Production checklist

- Use HTTPS and set `JWT_COOKIE_SECURE=true`.
- Use strong unique `SECRET_KEY`, `JWT_SECRET_KEY` and PostgreSQL credentials.
- Set `CORS_ORIGINS` to the exact public origin(s), never `*`.
- Redis-backed rate limiting is included in the Docker Compose deployment; for multiple external instances, use a shared Redis service.
- Put the app behind a reverse proxy/load balancer and restrict database network access.
- Back up PostgreSQL regularly and test restore procedures.
- Keep dependencies updated and scan images/packages before deployment.
- Monitor application, database and reverse-proxy logs.
- Do not commit `.env`, uploaded files or credentials to Git.

## API highlights

- `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout`
- `/api/auth/me`, `/api/auth/register`, `/api/auth/change-password`
- `/api/profile`, `/api/profile/avatar`
- `/api/leads`, `/api/leads/:id`, `/api/leads/:id/activities`, `/api/leads/:id/tasks`
- `/api/tasks`, `/api/notifications`
- `/api/admin/users`, `/api/admin/leads/bulk`, `/api/admin/leads/merge`
- `/api/admin/analytics`, `/api/admin/audit-logs`
- `/api/admin/export/leads.csv`, `/api/admin/import/leads`

## Important limitation

No software package can honestly be promised as literally “100% mistake-free” before it is installed, configured, security-reviewed and load-tested in the target production environment. This package is designed as a production-oriented baseline with explicit security and deployment controls; run the included local tests and a staging deployment before going live.

## Verification checklist

Before production handover, run the following test sequence in staging: fresh login, reload, new-tab reuse, expired access token refresh, logout, password change, blocked-account login, employee isolation, admin-only routes, lead create/edit/approve/reject/delete, assignment/reassignment, task create/complete, notification read, avatar upload/remove, CSV import/export, duplicate detection/merge, audit log creation, backup/restore and Docker health checks. The application includes defensive error handlers and server-side authorization, but final production readiness still depends on the target infrastructure and security/load testing.
