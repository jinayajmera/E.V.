# Deploying the application

The app is a standard WSGI application (`app:create_app()`), so it can run anywhere Python and MySQL are available.

## Option A — your own machine / a server (Waitress)
```powershell
pip install -r requirements.txt
python scripts\upgrade_database.py
python serve.py            # http://127.0.0.1:8000   (HOST / PORT can be set in .env)
```

## Option B — Docker
```bash
docker build -t janmat .
docker run -p 8000:8000 --env-file .env -v janmat_uploads:/app/instance janmat
```
Use `DB_HOST=host.docker.internal` (Windows/Mac) or the database server's address in `.env`.

## HTTPS and a public address
Put the app behind a reverse proxy that terminates HTTPS (nginx, Caddy, a cloud load balancer) and set in `.env`:
```
COOKIE_SECURE=1
TRUST_PROXY=1
```
This turns on secure cookies and HSTS and makes the lock-out logic see the visitor's real IP address.

## Production checklist
* Set long random values for `SECRET_KEY` and `ID_PEPPER` (never change `ID_PEPPER` afterwards — it protects stored identity numbers).
* Use a dedicated MySQL user with rights on `evoting` only, not `root`.
* Back up the database and the `instance/uploads` folder (voter photographs) together.
* Keep `logs/app.log` private; it contains technical error details that users never see.
* Health probe for the hosting platform: `GET /health` → `{"status":"ok"}`.
