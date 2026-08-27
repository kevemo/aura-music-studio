from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["Global Compliance"])

POLICY_VERSION = "2026-08-27"
AUTHORITATIVE_POLICY_LANGUAGE = "en-GB"

SUPPORTED_LOCALES = (
    "en-GB", "en-US", "es", "fr", "de", "pt-BR", "it", "nl", "pl", "ar", "hi", "ja", "ko", "zh-CN"
)

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Global Safety & Compliance Centre",
        "safety": "No hate, bullying, harassment, exploitation, credible threats, or instructions that facilitate serious harm.",
        "professional": "Aura and this service are not licensed medical, legal, financial, mental-health, or emergency professionals. For professional or urgent matters, contact a suitably qualified professional or local emergency service.",
        "ai": "AI-generated or materially AI-edited content should be labelled or disclosed when required by law, platform policy, or context.",
        "translation": "Translated safety notices are provided for accessibility. Legally significant translations require jurisdiction-appropriate professional review; the authoritative policy language is English (UK) unless a reviewed local version is published.",
        "tiktok": "TikTok LIVE creators remain responsible for their LIVE, guests, overlays, voice-to-text, translations, and other third-party tools.",
    },
    "es": {
        "title": "Centro Global de Seguridad y Cumplimiento",
        "safety": "No se permite el odio, acoso, intimidación, explotación, amenazas creíbles ni instrucciones que faciliten daños graves.",
        "professional": "Aura y este servicio no son profesionales con licencia médica, legal, financiera, de salud mental ni de emergencias. Para asuntos profesionales o urgentes, contacte con un profesional debidamente cualificado o con los servicios de emergencia locales.",
        "ai": "El contenido generado o modificado sustancialmente por IA debe etiquetarse o divulgarse cuando lo exijan la ley, las normas de la plataforma o el contexto.",
        "translation": "Las traducciones de avisos de seguridad se ofrecen por accesibilidad. Las traducciones con importancia jurídica requieren revisión profesional adecuada a la jurisdicción.",
        "tiktok": "Los creadores de TikTok LIVE siguen siendo responsables de su LIVE, invitados, superposiciones, voz a texto, traducciones y otras herramientas de terceros.",
    },
    "fr": {
        "title": "Centre mondial de sécurité et de conformité",
        "safety": "Aucune haine, intimidation, harcèlement, exploitation, menace crédible ni instruction facilitant un préjudice grave.",
        "professional": "Aura et ce service ne sont pas des professionnels agréés de la santé, du droit, de la finance, de la santé mentale ou des urgences. Pour toute question professionnelle ou urgente, contactez un professionnel dûment qualifié ou les services d'urgence locaux.",
        "ai": "Les contenus générés ou substantiellement modifiés par l'IA doivent être signalés lorsque la loi, les règles de la plateforme ou le contexte l'exigent.",
        "translation": "Les avis de sécurité traduits sont fournis à des fins d'accessibilité. Les traductions à portée juridique nécessitent une révision professionnelle adaptée à la juridiction.",
        "tiktok": "Les créateurs TikTok LIVE restent responsables de leur LIVE, de leurs invités, des incrustations, de la transcription, des traductions et des autres outils tiers.",
    },
    "de": {
        "title": "Globales Sicherheits- und Compliance-Center",
        "safety": "Kein Hass, Mobbing, Belästigung, Ausbeutung, glaubhafte Drohungen oder Anleitungen, die schweren Schaden erleichtern.",
        "professional": "Aura und dieser Dienst sind keine zugelassenen Fachkräfte für Medizin, Recht, Finanzen, psychische Gesundheit oder Notfälle. Wenden Sie sich bei fachlichen oder dringenden Anliegen an qualifizierte Fachkräfte oder örtliche Notdienste.",
        "ai": "KI-generierte oder wesentlich KI-bearbeitete Inhalte sollten gekennzeichnet werden, wenn Recht, Plattformregeln oder der Kontext dies verlangen.",
        "translation": "Übersetzte Sicherheitshinweise dienen der Zugänglichkeit. Rechtlich bedeutsame Übersetzungen erfordern eine fachliche Prüfung für die jeweilige Rechtsordnung.",
        "tiktok": "TikTok-LIVE-Creator bleiben für ihren LIVE, Gäste, Overlays, Sprache-zu-Text, Übersetzungen und andere Drittanbieter-Tools verantwortlich.",
    },
    "pt": {
        "title": "Centro Global de Segurança e Conformidade",
        "safety": "Não são permitidos ódio, intimidação, assédio, exploração, ameaças credíveis ou instruções que facilitem danos graves.",
        "professional": "Aura e este serviço não são profissionais licenciados de medicina, direito, finanças, saúde mental ou emergência. Para assuntos profissionais ou urgentes, procure um profissional devidamente qualificado ou o serviço de emergência local.",
        "ai": "Conteúdo gerado ou materialmente editado por IA deve ser identificado quando exigido por lei, política da plataforma ou contexto.",
        "translation": "Avisos de segurança traduzidos são fornecidos para acessibilidade. Traduções com significado jurídico exigem revisão profissional adequada à jurisdição.",
        "tiktok": "Criadores do TikTok LIVE continuam responsáveis pela LIVE, convidados, sobreposições, voz para texto, traduções e outras ferramentas de terceiros.",
    },
    "ar": {
        "title": "مركز السلامة والامتثال العالمي",
        "safety": "لا يُسمح بالكراهية أو التنمر أو التحرش أو الاستغلال أو التهديدات الجدية أو التعليمات التي تسهّل ضرراً جسيماً.",
        "professional": "Aura وهذه الخدمة ليست جهة مهنية مرخّصة في الطب أو القانون أو المال أو الصحة النفسية أو الطوارئ. للمسائل المهنية أو العاجلة، تواصل مع مختص مؤهل أو خدمات الطوارئ المحلية.",
        "ai": "ينبغي وسم أو الإفصاح عن المحتوى المُنشأ أو المعدّل بشكل جوهري بالذكاء الاصطناعي عندما يفرض القانون أو سياسة المنصة أو السياق ذلك.",
        "translation": "تُقدَّم ترجمات إشعارات السلامة لتسهيل الوصول. الترجمات ذات الأثر القانوني تتطلب مراجعة مهنية مناسبة للاختصاص القضائي.",
        "tiktok": "يبقى منشئو TikTok LIVE مسؤولين عن البث والضيوف والعناصر المعروضة وتحويل الصوت إلى نص والترجمات وأدوات الطرف الثالث.",
    },
}


def canonical_locale(value: str | None) -> str:
    raw = (value or "").strip().replace("_", "-")
    if not raw:
        return AUTHORITATIVE_POLICY_LANGUAGE
    exact = next((item for item in SUPPORTED_LOCALES if item.lower() == raw.lower()), None)
    if exact:
        return exact
    language = raw.split("-", 1)[0].lower()
    match = next((item for item in SUPPORTED_LOCALES if item.split("-", 1)[0].lower() == language), None)
    return match or AUTHORITATIVE_POLICY_LANGUAGE


def request_locale(request: Request) -> str:
    explicit = request.query_params.get("lang")
    if explicit:
        return canonical_locale(explicit)
    header = request.headers.get("accept-language", "")
    for part in header.split(","):
        token = part.split(";", 1)[0].strip()
        if token:
            resolved = canonical_locale(token)
            if resolved != AUTHORITATIVE_POLICY_LANGUAGE or token.lower().startswith("en"):
                return resolved
    return AUTHORITATIVE_POLICY_LANGUAGE


def notices(locale: str) -> dict[str, str]:
    resolved = canonical_locale(locale)
    language = resolved.split("-", 1)[0].lower()
    base = _TRANSLATIONS.get(language, _TRANSLATIONS["en"])
    return {"locale": resolved, "dir": "rtl" if language == "ar" else "ltr", **base}


POLICY_SOURCES = (
    {
        "id": "tiktok-community-guidelines",
        "authority": "TikTok",
        "scope": "TikTok Community Guidelines, LIVE, monetization and commercial disclosure",
        "url": "https://www.tiktok.com/community-guidelines",
        "checked_on": POLICY_VERSION,
    },
    {
        "id": "uk-ico-children",
        "authority": "UK Information Commissioner's Office",
        "scope": "UK GDPR, privacy by design and children's information",
        "url": "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/childrens-information/",
        "checked_on": POLICY_VERSION,
    },
    {
        "id": "eu-ai-transparency",
        "authority": "European Commission / AI Office",
        "scope": "AI Act transparency for AI-generated content",
        "url": "https://digital-strategy.ec.europa.eu/en/policies/code-practice-ai-generated-content",
        "checked_on": POLICY_VERSION,
    },
    {
        "id": "w3c-wcag22",
        "authority": "W3C",
        "scope": "Web Content Accessibility Guidelines 2.2",
        "url": "https://www.w3.org/TR/WCAG22/",
        "checked_on": POLICY_VERSION,
    },
    {
        "id": "us-ftc-coppa",
        "authority": "US Federal Trade Commission",
        "scope": "Children's Online Privacy Protection Rule",
        "url": "https://www.ftc.gov/business-guidance/privacy-security/childrens-privacy",
        "checked_on": POLICY_VERSION,
    },
    {
        "id": "california-ccpa",
        "authority": "State of California",
        "scope": "California consumer privacy rights",
        "url": "https://privacy.ca.gov/california-privacy-rights/rights-under-the-california-consumer-privacy-act/",
        "checked_on": POLICY_VERSION,
    },
)


class TikTokLivePreflight(BaseModel):
    creator_age: int = Field(ge=0, le=130)
    commercial_content: bool = False
    commercial_disclosure_enabled: bool = False
    third_party_tools_enabled: bool = False
    third_party_tools_moderated: bool = False
    ai_generated_or_materially_edited: bool = False
    ai_disclosure_planned: bool = False
    gift_or_engagement_pressure: bool = False
    gambling_or_gambling_like: bool = False
    firearms_or_explosive_weapons: bool = False
    physical_altercation: bool = False
    hate_or_protected_class_attack: bool = False
    bullying_or_harassment: bool = False
    sexualized_or_explicit_content: bool = False
    unoriginal_or_permission_unclear: bool = False
    professional_topic: str | None = None
    professional_disclaimer_planned: bool = False


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str


def evaluate_tiktok_live_preflight(data: TikTokLivePreflight) -> dict[str, Any]:
    findings: list[Finding] = []
    def add(code: str, severity: str, message: str) -> None:
        findings.append(Finding(code, severity, message))

    if data.creator_age < 18:
        add("tiktok_live_age", "block", "TikTok LIVE creators must be 18 or older.")
    if data.hate_or_protected_class_attack:
        add("hate", "block", "Hate speech or attacks based on protected attributes are not permitted.")
    if data.bullying_or_harassment:
        add("harassment", "block", "Bullying or harassment must not be included or facilitated.")
    if data.firearms_or_explosive_weapons:
        add("live_weapons", "block", "TikTok LIVE does not allow showing or promoting firearms or explosive weapons.")
    if data.physical_altercation:
        add("live_altercation", "block", "TikTok LIVE does not allow physical altercations.")
    if data.gambling_or_gambling_like:
        add("live_gambling", "block", "Gambling or gambling-like participation is not permitted in TikTok LIVE.")
    if data.gift_or_engagement_pressure:
        add("gift_pressure", "block", "Do not trick or pressure viewers into Gifts or engagement.")
    if data.commercial_content and not data.commercial_disclosure_enabled:
        add("commercial_disclosure", "block", "Commercial content requires TikTok's commercial disclosure setting where applicable.")
    if data.third_party_tools_enabled and not data.third_party_tools_moderated:
        add("third_party_tools", "block", "LIVE creators remain responsible for output from translation, voice-to-text, overlays and other third-party tools; moderation controls must be enabled.")
    if data.unoriginal_or_permission_unclear:
        add("originality_rights", "block", "Do not stream content without permission or where ownership/licensing is unclear.")
    if data.sexualized_or_explicit_content:
        add("sexual_content", "review", "Sexualized or explicit LIVE content is region- and age-sensitive and may be prohibited or age-restricted; obtain current policy review before LIVE.")
    if data.ai_generated_or_materially_edited and not data.ai_disclosure_planned:
        add("ai_transparency", "review", "Add appropriate AI-content disclosure/marking before publication where required by law or platform policy.")
    if data.professional_topic and not data.professional_disclaimer_planned:
        add("professional_boundary", "review", "Professional-topic content should clearly state that Aura/ESP is not a licensed professional service and direct users to qualified help when appropriate.")

    status = "blocked" if any(item.severity == "block" for item in findings) else "review" if findings else "pass"
    return {
        "status": status,
        "policy_version": POLICY_VERSION,
        "findings": [item.__dict__ for item in findings],
        "human_review_required": status != "pass",
        "grants_esp_role_or_permission": False,
        "legal_certification": False,
        "notice": "This preflight is a safety/compliance aid, not legal advice or a guarantee of TikTok eligibility. Current platform rules and applicable local law remain authoritative.",
    }


def manifest(locale: str = AUTHORITATIVE_POLICY_LANGUAGE) -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "authoritative_policy_language": AUTHORITATIVE_POLICY_LANGUAGE,
        "supported_locales": list(SUPPORTED_LOCALES),
        "notices": notices(locale),
        "principles": {
            "safety": ["no_hate", "no_bullying", "no_harassment", "no_exploitation", "no_serious_harm_facilitation"],
            "professional_boundaries": ["medical", "legal", "financial", "mental_health", "emergency"],
            "privacy": ["data_minimisation", "privacy_by_design", "rights_requests", "child_specific_protection"],
            "ai": ["transparency", "human_escalation", "traceability", "no_false_professional_authority"],
            "accessibility": ["WCAG_2_2_target"],
            "tiktok_live": ["18_plus", "host_accountability", "commercial_disclosure", "third_party_tool_accountability", "no_gift_pressure"],
        },
        "sources": list(POLICY_SOURCES),
        "continuous_compliance": True,
        "legal_certification": False,
        "translation_legal_review_required": True,
        "role_boundary": "Compliance checks never grant member, ESP Creator, ESP Agent, mentor, administrator, or owner permissions.",
    }


@router.get("/compliance/manifest.json", include_in_schema=False)
def compliance_manifest(request: Request):
    return JSONResponse(manifest(request_locale(request)), headers={"Cache-Control": "public, max-age=300"})


@router.post("/compliance/tiktok-live/preflight", include_in_schema=False)
def tiktok_live_preflight(payload: TikTokLivePreflight):
    return JSONResponse(evaluate_tiktok_live_preflight(payload), headers={"Cache-Control": "private, no-store"})


@router.get("/compliance", response_class=HTMLResponse, include_in_schema=False)
def compliance_page(request: Request):
    copy = notices(request_locale(request))
    html = f"""<!doctype html><html lang='{escape(copy['locale'])}' dir='{copy['dir']}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='index,follow'><title>{escape(copy['title'])}</title><style>body{{font-family:system-ui,sans-serif;background:#0b0711;color:#fff;margin:0}}main{{max-width:980px;margin:auto;padding:32px 18px}}section{{background:#171020;border:1px solid #ffffff1f;border-radius:16px;padding:18px;margin:14px 0}}a{{color:#f0c56d}}.muted{{color:#c9bfd2}}code{{word-break:break-word}}</style></head><body><main><h1>{escape(copy['title'])}</h1><p class='muted'>Policy baseline {POLICY_VERSION} · continuous review required · not a legal certification.</p><section><h2>Safety</h2><p>{escape(copy['safety'])}</p></section><section><h2>Professional boundaries</h2><p>{escape(copy['professional'])}</p></section><section><h2>AI transparency</h2><p>{escape(copy['ai'])}</p></section><section><h2>TikTok LIVE</h2><p>{escape(copy['tiktok'])}</p></section><section><h2>Translations</h2><p>{escape(copy['translation'])}</p></section><section><h2>Policy evidence</h2><p class='muted'>The machine-readable policy registry and source list are available at <code>/compliance/manifest.json</code>.</p></section></main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=300"})


__all__ = [
    "AUTHORITATIVE_POLICY_LANGUAGE", "POLICY_SOURCES", "POLICY_VERSION", "SUPPORTED_LOCALES",
    "TikTokLivePreflight", "canonical_locale", "evaluate_tiktok_live_preflight", "manifest", "notices", "router",
]
