"""Script generation.

Two engines, one interface:

* :func:`generate_script_llm` - uses any OpenAI-compatible chat endpoint when
  ``OPENAI_API_KEY`` is configured. This is the high quality path.
* :func:`generate_script_offline` - a deterministic, dependency-free writer that
  always works. It is what makes the app runnable with zero configuration and
  what keeps the test suite hermetic.

Both return a :class:`Script` whose ``scenes`` are already split into
narration + on-screen caption pairs, ready for the voice and render stages.
"""
from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from .config import settings

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class ScriptScene:
    narration: str
    caption: str


@dataclass
class Script:
    title: str
    hook: str
    scenes: List[ScriptScene] = field(default_factory=list)
    cta: str = ""
    engine: str = "offline"

    def to_text(self) -> str:
        """Human readable script, in the shape the original UI displayed."""
        lines = [f"TITLE: {self.title}", "", f"HOOK: {self.hook}", ""]
        for i, scene in enumerate(self.scenes, start=1):
            lines.append(f"SCENE {i}: {scene.narration}")
            lines.append(f"ON-SCREEN TEXT: {scene.caption}")
            lines.append("")
        if self.cta:
            lines.append(f"CTA: {self.cta}")
        return "\n".join(lines).strip()

    def full_narration(self) -> str:
        parts = [self.hook] + [s.narration for s in self.scenes]
        if self.cta:
            parts.append(self.cta)
        return " ".join(p.strip() for p in parts if p and p.strip())


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
LENGTH_TO_SCENES = {
    "30 seconds": 4,
    "60 seconds": 7,
    "90 seconds": 10,
    "3 minutes": 16,
}


def scene_count_for(length: str) -> int:
    return LENGTH_TO_SCENES.get(length.strip().lower(), 6)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip('"\'` ')


def _caption_from(narration: str, limit: int = 62) -> str:
    """Derive a punchy on-screen caption from a narration line."""
    text = _clean(narration)
    text = re.sub(r"^(and|but|so|then|because|which|that)\s+", "", text, flags=re.I)
    # Prefer the first clause - it is usually the punchiest.
    for sep in (". ", "! ", "? ", "; ", ", "):
        if sep in text:
            head = text.split(sep)[0].strip()
            if 12 <= len(head) <= limit:
                text = head
                break
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = cut or text[:limit]
    return text.upper().rstrip(".,;:")


# --------------------------------------------------------------------------- #
# Offline engine
# --------------------------------------------------------------------------- #
_HOOKS = [
    "Nobody talks about this, but {topic} changes everything.",
    "If you learn one thing about {topic}, make it this.",
    "Most people get {topic} completely wrong.",
    "This is the fastest way to understand {topic}.",
    "Stop scrolling. This is what {topic} really looks like.",
    "In one minute you will see {topic} differently.",
]

# Each beat is roughly 15-20 spoken words, which lands close to the target
# length once the hook and the call to action are added.
_BEATS = [
    ("Start with the uncomfortable truth",
     "The uncomfortable truth about {topic} is that almost nobody starts."),
    ("Understand the real cost",
     "Every day you delay {topic}, the gap gets wider and harder to close."),
    ("Shrink the first step",
     "Make the first step with {topic} so small that skipping it feels ridiculous."),
    ("Build a repeatable system",
     "Motivation fades, so build a system around {topic} that runs without it."),
    ("Protect your attention",
     "Your attention is the raw material of {topic}, and everything is fighting for it."),
    ("Measure what actually moves",
     "Track one number that proves {topic} is working, and ignore the rest."),
    ("Expect the boring middle",
     "There is a long, boring middle in {topic} where most people quietly quit."),
    ("Use the people around you",
     "You do not have to figure out {topic} alone; find someone who already did."),
    ("Make it visible",
     "When {topic} is visible, it becomes real, and quitting starts to cost you."),
    ("Review and adjust weekly",
     "Once a week, look honestly at {topic}, keep what worked, cut the rest."),
    ("Play the long game",
     "{topic} rewards patience more than intensity, because the compounding is invisible."),
    ("Decide before you feel ready",
     "You will never feel ready for {topic}; decide first, then find the confidence."),
    ("Stop waiting for permission",
     "Nobody is coming to hand you {topic}; the permission has to come from you."),
    ("Trade comfort for progress",
     "Every real step in {topic} costs you some comfort, and that is the price."),
    ("Keep the promise small",
     "Promise yourself ten minutes a day on {topic}, and keep that promise."),
    ("Let the data decide",
     "Stop arguing with yourself about {topic} and let the results settle it."),
]

_CTAS = [
    "Follow for more on {topic}, and save this for later.",
    "If this helped, follow and share it with someone who needs it.",
    "Save this video, then go do the first small step today.",
    "Follow for part two, where we go deeper into {topic}.",
]


def generate_script_offline(
    topic: str,
    niche: str = "Motivation",
    length: str = "60 seconds",
    style: str = "Cinematic",
    seed: Optional[int] = None,
) -> Script:
    """Deterministic, dependency-free scriptwriter.

    Produces a hook, N narration beats and a call to action. The same inputs
    always produce the same script unless ``seed`` is supplied.
    """
    rng = random.Random(seed if seed is not None else hash((topic, niche, length, style)) & 0xFFFFFFFF)
    topic_clean = _clean(topic).rstrip(".")
    topic_lower = topic_clean[0].lower() + topic_clean[1:] if topic_clean else topic_clean

    n = scene_count_for(length)
    hook = rng.choice(_HOOKS).format(topic=topic_lower)

    beats = list(_BEATS)
    rng.shuffle(beats)
    chosen = beats[:n]

    scenes: List[ScriptScene] = []
    for heading, template in chosen:
        narration = template.format(topic=topic_lower)
        scenes.append(ScriptScene(narration=narration, caption=heading.upper()))

    cta = rng.choice(_CTAS).format(topic=topic_lower)
    title = topic_clean[:70]

    return Script(title=title, hook=hook, scenes=scenes, cta=cta, engine="offline")


# --------------------------------------------------------------------------- #
# LLM engine
# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT = (
    "You are a short-form video scriptwriter for faceless vertical videos "
    "(TikTok, YouTube Shorts, Instagram Reels). You write tight, spoken-word "
    "narration with no stage directions, no emojis and no markdown. "
    "You always answer with a single JSON object and nothing else."
)

_USER_TEMPLATE = """Write a {n}-scene faceless video script.

Topic: {topic}
Niche: {niche}
Target length: {length}
Visual style: {style}

Rules:
- "hook" is one spoken sentence that stops the scroll.
- Each scene's "narration" is 1-2 spoken sentences (roughly 12-28 words).
- Each scene's "caption" is a punchy on-screen text of at most 6 words, in CAPS.
- "cta" is one spoken closing sentence.
- Plain spoken English only. No emojis, no markdown, no scene directions.

Return JSON exactly in this shape:
{{"title": "...", "hook": "...", "scenes": [{{"narration": "...", "caption": "..."}}], "cta": "..."}}"""


def _extract_json(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(raw[start : end + 1])


def generate_script_llm(
    topic: str,
    niche: str = "Motivation",
    length: str = "60 seconds",
    style: str = "Cinematic",
) -> Script:
    """Generate a script through an OpenAI-compatible chat completions API."""
    n = scene_count_for(length)
    payload = {
        "model": settings.openai_model,
        "temperature": 0.8,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_TEMPLATE.format(
                    n=n, topic=topic, niche=niche, length=length, style=style
                ),
            },
        ],
    }
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=settings.llm_timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"]
    parsed = _extract_json(content)

    scenes = []
    for item in parsed.get("scenes", [])[:n]:
        narration = _clean(item.get("narration", ""))
        if not narration:
            continue
        caption = _clean(item.get("caption", "")) or _caption_from(narration)
        scenes.append(ScriptScene(narration=narration, caption=caption.upper()[:70]))

    if not scenes:
        raise ValueError("model returned no usable scenes")

    return Script(
        title=_clean(parsed.get("title", topic)) or topic,
        hook=_clean(parsed.get("hook", "")) or scenes[0].narration,
        scenes=scenes,
        cta=_clean(parsed.get("cta", "")),
        engine=f"llm:{settings.openai_model}",
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def generate_script(
    topic: str,
    niche: str = "Motivation",
    length: str = "60 seconds",
    style: str = "Cinematic",
) -> Script:
    """Generate a script, preferring the LLM and falling back to offline."""
    if settings.llm_enabled:
        try:
            return generate_script_llm(topic, niche, length, style)
        except Exception as exc:  # noqa: BLE001 - any failure must degrade gracefully
            log.warning("LLM script generation failed (%s); using offline writer", exc)
    return generate_script_offline(topic, niche, length, style)


def script_from_text(text: str, length: str = "60 seconds") -> Script:
    """Build a :class:`Script` from a script the user pasted in."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("script text is empty")

    # Strip the labels the UI/LLM may have left behind.
    cleaned = re.sub(
        r"^\s*(TITLE|HOOK|SCENE\s*\d+|ON[- ]?SCREEN TEXT|VISUALS?|VOICEOVER|CTA)\s*:\s*",
        "",
        raw,
        flags=re.I | re.M,
    )
    cleaned = re.sub(r"\[[^\]]*\]", "", cleaned)

    chunks = [c.strip() for c in re.split(r"\n+", cleaned) if len(c.strip()) > 15]
    if len(chunks) < 2:
        chunks = [c.strip() for c in re.split(r"(?<=[.!?])\s+", cleaned) if len(c.strip()) > 15]
    if not chunks:
        chunks = [cleaned]

    n = scene_count_for(length)
    if len(chunks) > n:
        # Merge the tail so nothing is lost.
        head, tail = chunks[: n - 1], chunks[n - 1 :]
        chunks = head + [" ".join(tail)]

    scenes = [ScriptScene(narration=c, caption=_caption_from(c)) for c in chunks]
    return Script(
        title=chunks[0][:70],
        hook=chunks[0],
        scenes=scenes,
        cta="",
        engine="user-supplied",
    )
