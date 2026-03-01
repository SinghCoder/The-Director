# The Director

**The Director** is a multi-agent, interactive storytelling engine built with the **Mistral Agents API**.  
Players can start a story in multiple genres, make meaningful choices, branch timelines with “what-if” actions, and see each scene generated as an illustrated visual panel.

Built for the **Mistral Worldwide Hackathon** (Feb 28 – Mar 1, 2026).

## What this project does

The app combines three ideas into one experience:

1. **Dynamic narrative agent** that generates scene narration, dialogue, choices, and world state in structured JSON.
2. **Character agents** created on the fly for role-played NPCs (each with personality, goals, and secrets).
3. **Visual scene generation** using FLUX-style prompts with fallback providers.

The frontend shows the story as a branching canvas so users can:

- Follow the current timeline
- Fork from past scenes into alternate branches
- Explore custom actions with "what-if" prompts
- Inspect world state and scene metadata per node

## Features

- Genre presets: `film_noir`, `high_fantasy`, `sci_fi`, `horror`
- Multi-agent storytelling with persistent scene context
- Multi-turn branching timeline with node graph visualization
- Optional custom premise input on story start
- Per-scene image generation with fallback:
  - Mistral image-generation agent
  - Hugging Face (if `HF_TOKEN` is provided)
  - Pollinations.ai as last-resort fallback
- FastAPI backend that serves the frontend directly
- In-memory story sessions (single-process, no database required)

## Repository structure

- `backend/server.py`: FastAPI app, API routes, static file serving
- `backend/engine.py`: Story orchestration, agent interaction, branching logic
- `backend/art_director.py`: Builds image prompts from scene + character lore
- `backend/prompts.py`: Narrator and character system prompts + genre templates/styles
- `backend/models.py`: Session, timeline, and world-state dataclasses
- `frontend/index.html`: UI shell
- `frontend/app.js`: Client app logic and interactive canvas rendering
- `frontend/styles.css`: Visual presentation/theme styles
- `backend/static/`: Generated scene image files

## Tech stack

- **Backend**: Python, FastAPI, Uvicorn, Mistral Agents SDK, Requests
- **Frontend**: Vanilla JavaScript, HTML, CSS
- **AI APIs**:
  - Mistral Agents API (narration, character agents, image generation)
  - Hugging Face Inference API (optional image fallback)

## Setup

### 1) Create environment

```bash
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
```

### 2) Install dependencies

```bash
pip install fastapi uvicorn mistralai python-dotenv requests
```

### 3) Configure environment variables

Create a `.env` file in the repository root:

```ini
MISTRAL_API_KEY=<your-mistral-api-key>
HF_TOKEN=<your-huggingface-token>   # optional, for image fallback
```

`REPLICATE_API_TOKEN` is not required by the current backend flow and can be ignored.

### 4) Run the app

```bash
uvicorn backend.server:app --reload --host 0.0.0.0 --port 8000
```

Open the app at [http://127.0.0.1:8000](http://127.0.0.1:8000).
