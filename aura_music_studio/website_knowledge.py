from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class WebsiteKnowledgePage:
    id: str
    title: str
    url: str
    category: str
    scope: str
    summary: str
    status: str = "verified"
    source_priority: str = "historical_public_website"

    def public_dict(self) -> dict:
        return asdict(self)


# Snapshot imported from the public Elevate Souls Productions website on 2026-08-23.
# Website content is treated as historical/source material. Current Drive/system records win
# whenever programme terms, regions, figures, certifications, incentives or policies conflict.
#
# Scope controls are intentionally stricter than the old website: material that belongs to the
# TikTok creator/agent operating system remains ESP-gated even when an older public page existed.
PAGES: tuple[WebsiteKnowledgePage, ...] = (
    WebsiteKnowledgePage(
        "home",
        "Elevate Souls Productions",
        "https://www.elevatesoulsproductions.com/",
        "company",
        "public",
        "Public company overview: ESP presents itself as a global multi-certified TikTok LIVE Creator Network agency focused on creator mentorship, training, community, growth, authenticity and purposeful media. The page describes personal mentorship, LIVE growth strategy, content/brand development, certified training, collaboration, professional development, wellbeing support and creator opportunities. Historical on-page metrics must not be treated as current unless separately verified.",
    ),
    WebsiteKnowledgePage(
        "recruitment-hub",
        "ESP Creator Network Recruitment Hub",
        "https://www.elevatesoulsproductions.com/esp-cn-recruitment-hub/",
        "recruitment",
        "public",
        "Public work-in-progress page describing a Creator Network recruitment hub: Discord-based verification, ticket onboarding, recruitment tracking and moderation first, followed by a mobile-first website/app and broader recruitment workflows.",
    ),
    WebsiteKnowledgePage(
        "creator-tools",
        "Creator Tools",
        "https://www.elevatesoulsproductions.com/creator-tools/",
        "creator-tools",
        "esp_creator",
        "Known website route for creator tools. The route was linked from the public navigation but could not be fetched during the 2026-08-23 import. Keep it in the catalogue for later refresh instead of silently losing the page.",
        status="needs_refresh",
    ),
    WebsiteKnowledgePage(
        "earnings-calculator",
        "Earnings Calculator For Creators",
        "https://www.elevatesoulsproductions.com/creator-tools/earnings-calculator-for-creators/",
        "creator-tools",
        "esp_creator",
        "Creator-facing earnings/calculation resource from the legacy site. In the new system this belongs inside the ESP Creator toolset and must not be exposed as an ordinary Music/Video/Image Studio feature.",
    ),
    WebsiteKnowledgePage(
        "about",
        "About Elevate Souls Productions",
        "https://www.elevatesoulsproductions.com/about-us/",
        "company",
        "public",
        "Company story, purpose and values. It emphasizes kindness, connection, integrity, empathy, collaboration, creator confidence, inclusive opportunity, mentorship, official education, global community and leadership development, with a long-term vision of a fairer creator industry.",
    ),
    WebsiteKnowledgePage(
        "agent-apprentice",
        "Agent Apprentice Program",
        "https://www.elevatesoulsproductions.com/about-us/agent-apprentice-program-1/",
        "agent-training",
        "esp_agent",
        "Legacy public description of ESP's pathway for people learning to become Creator Managers/Agents. The new system treats the programme and training content as ESP Agent-only material and newer Drive/academy records override historical website wording.",
    ),
    WebsiteKnowledgePage(
        "certifications",
        "Certifications & Awards",
        "https://www.elevatesoulsproductions.com/about-us/certifications-and-awards-1/",
        "company",
        "public",
        "Historical certifications/recognition page describing TikTok LIVE Creator Network certifications across multiple regions and certified creator-management expertise. Certification counts and territory claims are time-sensitive and must be checked against newer authoritative records before Aura states them as current.",
    ),
    WebsiteKnowledgePage(
        "contact",
        "Contact Us",
        "https://www.elevatesoulsproductions.com/about-us/contact-us/",
        "company",
        "public",
        "Contact and community page explaining Discord as ESP's communication hub for training, support, announcements, voice/video calls and shared resources. It also directs visitors to TikTok profiles, the ESP Discord community and the public ESP email address.",
    ),
    WebsiteKnowledgePage(
        "exclusive-incentives",
        "Exclusive Incentives",
        "https://www.elevatesoulsproductions.com/about-us/exclusive-incentives-1/",
        "creator-programmes",
        "esp_creator",
        "Historical public overview of ESP creator incentive programmes. Incentive thresholds, reward values and eligibility are inherently time-sensitive; the current ESP system/Drive rules always override this page.",
    ),
    WebsiteKnowledgePage(
        "mary-kev-words",
        "In Mary & Kev's Words",
        "https://www.elevatesoulsproductions.com/about-us/in-mary-and-kev-s-words/",
        "company",
        "public",
        "Founders' narrative and brand philosophy: purposeful media, love, light, kindness, humanity first, creator independence, mentorship, music, creative development and community. It records the evolution from the earlier Smart Tok Viral community into Elevate Souls Productions and the principle of helping creators without controlling their platforms.",
    ),
    WebsiteKnowledgePage(
        "partnerships",
        "Partnerships",
        "https://www.elevatesoulsproductions.com/about-us/partnerships-1/",
        "partnerships",
        "public",
        "Historical partnerships page, including public material about the Sunfly Karaoke relationship and creator-music compliance/support. Partnership status and licensing claims must be revalidated before Aura presents them as current contractual facts.",
    ),
    WebsiteKnowledgePage(
        "exclusive-programmes",
        "Exclusive TikTok Programs With ESP",
        "https://www.elevatesoulsproductions.com/exclusive-tik-tok-programs-with-esp/",
        "creator-programmes",
        "esp_creator",
        "Legacy programme hub for special TikTok/ESP creator opportunities, including the UK+ Vertical Creator Partnership Program for musicians. The new system exposes programme details only to the appropriate ESP roles.",
    ),
    WebsiteKnowledgePage(
        "musicians-partnership",
        "Musicians Creator Partnership Program",
        "https://www.elevatesoulsproductions.com/exclusive-tik-tok-programs-with-esp/musicians-creator-partnership-program/",
        "creator-programmes",
        "esp_creator",
        "Legacy UK+ musician-focused creator partnership information describing a TikTok-linked programme for music creators. Eligibility, territory, targets and certification requirements must come from current programme records rather than this historical snapshot.",
    ),
    WebsiteKnowledgePage(
        "locations-hub",
        "Our Locations / Join Us",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/",
        "recruitment",
        "public",
        "Global recruitment/expansion overview describing ESP's people-first goal of widening access to creator training, support, ethical agency representation, community and professional pathways across TikTok LIVE regions. Historical region availability is not a source of truth for current operating territory.",
    ),
    WebsiteKnowledgePage(
        "usa-canada",
        "USA & Canada",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/usa-and-canada/",
        "regional-recruitment",
        "public",
        "Regional welcome/recruitment page for creators in the United States and Canada, centered on creator individuality, culture, training, tools, international support and community.",
    ),
    WebsiteKnowledgePage(
        "uk-plus",
        "UK Plus Region",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/uk-plus-region/",
        "regional-recruitment",
        "public",
        "Multilingual regional welcome/recruitment page for the historical UK+ footprint, emphasizing certified training, community programmes, networking, individuality, culture and creative growth. Current UK+ country routing must come from the live ESP region configuration.",
    ),
    WebsiteKnowledgePage(
        "latin-america",
        "Latin America",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/latin-america/",
        "regional-recruitment",
        "public",
        "English/Spanish regional welcome and recruitment content for Latin American creators, emphasizing culture, authenticity, certified creator programmes, mentorship, networking and global opportunity. Current country eligibility is governed by newer ESP/TikTok records.",
    ),
    WebsiteKnowledgePage(
        "thailand",
        "Thailand",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/thailand/",
        "regional-recruitment",
        "public",
        "Historical Thailand regional recruitment/welcome page for TikTok LIVE creators, preserving the site's localized cultural and creator-support messaging while leaving current availability to newer operating records.",
    ),
    WebsiteKnowledgePage(
        "se-nordics",
        "SE+ Nordics",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/se-nordics/",
        "regional-recruitment",
        "public",
        "Historical SE+/Nordics regional welcome and recruitment page. It is retained for brand/history/reference but is not authoritative for present-day region coverage.",
    ),
    WebsiteKnowledgePage(
        "philippines",
        "Philippines",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/philippines/",
        "regional-recruitment",
        "public",
        "Historical Philippines regional welcome/recruitment content centered on creator support, creative identity, training and global community.",
    ),
    WebsiteKnowledgePage(
        "mena",
        "Middle East & North Africa (MENA)",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/middle-east-and-north-africa-mena/",
        "regional-recruitment",
        "public",
        "Historical MENA regional creator welcome/recruitment page. Aura may use it as brand/history context but must verify current country/region eligibility from the live ESP operating system.",
    ),
    WebsiteKnowledgePage(
        "korea",
        "Korea",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/korea/",
        "regional-recruitment",
        "public",
        "Historical South Korea creator welcome/recruitment content retained for localized brand context. Current age, programme and regional eligibility rules must come from current policy data.",
    ),
    WebsiteKnowledgePage(
        "japan",
        "Japan",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/japan/",
        "regional-recruitment",
        "public",
        "Historical Japan regional TikTok LIVE creator welcome/recruitment page emphasizing community, creativity, development and global opportunity.",
    ),
    WebsiteKnowledgePage(
        "indonesia",
        "Indonesia",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/id-indonesia/",
        "regional-recruitment",
        "public",
        "Historical Indonesia regional TikTok LIVE creator welcome/recruitment page retained as localized public brand content.",
    ),
    WebsiteKnowledgePage(
        "spain-andorra",
        "ES+ Spain & Andorra",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/es-spain-and-andora/",
        "regional-recruitment",
        "public",
        "Historical ES+ regional welcome/recruitment content for Spain/Andorra. It is a legacy marketing source, not the authority for current network territory or programme eligibility.",
    ),
    WebsiteKnowledgePage(
        "de-plus",
        "DE+ Germany",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/de-germany/",
        "regional-recruitment",
        "public",
        "English/German regional welcome and recruitment page covering the historical DE+ grouping and emphasizing creativity, certified programmes, networking and international creator support. Current territory must be verified against live ESP records.",
    ),
    WebsiteKnowledgePage(
        "brazil",
        "Brazil",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/brazil/",
        "regional-recruitment",
        "public",
        "English/Portuguese historical Brazil creator welcome/recruitment page celebrating local culture and describing training, global collaboration and creator support. It is preserved even where Brazil is not part of a current LATAM operational routing rule.",
    ),
    WebsiteKnowledgePage(
        "australia-new-zealand",
        "Australia & New Zealand",
        "https://www.elevatesoulsproductions.com/our-locations-join-us-1/australia-and-new-zealand/",
        "regional-recruitment",
        "public",
        "Historical AU+ regional welcome/recruitment page with multilingual Pacific greetings and creator-support messaging. Current AU/NZ routing and eligible territories come from current ESP configuration.",
    ),
    WebsiteKnowledgePage(
        "news-updates",
        "News & Updates",
        "https://www.elevatesoulsproductions.com/news-and-updates-1/",
        "creator-operations",
        "esp_creator",
        "Legacy hub for TikTok LIVE campaigns, policy/guideline updates, expert training/webinars, creator tools and useful platform links. Because this material changes quickly, Aura must prefer current TikTok/ESP records and treat the page as historical unless refreshed.",
    ),
    WebsiteKnowledgePage(
        "press-links",
        "Press Links for Elevate Souls Productions",
        "https://www.elevatesoulsproductions.com/news-and-updates-1/press-links-for-elevate-souls-productions/",
        "press",
        "public",
        "Large legacy directory of press, profile and external media links relating to ESP and its founders. Retain the page as a source directory; individual third-party claims should be verified at their original source before reuse.",
    ),
    WebsiteKnowledgePage(
        "press-story",
        "Press Release: The Story So Far",
        "https://www.elevatesoulsproductions.com/news-and-updates-1/press-release-the-story-so-far/",
        "press",
        "public",
        "Historical October 2025 press-release narrative describing ESP's growth from a creator/community movement into a global Creator Network, its music/mentorship focus, founder story and then-current market footprint. Dates, market counts and scale claims are preserved as historical statements rather than current facts.",
    ),
    WebsiteKnowledgePage(
        "agent-hub",
        "ESP Agent Hub",
        "https://www.elevatesoulsproductions.com/esp-agent-hub/",
        "agent-operations",
        "esp_agent",
        "Legacy agent/agency expansion hub discussing ESP's worldwide mission, professional training, compliance, community values and pathways for creator agents/managers and training leaders. Detailed agent systems remain ESP Agent-only in the new platform.",
    ),
    WebsiteKnowledgePage(
        "golden-rule-agency",
        "Golden Rule Agency",
        "https://www.elevatesoulsproductions.com/esp-agent-hub/golden-rule-agency/",
        "agent-network",
        "esp_agent",
        "Legacy sub-agency/agent-network page preserved for historical ESP partner/agency context. Current agency status, commercial terms and permissions must be verified from current ESP records.",
    ),
    WebsiteKnowledgePage(
        "creative-sparks-agency",
        "Creative Sparks Agency",
        "https://www.elevatesoulsproductions.com/esp-agent-hub/creative-sparks-agency/",
        "agent-network",
        "esp_agent",
        "Known legacy sub-agency route linked by the website. It could not be fetched during the import, so it is retained with a refresh flag instead of being omitted.",
        status="needs_refresh",
    ),
    WebsiteKnowledgePage(
        "fun-games",
        "Fun & Games",
        "https://www.elevatesoulsproductions.com/fun-and-games/",
        "community",
        "esp_creator",
        "Known legacy community/games route linked from the website. The page could not be fetched during the import and is queued for a later refresh.",
        status="needs_refresh",
    ),
    WebsiteKnowledgePage(
        "tri-zone-star-shooter",
        "Tri-Zone Star Shooter",
        "https://www.elevatesoulsproductions.com/fun-and-games/tri-zone-star-shooter/",
        "community",
        "esp_creator",
        "Known legacy game route linked from the website. The page could not be fetched during the import and is preserved for later refresh rather than dropped from the knowledge map.",
        status="needs_refresh",
    ),
)


TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(value or "") if len(token) > 1}


def allowed_scopes(*, esp_roles: Iterable[str] = (), owner: bool = False) -> set[str]:
    if owner:
        return {"public", "esp_creator", "esp_agent", "owner"}
    roles = {str(role).strip().lower() for role in esp_roles}
    scopes = {"public"}
    if roles & {"creator", "both"}:
        scopes.add("esp_creator")
    if roles & {"agent", "both"}:
        scopes.update({"esp_creator", "esp_agent"})
    return scopes


def search_website_knowledge(
    query: str,
    *,
    scopes: set[str] | None = None,
    limit: int = 8,
) -> list[dict]:
    """Simple local retrieval over the imported ESP website knowledge map.

    The index is deliberately dependency-free. A future embedding/RAG layer can replace the
    scorer without changing the access classification or source metadata.
    """
    wanted = _tokens(query)
    if not wanted:
        return []
    permitted = scopes or {"public"}
    ranked: list[tuple[int, WebsiteKnowledgePage]] = []
    for page in PAGES:
        if page.scope not in permitted:
            continue
        title_tokens = _tokens(page.title)
        body_tokens = _tokens(f"{page.category} {page.summary} {page.url}")
        score = 4 * len(wanted & title_tokens) + len(wanted & body_tokens)
        if score:
            ranked.append((score, page))
    ranked.sort(key=lambda item: (-item[0], item[1].title.lower()))
    return [page.public_dict() | {"score": score} for score, page in ranked[: max(1, min(limit, 25))]]


def website_knowledge_manifest() -> dict:
    by_scope: dict[str, int] = {}
    for page in PAGES:
        by_scope[page.scope] = by_scope.get(page.scope, 0) + 1
    return {
        "source": "https://www.elevatesoulsproductions.com/",
        "snapshot_date": "2026-08-23",
        "precedence": "Current ESP/TikTok/Drive/system records override legacy website wording when facts conflict.",
        "pages": len(PAGES),
        "by_scope": by_scope,
        "needs_refresh": [page.url for page in PAGES if page.status != "verified"],
    }
