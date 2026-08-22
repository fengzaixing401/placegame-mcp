from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping


OperationName = Literal[
    "login",
    "bootstrap",
    "catalog",
    "idle_summary",
    "view_sections",
    "idle_collect",
    "equipment_list",
    "equipment_decompose_preview",
    "equipment_enhance_preview",
    "equipment_quality_upgrade_preview",
    "boss_preview",
    "boss_challenge",
    "boss_assist",
    "profession_settle",
    "profession_enqueue",
    "profession_supply_equip",
    "daily_claim",
    "quest_claim",
    "achievement_claim",
    "codex_claim",
    "mail_claim",
]


@dataclass(frozen=True)
class EndpointSpec:
    method: Literal["GET", "POST"]
    path: str
    mutation: bool


REGISTRY: Mapping[OperationName, EndpointSpec] = MappingProxyType(
    {
        "login": EndpointSpec("POST", "/api/auth/login", mutation=True),
        "bootstrap": EndpointSpec("GET", "/api/client/bootstrap", mutation=False),
        "catalog": EndpointSpec("GET", "/api/client/catalog", mutation=False),
        "idle_summary": EndpointSpec("GET", "/api/client/idle-summary", mutation=False),
        "view_sections": EndpointSpec("POST", "/api/client/view-sections", mutation=False),
        "idle_collect": EndpointSpec("POST", "/api/client/collect", mutation=True),
        # Equipment reads. Every destructive equipment action has a matching
        # preview, and a preview changes nothing, so these are the safe half.
        "equipment_list": EndpointSpec("GET", "/api/equipment/list", mutation=False),
        "equipment_decompose_preview": EndpointSpec(
            "POST", "/api/equipment/decompose-preview", mutation=False
        ),
        "equipment_enhance_preview": EndpointSpec(
            "POST", "/api/equipment/enhance-preview", mutation=False
        ),
        "equipment_quality_upgrade_preview": EndpointSpec(
            "POST", "/api/equipment/quality-upgrade-preview", mutation=False
        ),
        "boss_preview": EndpointSpec("POST", "/api/boss/preview", mutation=False),
        "boss_challenge": EndpointSpec("POST", "/api/boss/challenge", mutation=True),
        "boss_assist": EndpointSpec("POST", "/api/boss/assist", mutation=True),
        "profession_settle": EndpointSpec("POST", "/api/professions/settle", mutation=True),
        "profession_enqueue": EndpointSpec(
            "POST", "/api/professions/queue/enqueue", mutation=True
        ),
        "profession_supply_equip": EndpointSpec(
            "POST", "/api/professions/supply/equip", mutation=True
        ),
        "daily_claim": EndpointSpec("POST", "/api/daily/claim", mutation=True),
        "quest_claim": EndpointSpec("POST", "/api/quests/claim", mutation=True),
        "achievement_claim": EndpointSpec(
            "POST", "/api/achievements/claim", mutation=True
        ),
        "codex_claim": EndpointSpec("POST", "/api/codex/claim", mutation=True),
        "mail_claim": EndpointSpec("POST", "/api/mail/claim", mutation=True),
    }
)
