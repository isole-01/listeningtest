from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, conint, constr

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
AUDIO_DIR = PUBLIC_DIR / "audio"

STYLE_LABEL = "Blues Piano"
MODEL_DIRS = ["text", "mixed"]
REFERENCE_DIR_NAME = "reference"

GSHEETS_WEBAPP_URL = os.environ["GSHEETS_WEBAPP_URL"]
GSHEETS_SHARED_SECRET = os.environ["GSHEETS_SHARED_SECRET"]

# ---------- Validation (fail-fast) ----------
ParticipantId = constr(min_length=1, max_length=128)
NonEmptyStr = constr(min_length=1, max_length=512)


class Trial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip_id: NonEmptyStr
    model: NonEmptyStr
    step: conint(ge=0, le=100000)
    rating: conint(ge=1, le=10)
    order_index: conint(ge=0, le=100000)
    rt_ms: conint(ge=0, le=10000000)
    listen_count: conint(ge=0, le=100000)
    picked_folder: NonEmptyStr
    picked_file: NonEmptyStr


class SubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participant_id: ParticipantId
    style_label: NonEmptyStr
    reference_audio: NonEmptyStr
    trials: list[Trial] = Field(min_length=1, max_length=10000)
    meta: dict[str, str] = Field(default_factory=dict)


# ---------- App ----------
app = FastAPI()
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")


@app.get("/")
def index():
    index_path = PUBLIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=500, detail=f"Missing index file: {index_path}")
    return FileResponse(index_path)


def _fail(msg: str) -> None:
    raise HTTPException(status_code=500, detail=msg)


def _list_reference_files(reference_dir: Path) -> list[str]:
    if not reference_dir.is_dir():
        _fail(f"Missing reference directory: {reference_dir}")

    allowed_suffixes = {".wav", ".mp3", ".ogg", ".m4a", ".flac"}
    files = sorted(
        p.name
        for p in reference_dir.iterdir()
        if p.is_file() and p.suffix.lower() in allowed_suffixes
    )

    if not files:
        _fail(f"No audio files found in reference directory: {reference_dir}")

    return files


def _parse_step_filename(filename: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)_(\d+)\.wav", filename)
    if match is None:
        _fail(
            f"Bad filename '{filename}'. Expected format '{{step}}_{{index}}.wav', "
            f"for example '20_3.wav'."
        )

    step = int(match.group(1))
    index = int(match.group(2))
    return step, index


def _post_to_google_sheets(data: dict) -> None:
    body = {
        "secret": GSHEETS_SHARED_SECRET,
        "payload": data,
    }

    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.post(GSHEETS_WEBAPP_URL, json=body)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Google Sheets endpoint: {e}",
        ) from e

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Google Sheets endpoint returned HTTP {response.status_code}: {response.text}",
        )

    try:
        result = response.json()
    except ValueError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Google Sheets endpoint returned invalid JSON: {e}",
        ) from e

    if result.get("ok") is not True:
        raise HTTPException(
            status_code=502,
            detail=f"Google Sheets endpoint error: {result.get('error', 'unknown error')}",
        )


@app.get("/api/manifest")
def get_manifest():
    if not AUDIO_DIR.is_dir():
        _fail(f"Missing audio directory: {AUDIO_DIR}")

    reference_dir = AUDIO_DIR / REFERENCE_DIR_NAME
    reference_files = _list_reference_files(reference_dir)

    conditions: list[dict] = []

    for model_name in MODEL_DIRS:
        model_dir = AUDIO_DIR / model_name
        if not model_dir.is_dir():
            _fail(f"Missing model directory: {model_dir}")

        grouped: dict[int, list[tuple[int, str]]] = defaultdict(list)

        wav_files = sorted(
            p for p in model_dir.iterdir()
            if p.is_file() and p.suffix.lower() == ".wav"
        )
        if not wav_files:
            _fail(f"No .wav files found in model directory: {model_dir}")

        for path in wav_files:
            step, index = _parse_step_filename(path.name)
            grouped[step].append((index, path.name))

        for step in sorted(grouped):
            files_sorted = [
                name for index, name in sorted(grouped[step], key=lambda x: x[0])
            ]
            conditions.append(
                {
                    "model": model_name,
                    "step": step,
                    "files": files_sorted,
                }
            )

    if not conditions:
        _fail("No conditions were generated from the audio folders.")

    return {
        "style_label": STYLE_LABEL,
        "base_url": "/static/audio",
        "reference": {
            "folder": REFERENCE_DIR_NAME,
            "files": reference_files,
        },
        "conditions": conditions,
    }


@app.post("/api/submit")
def submit(payload: SubmitPayload):
    data = payload.model_dump()
    _post_to_google_sheets(data)
    return {"ok": True}