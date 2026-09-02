from __future__ import annotations

import json
import re

from .game_forge_store import load_game

# These are creator/playtest HTML workspaces that operate on one concrete Game DNA. Keeping the
# allow-list explicit prevents a broad /game-creation/* middleware rule from ever rewriting API,
# gallery, download or unrelated future routes by accident.
_SUBWORKSPACE_RE = re.compile(
    r"^/game-creation/(?:play|export|capture-card|godot-export|state-machines|world-events|adventure|gameplay|world-logic)/(game_[A-Za-z0-9_-]+)$"
)

_SUBWORKSPACE_CLIENT_RE = (
    r"^/game-creation/(?:play|export|capture-card|godot-export|state-machines|world-events|adventure|gameplay|world-logic)/([^/]+)$"
)


def game_id_from_subworkspace_path(path: str) -> str | None:
    match = _SUBWORKSPACE_RE.fullmatch(str(path or ""))
    return match.group(1) if match else None


def bound_project_name_for_game(game_id: str) -> str:
    """Read only the persisted Game DNA project binding.

    This helper deliberately does not call the creator API. Capture-card/private-play surfaces can
    be available under playtest capability even when the current plan cannot create a game, and
    navigation continuity must not turn that legitimate page view into a creator-entitlement check.
    """
    try:
        game = load_game(game_id)
    except (ValueError, FileNotFoundError):
        return ""
    return str((game.metadata or {}).get("creative_project_name") or "").strip()


def project_navigation_script(game_id: str, project_name: str) -> str:
    """Build a bounded client navigation normalizer for one authoritative Game DNA binding."""
    clean_game = str(game_id or "").strip()
    clean_project = str(project_name or "").strip()
    if not clean_game or not clean_project:
        return ""
    game_json = json.dumps(clean_game, ensure_ascii=False).replace("</", "<\\/")
    project_json = json.dumps(clean_project, ensure_ascii=False).replace("</", "<\\/")
    route_json = json.dumps(_SUBWORKSPACE_CLIENT_RE).replace("</", "<\\/")
    return f"""<script data-game-project-continuity='1'>
(()=>{{
  const GAME_ID={game_json},PROJECT_NAME={project_json},SUBWORKSPACE=new RegExp({route_json});
  function normalize(raw){{
    if(!raw||raw.startsWith('#')||raw.startsWith('javascript:'))return raw;
    let url;try{{url=new URL(raw,location.origin)}}catch(_){{return raw}}
    if(url.origin!==location.origin)return raw;
    if(url.pathname==='/game-creation'){{
      url.searchParams.set('project',PROJECT_NAME);
      url.searchParams.set('game',GAME_ID);
      return url.pathname+url.search+url.hash;
    }}
    const match=url.pathname.match(SUBWORKSPACE);
    if(!match||decodeURIComponent(match[1])!==GAME_ID)return raw;
    url.searchParams.set('project',PROJECT_NAME);
    return url.pathname+url.search+url.hash;
  }}
  function rewrite(root=document){{
    root.querySelectorAll?.('a[href]').forEach(link=>{{
      const raw=link.getAttribute('href'),next=normalize(raw);
      if(next&&next!==raw)link.setAttribute('href',next);
    }});
  }}
  const current=new URL(location.href);
  if(current.searchParams.get('project')!==PROJECT_NAME){{
    current.searchParams.set('project',PROJECT_NAME);
    history.replaceState(history.state,'',current.pathname+current.search+current.hash);
  }}
  rewrite();
  const observer=new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{{
    if(node.nodeType!==1)return;
    if(node.matches?.('a[href]')){{const raw=node.getAttribute('href'),next=normalize(raw);if(next&&next!==raw)node.setAttribute('href',next)}}
    rewrite(node);
  }})));
  observer.observe(document.documentElement,{{childList:true,subtree:true}});
  window.GameForgeProjectNavigation={{gameId:GAME_ID,projectName:PROJECT_NAME,normalize,rewrite}};
}})();
</script>"""


__all__ = [
    "bound_project_name_for_game",
    "game_id_from_subworkspace_path",
    "project_navigation_script",
]
