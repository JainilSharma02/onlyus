# ONLY US – Our Little Universe
1. `docker compose up -d`  (Postgres)
2. `cp .env.example .env` → passwords/SECRET_KEY badlo, phir `set -a; source .env; set +a`
3. `pip install -r requirements.txt`
4. `uvicorn main:app --host 0.0.0.0 --port 8000`
5. Phone pe kholo: http://<tumhara-ip>:8000  (camera/voice-note ke liye production me HTTPS zaroori hai – Caddy/Nginx + Let's Encrypt ya Cloudflare Tunnel use karo, aur COOKIE_SECURE=true rakho)

Backup: `pg_dump $DATABASE_URL > backup.sql` aur `uploads/` folder copy karo (cron me daalo).
Uploads `uploads/` me random naam se save hote hain, sirf login ke baad `/api/file/<naam>` se milte hain.
