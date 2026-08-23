from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .membership import MembershipService

router = APIRouter()
store = AccountStore()
memberships = MembershipService(store)
MEMBER_COOKIE = "lss_session"
MAX_SECONDARY_NICHES = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _niche(title: str, description: str, theme: str, modules: list[str]) -> dict[str, Any]:
    return {"title": title, "description": description, "theme": theme, "modules": modules}


NICHE_CATALOG: dict[str, dict[str, Any]] = {
    "music": _niche("Music / Singing", "Perform, write, release and grow a music-led LIVE community.", "music", ["Music LIVE structure", "Song and set planning", "Audience requests", "Artist development"]),
    "gaming": _niche("Gaming", "Build watchable gaming LIVE formats, clips and returning communities.", "gaming", ["Gaming LIVE structure", "Viewer interaction", "Clip strategy", "Game and event planning"]),
    "just-chatting": _niche("Just Chatting", "Turn conversation, personality and community into a structured LIVE show.", "lifestyle", ["Conversation formats", "Retention loops", "Community prompts", "LIVE topic planning"]),
    "comedy": _niche("Comedy", "Develop repeatable comedy formats, characters, bits and audience participation.", "lifestyle", ["Comedy formats", "Bit development", "Audience participation", "Clip moments"]),
    "entertainment": _niche("Entertainment", "Build performance-led, variety and interactive entertainment programming.", "lifestyle", ["Show structure", "Audience participation", "Recurring segments", "Event planning"]),
    "beauty": _niche("Beauty", "Create tutorial, transformation, consultation and product-led content responsibly.", "beauty", ["Tutorial structure", "Lighting and camera", "Q&A formats", "Content series"]),
    "fashion": _niche("Fashion", "Build styling, review, lookbook and fashion-community formats.", "fashion", ["Styling LIVE formats", "Lookbook content", "Camera presentation", "Community prompts"]),
    "lifestyle": _niche("Lifestyle", "Turn everyday interests and personality into consistent content pillars.", "lifestyle", ["Content pillars", "LIVE structure", "Storytelling", "Community building"]),
    "fitness": _niche("Fitness", "Plan safe, engaging fitness content and community-focused LIVE sessions.", "fitness", ["Session structure", "Camera setup", "Challenge formats", "Community accountability"]),
    "wellness": _niche("Wellness", "Create responsible wellbeing content with clear boundaries and supportive community formats.", "wellbeing", ["Wellbeing formats", "Community boundaries", "Routine content", "Educational prompts"]),
    "food": _niche("Food / Cooking", "Turn recipes and food knowledge into interactive, watchable creator formats.", "food", ["Cooking-show structure", "Recipe content", "Camera angles", "Audience voting"]),
    "education": _niche("Education", "Teach clearly through repeatable lessons, demonstrations and interactive LIVE formats.", "education", ["Lesson formats", "Question loops", "Visual teaching", "Series planning"]),
    "business": _niche("Business / Entrepreneurship", "Build professional educational content around business experience and skills.", "business", ["Authority positioning", "Educational LIVE", "Case-study content", "Professional CTA"]),
    "technology": _niche("Technology", "Create demos, explainers, reviews and technical community content.", "technology", ["Demo structure", "Explainers", "Review framework", "Technical Q&A"]),
    "art": _niche("Art / Illustration", "Make the creative process itself part of the show and content strategy.", "art", ["Process LIVE", "Reveal formats", "Audience prompts", "Portfolio content"]),
    "crafts": _niche("Crafts / DIY", "Build process-led, tutorial and project-reveal creator formats.", "art", ["Project structure", "Tutorial format", "Camera setup", "Reveal content"]),
    "photography": _niche("Photography", "Create shoots, editing walkthroughs, critique and education formats.", "art", ["Shoot formats", "Editing content", "Critique structure", "Portfolio storytelling"]),
    "asmr": _niche("ASMR", "Design calm, repeatable sensory formats with strong audio and room discipline.", "asmr", ["Audio setup", "Trigger planning", "Session pacing", "Quiet-room retention"]),
    "spiritual": _niche("Spiritual / Wellbeing", "Create reflective, community-centred content without overstating certainty or personal insight.", "spiritual", ["Reflective LIVE formats", "Community care", "Content boundaries", "Audience prompts"]),
    "motivation": _niche("Motivation", "Turn lived experience and constructive encouragement into repeatable content.", "spiritual", ["Story framework", "Actionable prompts", "Community goals", "Series planning"]),
    "travel": _niche("Travel", "Create destination, journey, guide and experience-led creator formats.", "lifestyle", ["Travel storytelling", "Guide content", "LIVE-on-location", "Series planning"]),
    "sport": _niche("Sport", "Build sports discussion, training, analysis and community formats within platform rules.", "fitness", ["Sports LIVE format", "Training content", "Analysis structure", "Community debate"]),
    "dance": _niche("Dance", "Develop performance, teaching, choreography and challenge-led content.", "dance", ["Performance LIVE", "Choreography content", "Teaching format", "Challenge strategy"]),
    "acting": _niche("Acting", "Build scene, character, audition, monologue and performance-led content.", "lifestyle", ["Performance formats", "Character content", "Scene planning", "Audience prompts"]),
    "cosplay": _niche("Cosplay", "Combine making, transformation, performance and fandom community content.", "fashion", ["Build process", "Transformation format", "Character performance", "Community content"]),
    "collecting": _niche("Collecting", "Turn collections, knowledge and discoveries into recurring shows and reviews.", "lifestyle", ["Collection tours", "Review format", "Discovery content", "Community questions"]),
    "automotive": _niche("Automotive", "Build safe vehicle, modification, review and enthusiast community content.", "technology", ["Walkaround formats", "Review structure", "Project updates", "Community Q&A"]),
    "pets": _niche("Animals / Pets", "Create responsible pet, animal-care and personality-led community content.", "lifestyle", ["Pet LIVE structure", "Care content", "Story content", "Community prompts"]),
    "family": _niche("Parenting / Family", "Build family and parenting content with strong privacy and safeguarding boundaries.", "lifestyle", ["Family-safe formats", "Privacy boundaries", "Advice structure", "Community topics"]),
    "books": _niche("Books / Literature", "Create reviews, reading communities, discussion and author-focused content.", "education", ["Review framework", "Book-club LIVE", "Discussion prompts", "Recommendation series"]),
    "history": _niche("History", "Make historical topics watchable through stories, evidence and interactive discussion.", "education", ["Story-led teaching", "Source framing", "Question loops", "Series planning"]),
    "science": _niche("Science", "Create accessible science education, demonstrations and discussion content.", "education", ["Explain simply", "Demonstration format", "Evidence framing", "Audience Q&A"]),
    "languages": _niche("Languages", "Teach and practise languages through interactive, repeatable creator formats.", "education", ["Micro-lessons", "Conversation practice", "Quiz formats", "Series planning"]),
    "life-advice": _niche("Relationships / Life Advice", "Create grounded discussion and advice content without presenting professional services you do not provide.", "wellbeing", ["Topic structure", "Boundaries", "Audience questions", "Actionable takeaways"]),
    "reaction": _niche("Reaction", "Build transformative reaction and commentary formats with clear creator value.", "lifestyle", ["Reaction structure", "Transformative commentary", "Clip strategy", "Topic planning"]),
    "reviews": _niche("Reviews", "Create consistent review systems that make comparisons useful and trustworthy.", "business", ["Review framework", "Comparison format", "Evidence notes", "Recommendation CTA"]),
    "podcasts": _niche("Interviews / Podcasts", "Build structured interviews, conversations, clips and recurring programmes.", "business", ["Interview structure", "Guest preparation", "Clip moments", "Recurring segments"]),
    "news-commentary": _niche("News / Commentary", "Create platform-compliant commentary with sourcing, context and clear separation of fact from opinion.", "business", ["Source discipline", "Commentary format", "Context framing", "Community discussion"]),
    "specialist": _niche("Specialist", "Build around a professional, technical or specialist subject not covered elsewhere.", "business", ["Authority map", "Teaching format", "Community questions", "Series planning"]),
    "multi-niche": _niche("Multi-Niche Creator", "Combine two to five creator pillars while keeping a clear audience promise.", "default", ["Pillar architecture", "Audience bridge", "Weekly mix", "Cross-niche series"]),
    "other": _niche("Other / Build My Niche", "Describe your creator direction and let Aura help structure it into a clear niche.", "default", ["Niche discovery", "Audience promise", "Content pillars", "Testing plan"]),
}

ACADEMY_LEVELS = [
    "Beginner",
    "Developing",
    "Advanced",
    "Professional",
    "LIVE Growth",
    "Content Strategy",
    "Community Building",
    "Permitted Monetisation",
    "Safety & Compliance",
    "Platform Rules",
    "Technical Setup",
    "Analytics",
    "Personal Development Plan",
]

UNIVERSAL_MODULES = [
    "LIVE planner and show structure",
    "Hooks, captions, CTA and SEO",
    "Creator analytics and goals",
    "Community and retention",
    "Safety, compliance and platform rules",
    "Aura creator review and action plan",
]


class NichePreferenceRequest(BaseModel):
    primary_niche: str = Field(min_length=2, max_length=80)
    secondary_niches: list[str] = Field(default_factory=list, max_length=MAX_SECONDARY_NICHES)
    custom_niche: str | None = Field(default=None, max_length=120)


class EspNicheStore:
    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or store
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS esp_niche_preferences (
                    user_id TEXT PRIMARY KEY,
                    primary_niche TEXT NOT NULL,
                    secondary_niches_json TEXT NOT NULL DEFAULT '[]',
                    custom_niche TEXT,
                    selected_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )

    def membership(self, user_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT user_id,status,roles,region,tiktok_handle FROM esp_memberships WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        membership = dict(row)
        if membership.get("status") not in {"active", "owner"}:
            return None
        if membership.get("roles") not in {"creator", "agent", "both", "owner"}:
            return None
        return membership

    def require_esp(self, user_id: str) -> dict[str, Any]:
        membership = self.membership(user_id)
        if not membership:
            raise PermissionError("Active ESP Creator Network membership is required")
        return membership

    @staticmethod
    def _validate(
        primary_niche: str,
        secondary_niches: list[str] | None = None,
        custom_niche: str | None = None,
    ) -> tuple[str, list[str], str | None]:
        primary = (primary_niche or "").strip().lower()
        if primary not in NICHE_CATALOG:
            raise ValueError("Choose a valid creator niche")

        secondary: list[str] = []
        seen: set[str] = set()
        for raw in secondary_niches or []:
            slug = (raw or "").strip().lower()
            if slug not in NICHE_CATALOG or slug in {"multi-niche", "other"}:
                raise ValueError("Secondary niches must be standard creator niches")
            if slug == primary:
                continue
            if slug not in seen:
                secondary.append(slug)
                seen.add(slug)
        if len(secondary) > MAX_SECONDARY_NICHES:
            raise ValueError(f"Choose no more than {MAX_SECONDARY_NICHES} secondary niches")
        if primary == "multi-niche" and not 2 <= len(secondary) <= MAX_SECONDARY_NICHES:
            raise ValueError("Multi-Niche Creator requires between 2 and 5 selected niches")

        custom = (custom_niche or "").strip() or None
        if primary == "other":
            if not custom or len(custom) < 3:
                raise ValueError("Describe the niche you want Aura to help build")
        else:
            custom = None
        return primary, secondary, custom

    def get_preference(self, user_id: str, *, require_access: bool = True) -> dict[str, Any] | None:
        if require_access:
            self.require_esp(user_id)
        with self._connect() as con:
            row = con.execute("SELECT * FROM esp_niche_preferences WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["secondary_niches"] = json.loads(item.pop("secondary_niches_json") or "[]")
        except Exception:
            item["secondary_niches"] = []
        return item

    def set_preference(
        self,
        user_id: str,
        primary_niche: str,
        secondary_niches: list[str] | None = None,
        custom_niche: str | None = None,
    ) -> dict[str, Any]:
        self.require_esp(user_id)
        primary, secondary, custom = self._validate(primary_niche, secondary_niches, custom_niche)
        now = _now()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO esp_niche_preferences
                    (user_id,primary_niche,secondary_niches_json,custom_niche,selected_at,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    primary_niche=excluded.primary_niche,
                    secondary_niches_json=excluded.secondary_niches_json,
                    custom_niche=excluded.custom_niche,
                    updated_at=excluded.updated_at
                """,
                (user_id, primary, json.dumps(secondary), custom, now, now),
            )
        return self.get_preference(user_id) or {}

    def dashboard_context(self, user_id: str) -> dict[str, Any] | None:
        membership = self.membership(user_id)
        if not membership:
            return None
        pref = self.get_preference(user_id, require_access=False)
        if not pref:
            return {
                "esp": True,
                "membership": membership,
                "onboarding_required": True,
                "catalog": self.public_catalog(),
            }
        primary = pref["primary_niche"]
        spec = NICHE_CATALOG[primary]
        selected = pref.get("secondary_niches") or []
        modules = list(UNIVERSAL_MODULES)
        modules.extend(spec["modules"])
        for secondary in selected:
            for module in NICHE_CATALOG[secondary]["modules"][:2]:
                if module not in modules:
                    modules.append(module)
        title = pref.get("custom_niche") if primary == "other" else spec["title"]
        academy = [f"{level} · {title}" if level in {"Beginner", "Developing", "Advanced", "Professional"} else level for level in ACADEMY_LEVELS]
        return {
            "esp": True,
            "membership": membership,
            "onboarding_required": False,
            "preference": pref,
            "primary": {"slug": primary, **spec},
            "secondary": [{"slug": slug, **NICHE_CATALOG[slug]} for slug in selected],
            "theme": spec["theme"],
            "modules": modules,
            "academy": academy,
            "catalog": self.public_catalog(),
        }

    @staticmethod
    def public_catalog() -> list[dict[str, Any]]:
        return [{"slug": slug, **spec} for slug, spec in NICHE_CATALOG.items()]

    def dashboard_fragment(self, user_id: str) -> str:
        context = self.dashboard_context(user_id)
        if not context:
            return ""
        cards = []
        for item in context["catalog"]:
            cards.append(
                "<button type='button' class='esp-niche-card' data-niche='{}' data-theme='{}'>"
                "<b>{}</b><span>{}</span></button>".format(
                    escape(item["slug"], quote=True),
                    escape(item["theme"], quote=True),
                    escape(item["title"]),
                    escape(item["description"]),
                )
            )
        cards_html = "".join(cards)
        workspace = ""
        selected_slug = ""
        selected_secondary: list[str] = []
        selected_custom = ""
        if not context["onboarding_required"]:
            selected_slug = context["preference"]["primary_niche"]
            selected_secondary = context["preference"].get("secondary_niches") or []
            selected_custom = context["preference"].get("custom_niche") or ""
            module_html = "".join(f"<div class='esp-work-tile'>{escape(module)}</div>" for module in context["modules"][:10])
            academy_html = "".join(f"<span class='esp-stage'>{escape(level)}</span>" for level in context["academy"])
            workspace = f"""
            <section class='section esp-workspace' data-no-i18n='true'>
              <div class='eyebrow'>ESP Creator Workspace</div>
              <div class='esp-workspace-head'><div><h2>{escape(context['primary']['title'] if selected_slug != 'other' else selected_custom)}</h2>
              <p class='muted'>{escape(context['primary']['description'])}</p></div>
              <button type='button' class='btn' id='esp-change-niche'>Change niche</button></div>
              <div class='esp-work-grid'>{module_html}</div>
              <h3>Academy pathway</h3><div class='esp-stage-row'>{academy_html}</div>
            </section>"""

        secondary_options = "".join(
            f"<label class='esp-secondary'><input type='checkbox' value='{escape(slug, quote=True)}'><span>{escape(spec['title'])}</span></label>"
            for slug, spec in NICHE_CATALOG.items() if slug not in {"multi-niche", "other"}
        )
        initial_open = "true" if context["onboarding_required"] else "false"
        selected_secondary_json = json.dumps(selected_secondary)
        selected_slug_json = json.dumps(selected_slug)
        selected_custom_json = json.dumps(selected_custom)
        theme_json = json.dumps(context.get("theme") or "default")
        return f"""
        <style data-no-i18n='true'>
        .esp-workspace{{border-top:1px solid #ffffff14;margin-top:8px}}.esp-workspace-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}}
        .esp-work-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:18px 0}}.esp-work-tile{{padding:14px;border:1px solid var(--line);background:#120b1b;border-radius:14px;font-weight:800}}
        .esp-stage-row{{display:flex;flex-wrap:wrap;gap:7px}}.esp-stage{{border:1px solid var(--line);border-radius:999px;padding:7px 10px;color:var(--muted);font-size:.8rem}}
        .esp-niche-modal{{position:fixed;inset:0;z-index:2147482500;background:#050208eF;display:none;overflow:auto;padding:24px}}.esp-niche-modal.open{{display:block}}
        .esp-niche-panel{{max-width:1180px;margin:20px auto;background:linear-gradient(145deg,#1d1228,#0e0814);border:1px solid #5b3a6f;border-radius:24px;padding:24px;box-shadow:0 30px 100px #000}}
        .esp-niche-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;max-height:55vh;overflow:auto;padding:4px}}.esp-niche-card{{text-align:left;background:#120b1b;color:#fff;border:1px solid #3b294b;border-radius:14px;padding:13px;min-height:112px}}
        .esp-niche-card b{{display:block;color:#e8bd62;margin-bottom:5px}}.esp-niche-card span{{font-size:.82rem;color:#cbbfd5;line-height:1.35}}.esp-niche-card.selected{{outline:2px solid #d24fae;background:#2a1232}}
        .esp-niche-extra{{display:none;margin-top:18px;padding:16px;border:1px solid #3b294b;border-radius:16px}}.esp-niche-extra.open{{display:block}}.esp-secondary-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;max-height:220px;overflow:auto}}
        .esp-secondary{{display:flex;gap:8px;align-items:center;padding:8px;border:1px solid #3b294b;border-radius:10px}}.esp-secondary input{{width:auto;margin:0}}.esp-custom-input{{width:100%;margin-top:8px;background:#0e0914;color:#fff;border:1px solid #3b294b;border-radius:12px;padding:12px}}
        .esp-niche-actions{{display:flex;gap:10px;justify-content:flex-end;margin-top:18px;flex-wrap:wrap}}.esp-niche-error{{color:#ff9aa9;min-height:1.2em;font-weight:700}}
        @media(max-width:900px){{.esp-niche-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.esp-work-grid,.esp-secondary-grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:560px){{.esp-niche-grid,.esp-work-grid,.esp-secondary-grid{{grid-template-columns:1fr}}}}
        </style>
        {workspace}
        <div id='esp-niche-modal' class='esp-niche-modal' data-open-on-load='{initial_open}' data-no-i18n='true'>
          <div class='esp-niche-panel'><div class='eyebrow'>Personalise your ESP</div><h2>What type of creator are you?</h2>
          <p class='muted'>Choose the niche that should shape your dashboard, training, creator guidance and Aura's workspace energy. You can change this later.</p>
          <div class='esp-niche-grid'>{cards_html}</div>
          <div id='esp-multi-extra' class='esp-niche-extra'><h3>Choose 2–5 creator pillars</h3><div class='esp-secondary-grid'>{secondary_options}</div></div>
          <div id='esp-other-extra' class='esp-niche-extra'><h3>Describe your niche</h3><input id='esp-custom-niche' class='esp-custom-input' maxlength='120' placeholder='e.g. vintage restoration and historical craftsmanship'></div>
          <p id='esp-niche-error' class='esp-niche-error'></p>
          <div class='esp-niche-actions'><button type='button' class='btn' id='esp-niche-cancel'>Close</button><button type='button' class='btn primary' id='esp-niche-save'>Build my ESP workspace</button></div></div>
        </div>
        <script data-no-i18n='true'>
        (()=>{{
          const modal=document.getElementById('esp-niche-modal'); if(!modal) return;
          let primary={selected_slug_json}; const selectedSecondary=new Set({selected_secondary_json});
          const custom=document.getElementById('esp-custom-niche'); custom.value={selected_custom_json};
          const multi=document.getElementById('esp-multi-extra'), other=document.getElementById('esp-other-extra'), err=document.getElementById('esp-niche-error');
          function sync(){{
            modal.querySelectorAll('.esp-niche-card').forEach(c=>c.classList.toggle('selected',c.dataset.niche===primary));
            modal.querySelectorAll('.esp-secondary input').forEach(i=>i.checked=selectedSecondary.has(i.value));
            multi.classList.toggle('open',primary==='multi-niche'); other.classList.toggle('open',primary==='other');
          }}
          function open(){{modal.classList.add('open');document.body.style.overflow='hidden';sync()}}
          function close(){{if(modal.dataset.openOnLoad==='true' && !primary) return;modal.classList.remove('open');document.body.style.overflow=''}}
          modal.querySelectorAll('.esp-niche-card').forEach(c=>c.addEventListener('click',()=>{{primary=c.dataset.niche;err.textContent='';sync()}}));
          modal.querySelectorAll('.esp-secondary input').forEach(i=>i.addEventListener('change',()=>{{if(i.checked) selectedSecondary.add(i.value);else selectedSecondary.delete(i.value);if(selectedSecondary.size>5){{selectedSecondary.delete(i.value);i.checked=false;err.textContent='Choose no more than 5 creator pillars.'}}}}));
          document.getElementById('esp-change-niche')?.addEventListener('click',open); document.getElementById('esp-niche-cancel').addEventListener('click',close);
          document.getElementById('esp-niche-save').addEventListener('click',async()=>{{
            err.textContent=''; if(!primary){{err.textContent='Choose your creator niche first.';return}}
            const body={{primary_niche:primary,secondary_niches:[...selectedSecondary],custom_niche:custom.value.trim()||null}};
            try{{const res=await fetch('/esp/niche/preference',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}});const data=await res.json();if(!res.ok) throw new Error(data.detail||'Could not save niche');window.dispatchEvent(new CustomEvent('aura:theme',{{detail:{{niche:data.theme||primary}}}}));location.reload()}}catch(e){{err.textContent=e.message||String(e)}}
          }}));
          if(modal.dataset.openOnLoad==='true') open(); sync();
          window.dispatchEvent(new CustomEvent('aura:theme',{{detail:{{niche:{theme_json}}}}}));
        }})();
        </script>"""


niches = EspNicheStore(store)


def _session_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(MEMBER_COOKIE)


def _member_or_401(request: Request):
    try:
        return memberships.from_session(_session_token(request), require_active=True)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc


def _esp_member_or_403(request: Request):
    member = _member_or_401(request)
    try:
        membership = niches.require_esp(member.user_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return member, membership


@router.get("/esp/niche/catalog")
def niche_catalog(request: Request):
    _esp_member_or_403(request)
    return {"catalog": niches.public_catalog(), "max_secondary_niches": MAX_SECONDARY_NICHES}


@router.get("/esp/niche/context")
def niche_context(request: Request):
    member, _membership = _esp_member_or_403(request)
    return niches.dashboard_context(member.user_id)


@router.post("/esp/niche/preference")
def save_niche_preference(request: Request, payload: NichePreferenceRequest):
    member, _membership = _esp_member_or_403(request)
    try:
        pref = niches.set_preference(member.user_id, payload.primary_niche, payload.secondary_niches, payload.custom_niche)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    context = niches.dashboard_context(member.user_id) or {}
    return {
        "saved": True,
        "preference": pref,
        "theme": context.get("theme", "default"),
        "modules": context.get("modules", []),
        "academy": context.get("academy", []),
    }
