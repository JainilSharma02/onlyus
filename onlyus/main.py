import os, re, json, uuid, datetime as dt, bcrypt
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from fastapi import (FastAPI, Depends, HTTPException, UploadFile, File, Form, Request,
                     Response, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import TimestampSigner, BadSignature
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker

DB = os.getenv("DATABASE_URL", "")
SECRET = os.getenv("SECRET_KEY", "dev-only-change-me")
SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
SESSION_SECS = 60 * 60 * 24 * 7
BASE_DIR = Path(__file__).resolve().parent

# Smart uploads directory — works on Railway, local, anywhere
_up_candidates = [
    Path(os.getenv("UPLOAD_DIR", "")),
    BASE_DIR / "uploads",
    Path("/tmp/uploads"),
]
UP = None
for _p in _up_candidates:
    if not str(_p).strip("/"):
        continue
    try:
        _p.mkdir(parents=True, exist_ok=True)
        UP = _p
        break
    except Exception:
        continue
if UP is None:
    UP = Path("/tmp/uploads")
    UP.mkdir(parents=True, exist_ok=True)

ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
           "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
           "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/webm": "weba", "audio/ogg": "ogg", "audio/wav": "wav"}
MAX_BYTES = 60 * 1024 * 1024
now = lambda: dt.datetime.utcnow()

def init_db(db_url):
    if db_url and not db_url.startswith("sqlite"):
        try:
            eng = create_engine(db_url, pool_pre_ping=True)
            with eng.connect():
                pass
            print(f"[DB] PostgreSQL connected successfully.")
            return eng
        except Exception as err:
            print(f"[DB] PostgreSQL unavailable ({err}). Falling back to SQLite local database.")
    
    try:
        _sqlite_path = str(BASE_DIR / "onlyus.db")
        # Test if writable
        with open(_sqlite_path, "a") as f:
            pass
    except Exception:
        _sqlite_path = "/tmp/onlyus.db"
    sqlite_url = f"sqlite:///{_sqlite_path}"
    print(f"[DB] Running on SQLite: {sqlite_url}")
    return create_engine(sqlite_url, connect_args={"check_same_thread": False})

engine = init_db(DB)
Session = sessionmaker(engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True); username = Column(String, unique=True)
    name = Column(String); pw = Column(String); avatar = Column(String, nullable=True)
class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True); sender = Column(ForeignKey("users.id"))
    body = Column(Text, default=""); kind = Column(String, default="text"); file = Column(String, nullable=True)
    reply_to = Column(Integer, nullable=True); reaction = Column(String, nullable=True)
    created = Column(DateTime, default=now); seen = Column(Boolean, default=False)
class Snap(Base):
    __tablename__ = "snaps"
    id = Column(Integer, primary_key=True); owner = Column(ForeignKey("users.id"))
    file = Column(String); mime = Column(String); caption = Column(Text, default="")
    sticker = Column(String, default=""); album = Column(String, default="Random Moments")
    created = Column(DateTime, default=now)
class Letter(Base):
    __tablename__ = "letters"
    id = Column(Integer, primary_key=True); author = Column(ForeignKey("users.id"))
    kind = Column(String, default="letter")  # letter | openwhen | tell
    title = Column(String); body = Column(Text); open_at = Column(DateTime, default=now)
    created = Column(DateTime, default=now)
class Song(Base):
    __tablename__ = "songs"
    id = Column(Integer, primary_key=True); owner = Column(ForeignKey("users.id"))
    title = Column(String); artist = Column(String, default=""); url = Column(String, nullable=True)
    file = Column(String, nullable=True); cover = Column(String, nullable=True)
    ours = Column(Boolean, default=False)
class Bucket(Base):
    __tablename__ = "bucket"
    id = Column(Integer, primary_key=True); text = Column(String); done = Column(Boolean, default=False)
class Kv(Base):
    __tablename__ = "kv"
    key = Column(String, primary_key=True); value = Column(Text)

Base.metadata.create_all(engine)

def seed():
    defaults = {
        "1": "him:J:himher0203",
        "2": "her:D:himher0203"
    }
    with Session() as s:
        for i in ("1", "2"):
            raw = os.getenv(f"USER_{i}", defaults.get(i))
            if not raw: continue
            parts = raw.split(":", 2)
            if len(parts) != 3: continue
            u, n, p = parts
            existing = s.query(User).filter_by(username=u).first()
            if not existing:
                s.add(User(username=u, name=n, pw=bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()))
            else:
                existing.name = n
                existing.pw = bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
        s.commit()
seed()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
signer = TimestampSigner(SECRET)

def user_from_token(tok):
    try:
        uid = int(signer.unsign(tok, max_age=SESSION_SECS).decode())
    except (BadSignature, ValueError, TypeError):
        return None
    with Session() as s:
        return s.get(User, uid)

def auth(request: Request):
    u = user_from_token(request.cookies.get("sid", ""))
    if not u: raise HTTPException(401, "Login karo")
    return u

def partner_of(u):
    with Session() as s:
        return s.query(User).filter(User.id != u.id).first()

def ser(o, skip=()):
    d = {c.name: getattr(o, c.name) for c in o.__table__.columns if c.name not in skip}
    return {k: (v.isoformat() + "Z" if isinstance(v, dt.datetime) else v) for k, v in d.items()}

class Login(BaseModel):
    username: str; password: str

attempts = {}
@app.post("/api/login")
def login(d: Login, request: Request, resp: Response):
    ip = request.client.host if request.client else "?"
    if attempts.get(ip, 0) >= 8: raise HTTPException(429, "Bahut attempts, thodi der baad try karo")
    with Session() as s:
        u = s.query(User).filter_by(username=d.username.strip().lower()).first()
    if not u or not bcrypt.checkpw(d.password.encode(), u.pw.encode()):
        attempts[ip] = attempts.get(ip, 0) + 1
        raise HTTPException(401, "Wrong Password")
    attempts.pop(ip, None)
    resp.set_cookie("sid", signer.sign(str(u.id)).decode(), max_age=SESSION_SECS,
                    httponly=True, samesite="strict", secure=SECURE)
    return {"ok": True}

@app.post("/api/logout")
def logout(resp: Response):
    resp.delete_cookie("sid"); return {"ok": True}

def get_kv(s, key, default):
    r = s.get(Kv, key); return json.loads(r.value) if r else default

@app.get("/api/me")
def me(u: User = Depends(auth)):
    p = partner_of(u)
    with Session() as s:
        cfg = get_kv(s, "settings", {"start": None, "dates": [], "love": 0})
    return {"me": ser(u, ("pw",)), "partner": ser(p, ("pw",)) if p else None, "settings": cfg}

@app.put("/api/settings")
def put_settings(d: dict, u: User = Depends(auth)):
    keep = {"start": d.get("start"), "dates": d.get("dates", [])[:20], "love": int(d.get("love", 0))}
    with Session() as s:
        s.merge(Kv(key="settings", value=json.dumps(keep))); s.commit()
    return keep

# ---------- files (private) ----------
def save_upload(f: UploadFile):
    ext = ALLOWED.get((f.content_type or "").split(";")[0])
    if not ext: raise HTTPException(400, "Ye file type allowed nahi hai")
    data = f.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES: raise HTTPException(413, "File 60MB se badi hai")
    name = f"{uuid.uuid4().hex}.{ext}"
    (UP / name).write_bytes(data)
    return name, f.content_type.split(";")[0]

@app.post("/api/upload")
def upload(file: UploadFile = File(...), u: User = Depends(auth)):
    name, mime = save_upload(file)
    return {"file": name, "mime": mime}

@app.get("/api/file/{name}")
def get_file(name: str, u: User = Depends(auth)):
    if not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{2,4}", name): raise HTTPException(404)
    p = UP / name
    if not p.exists(): raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})

# ---------- chat ----------
conns = {}
@app.get("/api/messages")
def messages(u: User = Depends(auth)):
    with Session() as s:
        return [ser(m) for m in s.query(Message).order_by(Message.id.desc()).limit(200).all()][::-1]

async def push(uids, payload):
    for uid in uids:
        for ws in list(conns.get(uid, [])):
            try: await ws.send_json(payload)
            except Exception: conns[uid].discard(ws)

@app.websocket("/ws")
async def ws_chat(ws: WebSocket):
    u = user_from_token(ws.cookies.get("sid", ""))
    if not u: await ws.close(code=4401); return
    await ws.accept(); conns.setdefault(u.id, set()).add(ws)
    p = partner_of(u); both = [u.id, p.id] if p else [u.id]
    await push([p.id] if p else [], {"t": "presence", "online": True})
    try:
        while True:
            d = await ws.receive_json(); t = d.get("t")
            with Session() as s:
                if t == "msg":
                    kind = d.get("kind", "text")
                    if kind not in ("text", "image", "audio"): continue
                    f = d.get("file")
                    if f and not re.fullmatch(r"[a-f0-9]{32}\.[a-z0-9]{2,4}", f): continue
                    m = Message(sender=u.id, body=(d.get("body") or "")[:4000], kind=kind, file=f, reply_to=d.get("reply_to"))
                    s.add(m); s.commit(); await push(both, {"t": "msg", "m": ser(m)})
                elif t == "react":
                    m = s.get(Message, d.get("id"))
                    if m: m.reaction = d.get("emoji") if m.reaction != d.get("emoji") else None; s.commit(); await push(both, {"t": "react", "id": m.id, "emoji": m.reaction})
                elif t == "seen":
                    ids = [m.id for m in s.query(Message).filter(Message.sender != u.id, Message.seen == False).all()]
                    s.query(Message).filter(Message.sender != u.id, Message.seen == False).update({"seen": True}); s.commit()
                    if ids: await push(both, {"t": "seen", "ids": ids})
                elif t == "hug":
                    await push(both, {"t": "hug", "from": u.name})
    except WebSocketDisconnect: pass
    finally:
        conns.get(u.id, set()).discard(ws)
        await push([p.id] if p else [], {"t": "presence", "online": bool(conns.get(u.id))})

@app.get("/api/online")
def online(u: User = Depends(auth)):
    p = partner_of(u); return {"online": bool(p and conns.get(p.id))}

# ---------- snaps / memories ----------
@app.get("/api/snaps")
def snaps(u: User = Depends(auth)):
    with Session() as s: return [ser(x) for x in s.query(Snap).order_by(Snap.created.desc()).all()]

@app.post("/api/snaps")
def add_snap(file: UploadFile = File(...), caption: str = Form(""), sticker: str = Form(""),
             album: str = Form("Random Moments"), u: User = Depends(auth)):
    name, mime = save_upload(file)
    if not mime.startswith(("image/", "video/")): raise HTTPException(400, "Sirf photo ya video")
    with Session() as s:
        x = Snap(owner=u.id, file=name, mime=mime, caption=caption[:1000], sticker=sticker[:8], album=album[:40])
        s.add(x); s.commit(); return ser(x)

@app.delete("/api/snaps/{sid}")
def del_snap(sid: int, u: User = Depends(auth)):
    with Session() as s:
        x = s.get(Snap, sid)
        if not x or x.owner != u.id: raise HTTPException(403, "Sirf apni upload delete kar sakte ho")
        (UP / x.file).unlink(missing_ok=True); s.delete(x); s.commit()
    return {"ok": True}

# ---------- letters ----------
class LetterIn(BaseModel):
    title: str; body: str; kind: str = "letter"; open_at: str | None = None

@app.get("/api/letters")
def letters(u: User = Depends(auth)):
    with Session() as s:
        out = []
        for l in s.query(Letter).order_by(Letter.created.desc()).all():
            d = ser(l); d["mine"] = l.author == u.id; d["locked"] = l.open_at > now()
            if d["locked"] and not d["mine"]: d["body"] = ""   # server hides it until open date
            out.append(d)
        return out

@app.post("/api/letters")
def add_letter(d: LetterIn, u: User = Depends(auth)):
    if d.kind not in ("letter", "openwhen", "tell"): raise HTTPException(400)
    at = dt.datetime.fromisoformat(d.open_at.replace("Z", "")) if d.open_at else now()
    with Session() as s:
        l = Letter(author=u.id, kind=d.kind, title=d.title[:120], body=d.body[:10000], open_at=at)
        s.add(l); s.commit(); return ser(l)

@app.delete("/api/letters/{lid}")
def del_letter(lid: int, u: User = Depends(auth)):
    with Session() as s:
        l = s.get(Letter, lid)
        if not l or l.author != u.id: raise HTTPException(403)
        s.delete(l); s.commit()
    return {"ok": True}

# ---------- music ----------
@app.get("/api/songs")
def songs(u: User = Depends(auth)):
    with Session() as s: return [ser(x) for x in s.query(Song).order_by(Song.id).all()]

@app.post("/api/songs")
def add_song(title: str = Form(...), artist: str = Form(""), url: str = Form(""),
             audio: UploadFile | None = File(None), cover: UploadFile | None = File(None), u: User = Depends(auth)):
    if url and not re.match(r"https://(www\.)?(youtube\.com|youtu\.be|open\.spotify\.com|music\.apple\.com|soundcloud\.com)/", url):
        raise HTTPException(400, "Sirf YouTube/Spotify/Apple Music/SoundCloud links allowed")
    f = save_upload(audio)[0] if audio else None
    c = save_upload(cover)[0] if cover else None
    with Session() as s:
        x = Song(owner=u.id, title=title[:120], artist=artist[:120], url=url or None, file=f, cover=c)
        s.add(x); s.commit(); return ser(x)

@app.put("/api/songs/{sid}/ours")
def ours(sid: int, u: User = Depends(auth)):
    with Session() as s:
        s.query(Song).update({"ours": False}); x = s.get(Song, sid); x.ours = True; s.commit()
    return {"ok": True}

@app.delete("/api/songs/{sid}")
def del_song(sid: int, u: User = Depends(auth)):
    with Session() as s:
        x = s.get(Song, sid)
        if not x or x.owner != u.id: raise HTTPException(403)
        s.delete(x); s.commit()
    return {"ok": True}

# ---------- bucket list ----------
@app.get("/api/bucket")
def bucket(u: User = Depends(auth)):
    with Session() as s: return [ser(x) for x in s.query(Bucket).order_by(Bucket.id).all()]

@app.post("/api/bucket")
def add_b(d: dict, u: User = Depends(auth)):
    with Session() as s:
        x = Bucket(text=str(d.get("text", ""))[:200]); s.add(x); s.commit(); return ser(x)

@app.put("/api/bucket/{bid}")
def tog_b(bid: int, u: User = Depends(auth)):
    with Session() as s:
        x = s.get(Bucket, bid); x.done = not x.done; s.commit(); return ser(x)

@app.delete("/api/bucket/{bid}")
def del_b(bid: int, u: User = Depends(auth)):
    with Session() as s:
        x = s.get(Bucket, bid)
        if x: s.delete(x); s.commit()
    return {"ok": True}

@app.post("/api/avatar")
def avatar(file: UploadFile = File(...), u: User = Depends(auth)):
    name, mime = save_upload(file)
    if not mime.startswith("image/"): raise HTTPException(400)
    with Session() as s:
        x = s.get(User, u.id); x.avatar = name; s.commit()
    return {"avatar": name}

static_dir = BASE_DIR / "static"
@app.get("/")
def index():
    return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-store"})
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
