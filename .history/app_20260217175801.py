from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, conint, constr

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
SUBMISSIONS_DIR = ROOT / "submissions"
SUBMISSIONS_DIR.mkdir(exist_ok=True)

# ---------- Validation (fail-fast) ----------
ParticipantId = constr(min_length=1, max_length=128)
NonEmptyStr = constr(min_length=1, max_length=512)

class Trial(BaseModel):
    clip_id: NonEmptyStr
    model: NonEmptyStr
    rating: conint(ge=1, le=10)
    order_index: conint(ge=0, le=100000)
    rt_ms: conint(ge=0, le=10000000)
    listen_count: conint(ge=0, le=100000)

class SubmitPayload(BaseModel):
    participant_id: ParticipantId
    style_label: NonEmptyStr
    reference_audio: NonEmptyStr
    trials: List[Trial] = Field(min_length=1, max_length=10000)

# ---------- App ----------

print("starting app...")
app = FastAPI()

# Serve static under /static so it's explicit
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

@app.get("/")
def index():
    print(PUBLIC_DIR)
    return FileResponse(PUBLIC_DIR / "index.html")

def _safe(s: str) -> str:
    # filename-safe, short, deterministic-ish
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:40] if s else "anon"

@app.post("/api/submit")
def submit(payload: SubmitPayload):
    # UTC timestamp for stable ordering
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pid = _safe(payload.participant_id)
    style = _safe(payload.style_label)
    nonce = secrets.token_hex(4)  # prevents collisions

    filename = f"{ts}__{style}__{pid}__{nonce}.json"
    out_path = SUBMISSIONS_DIR / filename

    # atomic write (write temp then replace)
    tmp_path = out_path.with_suffix(".json.tmp")
    data = payload.model_dump()

    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_path.replace(out_path)

    return {"ok": True, "file": filename}