"""Game Engine — orchestrates agents, conversations, turns, and image generation."""

from __future__ import annotations

import json
import logging
import os
import base64
import time
from uuid import uuid4

import requests as http_requests  # named to avoid clash with mistralai internals
from mistralai import Mistral, CompletionArgs, ResponseFormat
from mistralai.models import ToolFileChunk

from .models import StorySession, CharacterEntry, WorldState, TimelineNode
from .prompts import (
    NARRATOR_SYSTEM_PROMPT,
    CHARACTER_SYSTEM_PROMPT_TEMPLATE,
    GENRE_TEMPLATES,
)
from .art_director import compose_flux_prompt

logger = logging.getLogger(__name__)


class GameEngine:
    def __init__(self):
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise ValueError("MISTRAL_API_KEY not set")
        self.client = Mistral(api_key=api_key)
        self.sessions: dict[str, StorySession] = {}
        # Shared image generation agent (reused across sessions)
        self._image_agent_id: str | None = None

    def _get_image_agent(self) -> str:
        """Get or create the shared image generation agent."""
        if self._image_agent_id is None:
            agent = self.client.beta.agents.create(
                model="mistral-medium-latest",
                name="Art Director",
                description="Generates scene illustrations for interactive stories.",
                instructions="Generate an image exactly matching the description provided. Do not add commentary, just generate the image.",
                tools=[{"type": "image_generation"}],
            )
            self._image_agent_id = agent.id
        return self._image_agent_id

    def create_story(self, genre: str, custom_premise: str | None = None) -> dict:
        """Create a new story session. Returns the opening scene."""
        session = StorySession(genre=genre)

        # Get genre template
        genre_template = GENRE_TEMPLATES.get(genre, GENRE_TEMPLATES["film_noir"])
        premise = custom_premise or genre_template["premise"]

        # Create narrator agent with structured JSON output
        narrator = self.client.beta.agents.create(
            model="mistral-medium-latest",
            name=f"Narrator-{session.id[:8]}",
            description="The Director — Game Master of an interactive visual story",
            instructions=NARRATOR_SYSTEM_PROMPT,
            completion_args=CompletionArgs(
                response_format=ResponseFormat(type="json_object"),
            ),
        )
        session.narrator_agent_id = narrator.id

        # Start conversation with genre premise
        response = self.client.beta.conversations.start(
            agent_id=narrator.id,
            inputs=premise,
        )
        session.conversation_id = response.conversation_id

        # Parse narrator's structured response
        scene_data = self._parse_narrator_response(response)
        if scene_data is None:
            raise RuntimeError("Failed to parse narrator opening scene")

        # Create character agents for any new characters
        self._process_new_characters(session, scene_data)

        # Generate scene image
        image_url = self._generate_scene_image(session, scene_data)
        logger.info(f"[CREATE] Scene image result: {image_url}")

        # Create timeline node
        node = TimelineNode(
            id=str(uuid4()),
            scene_number=1,
            narration=scene_data.get("narration", ""),
            image_url=image_url,
            choices_presented=scene_data.get("choices", []),
            dialogue=scene_data.get("dialogue", []),
            art_direction=scene_data.get("art_direction", {}),
            world_state=scene_data.get("world_state", {}),
            conversation_id=session.conversation_id,
        )
        session.add_node(node)

        # Update world state
        ws = scene_data.get("world_state", {})
        if ws:
            session.world_state.location = ws.get("location", session.world_state.location)
            session.world_state.time_of_day = ws.get("time", session.world_state.time_of_day)
            session.world_state.tension = ws.get("tension", session.world_state.tension)
            session.world_state.active_threads = ws.get("active_plot_threads", [])
            session.world_state.key_events.extend(ws.get("recent_events", []))

        self.sessions[session.id] = session

        return self._build_scene_response(session, node, scene_data)

    def make_choice(self, session_id: str, choice_id: int = None, custom_text: str = None) -> dict:
        """Process a player's choice and return the next scene."""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        # Mark choice on current node
        current_node = session.timeline_nodes[session.current_node_id]

        if custom_text:
            choice_text = custom_text
            current_node.choice_made = 0
            current_node.choices_presented.append({"id": 0, "text": custom_text, "tone": "custom"})
        else:
            current_node.choice_made = choice_id
            choice_text = "Continue"
            for c in current_node.choices_presented:
                if c.get("id") == choice_id:
                    choice_text = c.get("text", "Continue")
                    break

        # Send choice to narrator
        response = self.client.beta.conversations.append(
            conversation_id=session.conversation_id,
            inputs=f'The player chose: "{choice_text}"',
        )
        session.conversation_id = response.conversation_id

        # Parse response
        scene_data = self._parse_narrator_response(response)
        if scene_data is None:
            raise RuntimeError("Failed to parse narrator response")

        # Handle new characters
        self._process_new_characters(session, scene_data)

        # Handle character handoff if requested
        if scene_data.get("handoff_to"):
            char_id = scene_data["handoff_to"]
            if char_id in session.character_agents:
                char_dialogue = self._handle_character_handoff(session, char_id, scene_data)
                if char_dialogue:
                    scene_data.setdefault("dialogue", []).append({
                        "speaker": char_id,
                        "text": char_dialogue,
                        "delivery": "in character",
                    })

        # Generate image
        image_url = self._generate_scene_image(session, scene_data)
        logger.info(f"[CHOICE] Scene image result: {image_url}")

        # Create new timeline node
        scene_num = len(session.timeline_nodes) + 1
        node = TimelineNode(
            id=str(uuid4()),
            scene_number=scene_num,
            narration=scene_data.get("narration", ""),
            image_url=image_url,
            choices_presented=scene_data.get("choices", []),
            parent_id=current_node.id,
            dialogue=scene_data.get("dialogue", []),
            art_direction=scene_data.get("art_direction", {}),
            world_state=scene_data.get("world_state", {}),
            conversation_id=session.conversation_id,
            from_choice_text=choice_text,
        )
        session.add_node(node)

        # Update world state
        ws = scene_data.get("world_state", {})
        if ws:
            session.world_state.location = ws.get("location", session.world_state.location)
            session.world_state.time_of_day = ws.get("time", session.world_state.time_of_day)
            session.world_state.tension = ws.get("tension", session.world_state.tension)
            session.world_state.active_threads = ws.get("active_plot_threads", [])
            session.world_state.key_events.extend(ws.get("recent_events", []))

        return self._build_scene_response(session, node, scene_data)

    def fork_timeline(self, session_id: str, node_id: str, alt_choice_id: int = None, custom_text: str = None) -> dict:
        """Fork the timeline from a past node with an alternate choice."""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        target_node = session.timeline_nodes.get(node_id)
        if not target_node:
            raise ValueError(f"Node {node_id} not found")

        # Find alternate choice text
        if custom_text:
            choice_text = custom_text
        else:
            choice_text = "Continue"
            for c in target_node.choices_presented:
                if c.get("id") == alt_choice_id:
                    choice_text = c.get("text", "Continue")
                    break

        # Replay conversation: collect narrations up to target node
        history = []
        current = target_node
        path = []
        while current:
            path.append(current)
            if current.parent_id:
                current = session.timeline_nodes.get(current.parent_id)
            else:
                break
        path.reverse()

        # Start a fresh conversation with the narrator to simulate the fork
        genre_template = GENRE_TEMPLATES.get(session.genre, GENRE_TEMPLATES["film_noir"])
        response = self.client.beta.conversations.start(
            agent_id=session.narrator_agent_id,
            inputs=genre_template["premise"],
        )
        fork_conv_id = response.conversation_id

        # Replay turns up to fork point (skip first since that was the premise)
        for node in path[1:]:
            if node.choice_made is not None:
                chosen_text = "Continue"
                for c in node.choices_presented:
                    if c.get("id") == node.choice_made:
                        chosen_text = c.get("text", "Continue")
                        break
                # We need to find the parent's chosen choice text instead
                pass  # simplified: skip replay for hackathon, just send alt choice

        # Send the alternate choice
        fork_prompt = f'The player chose: "{choice_text}"'
        if custom_text:
            fork_prompt += ' (This is a custom "what if" scenario. The player is exploring an entirely different path.)'
        else:
            fork_prompt += ' (This is an alternate timeline. The player is exploring what would have happened with a different choice.)'
        response = self.client.beta.conversations.append(
            conversation_id=fork_conv_id,
            inputs=fork_prompt,
        )
        fork_conv_id = response.conversation_id

        scene_data = self._parse_narrator_response(response)
        if scene_data is None:
            raise RuntimeError("Failed to parse forked narrator response")

        # Generate image
        image_url = self._generate_scene_image(session, scene_data)
        logger.info(f"[FORK] Scene image result: {image_url}")

        # Create forked timeline node
        node = TimelineNode(
            id=str(uuid4()),
            scene_number=target_node.scene_number + 1,
            narration=scene_data.get("narration", ""),
            image_url=image_url,
            choices_presented=scene_data.get("choices", []),
            parent_id=target_node.id,
            dialogue=scene_data.get("dialogue", []),
            art_direction=scene_data.get("art_direction", {}),
            world_state=scene_data.get("world_state", {}),
            conversation_id=fork_conv_id,
            from_choice_text=choice_text,
        )
        session.add_node(node)

        return self._build_scene_response(session, node, scene_data)

    def get_session(self, session_id: str) -> StorySession | None:
        return self.sessions.get(session_id)

    def get_characters(self, session_id: str) -> list[dict]:
        """Get characters (secrets redacted)."""
        session = self.sessions.get(session_id)
        if not session:
            return []
        result = []
        for cid, char in session.character_bible.items():
            result.append({
                "id": char.id,
                "name": char.name,
                "visual_description": char.visual_description,
                "personality": char.personality,
                "relationship": char.relationship,
                "voice_style": char.voice_style,
            })
        return result

    # --- Private helpers ---

    def _parse_narrator_response(self, response) -> dict | None:
        """Extract JSON from narrator response."""
        for entry in response.outputs:
            if not hasattr(entry, "content"):
                continue
            content = entry.content
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = ""
                for chunk in content:
                    if hasattr(chunk, "text"):
                        text += chunk.text
            else:
                continue

            if not text.strip():
                continue

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Try to extract JSON from text
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    try:
                        return json.loads(text[start:end])
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse JSON: {text[:200]}")
        return None

    def _process_new_characters(self, session: StorySession, scene_data: dict) -> None:
        """Create agents for newly introduced characters."""
        for new_char in scene_data.get("new_characters", []):
            char_id = new_char.get("id", str(uuid4())[:8])
            if char_id in session.character_bible:
                continue

            # Create character entry
            entry = CharacterEntry(
                id=char_id,
                name=new_char.get("name", "Unknown"),
                visual_description=new_char.get("visual_description", ""),
                personality=new_char.get("personality_summary", ""),
                goals=new_char.get("goals", ""),
                secrets=new_char.get("secrets", ""),
                relationship=new_char.get("relationship_to_player", ""),
                voice_style=new_char.get("voice_style", "neutral"),
            )

            # Create Mistral agent for this character
            char_prompt = CHARACTER_SYSTEM_PROMPT_TEMPLATE.format(
                name=entry.name,
                personality=entry.personality,
                goals=entry.goals,
                secrets=entry.secrets,
                voice_style=entry.voice_style,
                relationship=entry.relationship,
            )

            agent = self.client.beta.agents.create(
                model="mistral-medium-latest",
                name=f"Character-{entry.name}-{session.id[:8]}",
                description=f"Character agent for {entry.name}",
                instructions=char_prompt,
            )
            entry.agent_id = agent.id
            session.character_bible[char_id] = entry
            session.character_agents[char_id] = agent.id
            logger.info(f"Created character agent: {entry.name} ({agent.id})")

    def _handle_character_handoff(
        self, session: StorySession, char_id: str, scene_data: dict
    ) -> str | None:
        """Handle a character handoff — get character's dialogue response."""
        char = session.character_bible.get(char_id)
        if not char or not char.agent_id:
            return None

        context = (
            f"[Scene context: {scene_data.get('narration', '')[:300]}]\n"
            f"[The narrator has asked you to speak. Respond in character as {char.name}.]"
        )

        try:
            if char.conversation_id:
                response = self.client.beta.conversations.append(
                    conversation_id=char.conversation_id,
                    inputs=context,
                )
            else:
                response = self.client.beta.conversations.start(
                    agent_id=char.agent_id,
                    inputs=context,
                )
            char.conversation_id = response.conversation_id

            # Extract text from response
            for entry in response.outputs:
                if hasattr(entry, "content"):
                    if isinstance(entry.content, str):
                        return entry.content
                    elif isinstance(entry.content, list):
                        texts = [c.text for c in entry.content if hasattr(c, "text")]
                        return " ".join(texts)
        except Exception as e:
            logger.error(f"Character handoff failed for {char_id}: {e}")
        return None

    def _generate_scene_image(self, session: StorySession, scene_data: dict) -> str | None:
        """Generate a scene image using FLUX. Tries Mistral first, falls back to Replicate."""
        art_direction = scene_data.get("art_direction", {})
        logger.info(f"[IMG] _generate_scene_image called. art_direction keys: {list(art_direction.keys()) if art_direction else 'EMPTY'}")
        if not art_direction:
            logger.warning("[IMG] No art_direction in scene_data, skipping image generation")
            return None

        flux_prompt = compose_flux_prompt(
            art_direction=art_direction,
            character_bible=session.character_bible,
            genre=session.genre,
        )
        logger.info(f"[IMG] FLUX prompt ({len(flux_prompt)} chars): {flux_prompt[:300]}...")

        # Try Mistral image agent
        result = self._try_mistral_image(flux_prompt)
        if result:
            return result

        # Fallback: HuggingFace free inference (set HF_TOKEN in .env)
        logger.info("[IMG] Mistral failed, trying HuggingFace fallback")
        result = self._try_huggingface_image(flux_prompt)
        if result:
            return result

        # Last resort: Pollinations.ai (free, no key, may be Cloudflare-blocked)
        logger.info("[IMG] HuggingFace failed, trying Pollinations.ai")
        return self._try_pollinations_image(flux_prompt)

    def _try_mistral_image(self, flux_prompt: str) -> str | None:
        """Try generating image via Mistral's image_generation tool."""
        try:
            image_agent_id = self._get_image_agent()
            logger.info(f"[IMG-Mistral] Using image agent: {image_agent_id}")
            response = self.client.beta.conversations.start(
                agent_id=image_agent_id,
                inputs=flux_prompt,
            )
            logger.info(f"[IMG-Mistral] Response received, {len(response.outputs)} outputs")

            for entry in response.outputs:
                if not hasattr(entry, "content") or not isinstance(entry.content, list):
                    continue
                for chunk in entry.content:
                    if isinstance(chunk, ToolFileChunk):
                        logger.info(f"[IMG-Mistral] Found ToolFileChunk, file_id={chunk.file_id}")
                        file_bytes = self.client.files.download(file_id=chunk.file_id).read()
                        return self._save_image_bytes(file_bytes)

            logger.warning("[IMG-Mistral] No ToolFileChunk found in response")
        except Exception as e:
            logger.error(f"[IMG-Mistral] Failed: {e}")
        return None

    def _try_huggingface_image(self, flux_prompt: str) -> str | None:
        """Generate image via HuggingFace Inference API (free with HF_TOKEN)."""
        hf_token = os.environ.get("HF_TOKEN")
        if not hf_token:
            logger.info("[IMG-HF] No HF_TOKEN set, skipping")
            return None
        try:
            url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
            headers = {"Authorization": f"Bearer {hf_token}"}
            resp = http_requests.post(url, json={"inputs": flux_prompt[:500]}, headers=headers, timeout=120)
            if resp.status_code == 200 and "image" in (resp.headers.get("Content-Type", "")):
                logger.info(f"[IMG-HF] Got {len(resp.content)} bytes")
                return self._save_image_bytes(resp.content)
            logger.warning(f"[IMG-HF] Bad response: status={resp.status_code}, body={resp.text[:200]}")
        except Exception as e:
            logger.error(f"[IMG-HF] Failed: {e}")
        return None

    def _try_pollinations_image(self, flux_prompt: str) -> str | None:
        """Generate image via Pollinations.ai (free, no API key needed)."""
        try:
            from urllib.parse import quote
            encoded = quote(flux_prompt[:500])  # Pollinations has URL length limits
            url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=576&nologo=true"
            logger.info(f"[IMG-Pollinations] Requesting: {url[:120]}...")
            resp = http_requests.get(url, timeout=90, headers={"User-Agent": "TheDirector/1.0"})
            if resp.status_code == 200 and len(resp.content) > 1000:
                logger.info(f"[IMG-Pollinations] Got {len(resp.content)} bytes")
                return self._save_image_bytes(resp.content)
            logger.warning(f"[IMG-Pollinations] Bad response: status={resp.status_code}, size={len(resp.content)}")
        except Exception as e:
            logger.error(f"[IMG-Pollinations] Failed: {e}")
        return None

    def _save_image_bytes(self, file_bytes: bytes) -> str:
        """Save image bytes to static dir and return the URL path."""
        image_id = str(uuid4())[:12]
        filename = f"{image_id}.png"
        filepath = os.path.join(os.path.dirname(__file__), "static", filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(file_bytes)
        logger.info(f"[IMG] Saved {len(file_bytes)} bytes to /static/{filename}")
        return f"/static/{filename}"

    def _build_scene_response(self, session: StorySession, node: TimelineNode, scene_data: dict) -> dict:
        """Build the response to send to the frontend."""
        return {
            "session_id": session.id,
            "scene": {
                "node_id": node.id,
                "scene_number": node.scene_number,
                "narration": node.narration,
                "dialogue": node.dialogue,
                "choices": node.choices_presented,
                "image_url": node.image_url,
                "art_direction": node.art_direction,
                "world_state": node.world_state,
            },
            "characters": [
                {"id": c.id, "name": c.name}
                for c in session.character_bible.values()
            ],
            "genre": session.genre,
            "timeline": session.get_timeline_tree(),
        }

    def cleanup_session(self, session_id: str) -> None:
        """Clean up agents for a session."""
        session = self.sessions.get(session_id)
        if not session:
            return
        try:
            if session.narrator_agent_id:
                self.client.beta.agents.delete(agent_id=session.narrator_agent_id)
            for agent_id in session.character_agents.values():
                self.client.beta.agents.delete(agent_id=agent_id)
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        del self.sessions[session_id]
