"""FastAPI server for The Director."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

from .engine import GameEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="The Director", description="Interactive Visual Roleplay Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated images
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Game engine singleton
engine = GameEngine()


# --- Request/Response Models ---

class CreateStoryRequest(BaseModel):
    genre: str = "film_noir"
    premise: str | None = None

class ChoiceRequest(BaseModel):
    choice_id: int | None = None
    custom_text: str | None = None

class ForkRequest(BaseModel):
    from_node_id: str
    alt_choice_id: int | None = None
    custom_text: str | None = None


# --- Endpoints ---

@app.get("/api/genres")
def list_genres():
    """List available genres."""
    from .prompts import GENRE_TEMPLATES
    return {
        "genres": [
            {"id": gid, "name": g["name"]}
            for gid, g in GENRE_TEMPLATES.items()
        ]
    }

@app.post("/api/story/create")
def create_story(req: CreateStoryRequest):
    """Start a new story."""
    try:
        result = engine.create_story(genre=req.genre, custom_premise=req.premise)
        return result
    except Exception as e:
        logger.exception("Failed to create story")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/story/{session_id}/choose")
def make_choice(session_id: str, req: ChoiceRequest):
    """Make a choice in the story."""
    try:
        result = engine.make_choice(session_id=session_id, choice_id=req.choice_id, custom_text=req.custom_text)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Failed to process choice")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/story/{session_id}/fork")
def fork_timeline(session_id: str, req: ForkRequest):
    """Fork the timeline with an alternate choice."""
    try:
        result = engine.fork_timeline(
            session_id=session_id,
            node_id=req.from_node_id,
            alt_choice_id=req.alt_choice_id,
            custom_text=req.custom_text,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Failed to fork timeline")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/story/{session_id}/timeline")
def get_timeline(session_id: str):
    """Get the timeline tree for a session."""
    session = engine.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"tree": session.get_timeline_tree(), "current_node_id": session.current_node_id}

@app.get("/api/story/{session_id}/characters")
def get_characters(session_id: str):
    """Get characters (secrets redacted)."""
    characters = engine.get_characters(session_id)
    if characters is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"characters": characters}

@app.delete("/api/story/{session_id}")
def delete_story(session_id: str):
    """Clean up a story session."""
    engine.cleanup_session(session_id)
    return {"status": "deleted"}

# Serve frontend
@app.get("/")
def serve_frontend():
    frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path)
    return {"message": "The Director API is running. Frontend not found."}

@app.get("/app.js")
def serve_js():
    js_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "app.js")
    if os.path.exists(js_path):
        return FileResponse(js_path, media_type="application/javascript")
    raise HTTPException(status_code=404)

@app.get("/styles.css")
def serve_css():
    css_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "styles.css")
    if os.path.exists(css_path):
        return FileResponse(css_path, media_type="text/css")
    raise HTTPException(status_code=404)
