"""Art Director — composes FLUX 1.1 prompts from narrator output + character bible."""

from .models import CharacterEntry
from .prompts import GENRE_STYLES


def compose_flux_prompt(
    art_direction: dict,
    character_bible: dict[str, CharacterEntry],
    genre: str,
) -> str:
    """Construct an optimized FLUX 1.1 prompt.

    Strategy:
    1. Start with genre style prefix
    2. Add scene description
    3. Insert visual descriptions for characters visible in scene
    4. Add composition/camera/lighting
    5. Add mood + key visual elements
    6. End with quality keywords
    """
    style = GENRE_STYLES.get(genre, GENRE_STYLES["film_noir"])
    parts = []

    # 1. Genre style
    parts.append(style["prefix"])

    # 2. Scene description
    scene_desc = art_direction.get("scene_description", "")
    if scene_desc:
        parts.append(scene_desc)

    # 3. Character descriptions from bible (exact, never paraphrased)
    for char_id in art_direction.get("characters_visible", []):
        if char_id in character_bible:
            char = character_bible[char_id]
            parts.append(char.visual_description)

    # 4. Composition
    comp = art_direction.get("composition", "")
    if comp:
        parts.append(comp)

    # 5. Lighting
    lighting = art_direction.get("lighting", style["default_lighting"])
    parts.append(lighting)

    # 6. Mood
    mood = art_direction.get("mood", "")
    if mood:
        parts.append(f"{mood} atmosphere")

    # 7. Key visual elements
    for element in art_direction.get("key_visual_elements", []):
        parts.append(element)

    # 8. Quality
    parts.append(style["quality"])

    return ". ".join(parts)
