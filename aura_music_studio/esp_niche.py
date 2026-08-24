from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException, Request

from .esp_command_center import EspStore, esp

NetworkStatus = Literal["esp_only", "other_network", "unsure"]


NICHE_CATALOG: dict[str, dict] = {
    "music": {
        "title": "Music & Performing Arts",
        "icon": "🎵",
        "theme": {"accent": "#f4c873", "secondary": "#a66bff", "glow": "#f4c87355"},
        "training": [
            "LIVE show structure, set planning and recurring audience hooks",
            "Music discovery content, covers, originals and release storytelling",
            "Audience requests, safe gifting mechanics and community participation",
            "Short-form performance clips, teasers and song-launch campaigns",
            "Retention, watch-time and fan-community development",
        ],
    },
    "gaming": {
        "title": "Gaming",
        "icon": "🎮",
        "theme": {"accent": "#6fe8ff", "secondary": "#846dff", "glow": "#6fe8ff55"},
        "training": [
            "Game-focused LIVE structure, challenge loops and commentary",
            "Clip-worthy moments and short-form highlight strategy",
            "Community games, goals and audience participation",
            "Retention through progression, stakes and recurring segments",
            "Gaming collaborations, safe competition and schedule consistency",
        ],
    },
    "beauty": {
        "title": "Beauty & Cosmetics",
        "icon": "💄",
        "theme": {"accent": "#ff8fd1", "secondary": "#c486ff", "glow": "#ff8fd155"},
        "training": [
            "Tutorial-led LIVE structure and transformation storytelling",
            "Product demonstration without losing community conversation",
            "Before/after, routines, reviews and educational short-form content",
            "Visual setup, lighting and close-up presentation",
            "Trust-building, disclosure and sustainable audience growth",
        ],
    },
    "fashion": {
        "title": "Fashion & Style",
        "icon": "👗",
        "theme": {"accent": "#ffc1e8", "secondary": "#f0b76c", "glow": "#ffc1e855"},
        "training": [
            "Outfit, styling and transformation-led LIVE formats",
            "Lookbook and transition-video content systems",
            "Audience voting and interactive styling decisions",
            "Seasonal themes, launches and repeatable content pillars",
            "Brand-safe recommendations and authentic style positioning",
        ],
    },
    "fitness": {
        "title": "Fitness & Movement",
        "icon": "🏋️",
        "theme": {"accent": "#78e0a8", "secondary": "#5fc9ff", "glow": "#78e0a855"},
        "training": [
            "Workout LIVE structure, pacing and audience participation",
            "Progress, challenge and consistency storytelling",
            "Short-form movement demonstrations and educational hooks",
            "Community accountability without unsafe health claims",
            "Schedule design, repeat viewers and motivational formats",
        ],
    },
    "food": {
        "title": "Food, Cooking & Baking",
        "icon": "🍳",
        "theme": {"accent": "#ffb96c", "secondary": "#f36f76", "glow": "#ffb96c55"},
        "training": [
            "Recipe-led LIVE structure from setup to reveal",
            "Camera framing, preparation flow and audience interaction",
            "Recipe shorts, food reveals and repeatable series",
            "Community choices, challenges and cultural storytelling",
            "Retention through stages, timers and finished-result payoff",
        ],
    },
    "travel": {
        "title": "Travel & Places",
        "icon": "✈️",
        "theme": {"accent": "#62e4d8", "secondary": "#4aa6ff", "glow": "#62e4d855"},
        "training": [
            "Location-led storytelling and LIVE walk-through structure",
            "Destination guides, discoveries and itinerary content",
            "Safety, privacy and location-awareness while broadcasting",
            "Short-form travel hooks and visual storytelling",
            "Community recommendations and recurring destination series",
        ],
    },
    "education": {
        "title": "Education & Skills",
        "icon": "🎓",
        "theme": {"accent": "#7cc8ff", "secondary": "#b087ff", "glow": "#7cc8ff55"},
        "training": [
            "Teach-one-outcome LIVE structure and lesson pacing",
            "Question loops, demonstrations and audience comprehension",
            "Educational short-form hooks and searchable topics",
            "Series design, worksheets/resources and learner retention",
            "Authority building without overstating qualifications or claims",
        ],
    },
    "business": {
        "title": "Business & Entrepreneurship",
        "icon": "💼",
        "theme": {"accent": "#f2cf78", "secondary": "#67bfff", "glow": "#f2cf7855"},
        "training": [
            "Business-story LIVE formats and practical value delivery",
            "Case-study, process and behind-the-scenes content",
            "Search-led educational videos and authority building",
            "Community Q&A, recurring series and lead-safe calls to action",
            "Professional positioning, disclosure and trust",
        ],
    },
    "technology": {
        "title": "Technology & AI",
        "icon": "🤖",
        "theme": {"accent": "#63e6ff", "secondary": "#9a72ff", "glow": "#63e6ff55"},
        "training": [
            "Demo-led LIVE structure and real-time problem solving",
            "Tool comparisons, tutorials and searchable explainers",
            "Screen/demo preparation and audience question handling",
            "News-to-education content without unsupported claims",
            "Repeatable series around workflows, builds and discoveries",
        ],
    },
    "art_design": {
        "title": "Art, Design & Crafts",
        "icon": "🎨",
        "theme": {"accent": "#ff8fd6", "secondary": "#68dcff", "glow": "#ff8fd655"},
        "training": [
            "Create-with-me LIVE structure and progress milestones",
            "Process videos, reveals and satisfying transformation content",
            "Audience prompts, themes and collaborative creative choices",
            "Portfolio storytelling and recurring creative series",
            "Camera setup for hands-on creation and detail work",
        ],
    },
    "comedy": {
        "title": "Comedy & Entertainment",
        "icon": "😂",
        "theme": {"accent": "#ffe36d", "secondary": "#ff7fa9", "glow": "#ffe36d55"},
        "training": [
            "Recurring LIVE segments, callbacks and audience participation",
            "Character, sketch and reaction short-form systems",
            "Hook-to-payoff pacing and retention",
            "Community prompts and improvisation without harassment",
            "Clip mining and repeatable entertainment formats",
        ],
    },
    "spirituality": {
        "title": "Spirituality & Mindful Community",
        "icon": "✨",
        "theme": {"accent": "#d8a6ff", "secondary": "#73e6d5", "glow": "#d8a6ff55"},
        "training": [
            "Calm, inclusive LIVE room structure and recurring rituals",
            "Storytelling, reflection and community discussion",
            "Short-form affirmations, prompts and educational content",
            "Boundaries around medical, financial and certainty claims",
            "Community care, moderation and repeat-viewer belonging",
        ],
    },
    "lifestyle": {
        "title": "Lifestyle & Daily Life",
        "icon": "🌻",
        "theme": {"accent": "#ffd477", "secondary": "#ff98c9", "glow": "#ffd47755"},
        "training": [
            "Personality-led LIVE structure with repeatable segments",
            "Daily-life storytelling and relatable short-form hooks",
            "Community conversation and audience-question systems",
            "Series planning around routines, opinions and experiences",
            "Consistency without oversharing private information",
        ],
    },
    "family_parenting": {
        "title": "Family & Parenting",
        "icon": "👨‍👩‍👧‍👦",
        "theme": {"accent": "#8fdcff", "secondary": "#ffc28f", "glow": "#8fdcff55"},
        "training": [
            "Parenting/life discussion formats and community support",
            "Story-led short-form content and recurring family-safe series",
            "Privacy-first boundaries involving children and family members",
            "Audience Q&A without unsafe professional claims",
            "Positive moderation and sustainable community growth",
        ],
    },
    "talk_chat": {
        "title": "Talk, Podcast & Real Conversation",
        "icon": "🎙️",
        "theme": {"accent": "#c69cff", "secondary": "#65dcff", "glow": "#c69cff55"},
        "training": [
            "Topic-led LIVE structure, openers and 20-minute reset loops",
            "Guest, panel and audience-question formats",
            "Clip extraction from longer conversations",
            "Moderation, disagreement and community standards",
            "Searchable discussion topics and recurring shows",
        ],
    },
    "battle_entertainment": {
        "title": "Battles & Interactive Entertainment",
        "icon": "⚔️",
        "theme": {"accent": "#ff8a7a", "secondary": "#f4c873", "glow": "#ff8a7a55"},
        "training": [
            "Battle scheduling, show structure and audience preparation",
            "Entertainment-first competition and safe calls to action",
            "Pre-battle/post-battle short-form promotion",
            "Collaboration etiquette and repeatable match formats",
            "Retention outside battles so the room has its own identity",
        ],
    },
    "sports": {
        "title": "Sports & Sports Community",
        "icon": "⚽",
        "theme": {"accent": "#79e0a4", "secondary": "#64b9ff", "glow": "#79e0a455"},
        "training": [
            "Match/topic discussion LIVE formats",
            "Reaction, analysis and searchable short-form content",
            "Community predictions and interactive discussion",
            "Rights-aware use of footage and third-party media",
            "Recurring shows, schedules and fan-community retention",
        ],
    },
    "automotive": {
        "title": "Automotive",
        "icon": "🚗",
        "theme": {"accent": "#ff9e66", "secondary": "#66caff", "glow": "#ff9e6655"},
        "training": [
            "Walk-around, build and repair LIVE structures",
            "Before/after and process-based short-form videos",
            "Technical explanation and audience-question formats",
            "Safety-first demonstrations and responsible driving content",
            "Recurring project/build storytelling",
        ],
    },
    "books_writing": {
        "title": "Books, Writing & Storytelling",
        "icon": "📚",
        "theme": {"accent": "#d5b083", "secondary": "#b895ff", "glow": "#d5b08355"},
        "training": [
            "Reading/writing LIVE formats and discussion prompts",
            "Story hooks, excerpts and searchable recommendation content",
            "Writing-progress and behind-the-scenes series",
            "Community prompts, reviews and book-club formats",
            "Copyright-aware quotation and source handling",
        ],
    },
    "pets_animals": {
        "title": "Pets & Animals",
        "icon": "🐾",
        "theme": {"accent": "#ffca7a", "secondary": "#78d9b6", "glow": "#ffca7a55"},
        "training": [
            "Animal-led LIVE formats with clear recurring segments",
            "Personality, routine and transformation short-form content",
            "Audience questions and community storytelling",
            "Animal welfare and safety-first boundaries",
            "Repeatable series around training, care and daily moments",
        ],
    },
    "health_wellness": {
        "title": "Wellness & Self-Care",
        "icon": "🌿",
        "theme": {"accent": "#88dfb5", "secondary": "#91b8ff", "glow": "#88dfb555"},
        "training": [
            "Routine-led LIVE formats and supportive community discussion",
            "Self-care, habit and lifestyle short-form series",
            "Safe wording around health and wellbeing claims",
            "Audience participation without diagnosis or treatment advice",
            "Consistency, moderation and positive community culture",
        ],
    },
    "other": {
        "title": "Other / Custom Niche",
        "icon": "🌌",
        "theme": {"accent": "#f4c873", "secondary": "#9b72ff", "glow": "#9b72ff55"},
        "training": [
            "Define the audience promise and recurring LIVE format",
            "Build three to five repeatable content pillars",
            "Create searchable short-form hooks around the niche",
            "Design interaction loops and community identity",
            "Measure retention, consistency and audience response",
        ],
    },
}


class EspNicheStore:
    def __init__(self, esp_store: EspStore | None = None):
        self.esp = esp_store or esp
        self.db_path = self.esp.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS esp_niche_profiles (
                    user_id TEXT PRIMARY KEY,
                    niche TEXT NOT NULL,
                    sub_niche TEXT,
                    audience TEXT,
                    goals_json TEXT NOT NULL DEFAULT '[]',
                    network_status TEXT NOT NULL DEFAULT 'unsure',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                """
            )

    def get(self, user_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM esp_niche_profiles WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["goals"] = json.loads(item.pop("goals_json") or "[]")
        except Exception:
            item["goals"] = []
        item["catalog"] = niche_definition(item["niche"])
        return item

    def set(
        self,
        user_id: str,
        *,
        niche: str,
        sub_niche: str = "",
        audience: str = "",
        goals: list[str] | None = None,
        network_status: NetworkStatus = "unsure",
    ) -> dict:
        niche = (niche or "").strip().lower()
        if niche not in NICHE_CATALOG:
            raise ValueError("Choose a valid creator niche")
        if network_status not in {"esp_only", "other_network", "unsure"}:
            raise ValueError("Choose a valid Creator Network affiliation status")
        cleaned_goals = [str(value).strip()[:180] for value in (goals or []) if str(value).strip()][:20]
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO esp_niche_profiles
                    (user_id,niche,sub_niche,audience,goals_json,network_status,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    niche=excluded.niche,
                    sub_niche=excluded.sub_niche,
                    audience=excluded.audience,
                    goals_json=excluded.goals_json,
                    network_status=excluded.network_status,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    niche,
                    (sub_niche or "").strip()[:240],
                    (audience or "").strip()[:1000],
                    json.dumps(cleaned_goals),
                    network_status,
                    now,
                ),
            )
        return self.get(user_id) or {}


def niche_definition(niche: str | None) -> dict:
    key = (niche or "other").strip().lower()
    return NICHE_CATALOG.get(key, NICHE_CATALOG["other"])


def esp_access_state(request: Request) -> tuple[object, dict]:
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in to access the ESP Creator & Agent Hub")
    membership = esp.membership(member.user_id)
    if not membership or membership.get("status") not in {"active", "owner"}:
        raise HTTPException(403, "This area is available only to approved Elevate Souls Productions creators, agents and owners")
    role = (membership.get("roles") or "").strip().lower()
    if membership.get("status") != "owner" and role not in {"creator", "agent", "both"}:
        raise HTTPException(403, "An active ESP creator or agent role is required")
    return member, membership


def social_access_reason(membership: dict | None, profile: dict | None) -> tuple[bool, str]:
    if not membership or membership.get("status") not in {"active", "owner"}:
        return False, "ESP approval is required."
    if not profile:
        return False, "Select your creator niche before using ESP social-management training and tools."
    if profile.get("network_status") == "other_network":
        return False, (
            "ESP Social Management is not available for an account currently represented by another Creator Network. "
            "Elevate Souls Productions does not use this system to poach or manage creators belonging to another network."
        )
    if profile.get("network_status") != "esp_only":
        return False, "Confirm your current Creator Network affiliation before using ESP social-management tools."
    return True, "ESP social-management access confirmed."


def require_esp_hub_member(request: Request):
    return esp_access_state(request)


def require_esp_social_member(request: Request):
    member, membership = esp_access_state(request)
    profile = EspNicheStore().get(member.user_id)
    allowed, reason = social_access_reason(membership, profile)
    if not allowed:
        raise HTTPException(403, reason)
    return member, membership, profile
