"""Data models for The Director."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass
class CharacterEntry:
    id: str
    name: str
    visual_description: str  # permanent, used in FLUX prompts
    personality: str
    goals: str
    secrets: str  # hidden from player
    relationship: str
    voice_style: str
    agent_id: str | None = None
    conversation_id: str | None = None


@dataclass
class WorldState:
    location: str = "Unknown"
    time_of_day: str = "Night"
    day_number: int = 1
    tension: int = 3
    weather: str = "Clear"
    active_threads: list[str] = field(default_factory=list)
    resolved_threads: list[str] = field(default_factory=list)
    key_events: list[str] = field(default_factory=list)


@dataclass
class TimelineNode:
    id: str
    scene_number: int
    narration: str
    image_url: str | None
    choices_presented: list[dict]
    choice_made: int | None = None
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    conversation_id: str | None = None
    dialogue: list[dict] = field(default_factory=list)
    art_direction: dict = field(default_factory=dict)
    world_state: dict = field(default_factory=dict)
    from_choice_text: str | None = None


@dataclass
class StorySession:
    id: str = field(default_factory=lambda: str(uuid4()))
    genre: str = "film_noir"
    narrator_agent_id: str | None = None
    image_agent_id: str | None = None
    conversation_id: str | None = None
    character_bible: dict[str, CharacterEntry] = field(default_factory=dict)
    character_agents: dict[str, str] = field(default_factory=dict)  # char_id -> agent_id
    world_state: WorldState = field(default_factory=WorldState)
    timeline_nodes: dict[str, TimelineNode] = field(default_factory=dict)
    current_node_id: str | None = None
    root_node_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def add_node(self, node: TimelineNode) -> None:
        self.timeline_nodes[node.id] = node
        if node.parent_id and node.parent_id in self.timeline_nodes:
            parent = self.timeline_nodes[node.parent_id]
            if node.id not in parent.children:
                parent.children.append(node.id)
        self.current_node_id = node.id
        if self.root_node_id is None:
            self.root_node_id = node.id

    def get_timeline_tree(self) -> list[dict]:
        """Return the timeline as a list of node dicts for the frontend."""
        result = []
        for nid, node in self.timeline_nodes.items():
            result.append({
                "id": node.id,
                "scene_number": node.scene_number,
                "narration_preview": node.narration[:100] + "..." if len(node.narration) > 100 else node.narration,
                "image_url": node.image_url,
                "choices_presented": node.choices_presented,
                "choice_made": node.choice_made,
                "parent_id": node.parent_id,
                "children": node.children,
                "from_choice_text": node.from_choice_text,
            })
        return result
