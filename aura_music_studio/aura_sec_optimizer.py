from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CandidateKind(str, Enum):
    OS_TEMPORARY = "os_temporary"
    APP_CACHE = "app_cache"
    DUPLICATE_FILE = "duplicate_file"
    LARGE_FILE = "large_file"
    STALE_DOWNLOAD = "stale_download"
    SCREENSHOT = "screenshot"
    UNUSED_APP = "unused_app"
    STARTUP_ITEM = "startup_item"
    CLOUD_OFFLOAD = "cloud_offload"
    PROJECT_ASSET = "project_asset"
    USER_DOCUMENT = "user_document"
    PROTECTED_SYSTEM = "protected_system"


class OptimizerAction(str, Enum):
    DELETE_OS_TEMP = "delete_os_temp"
    CLEAR_APP_CACHE = "clear_app_cache"
    MOVE_TO_TRASH = "move_to_trash"
    ARCHIVE = "archive"
    CLOUD_OFFLOAD = "cloud_offload"
    UNINSTALL_APP = "uninstall_app"
    DISABLE_STARTUP = "disable_startup"
    ENABLE_STARTUP = "enable_startup"
    UPDATE_APP = "update_app"
    KEEP = "keep"


class OptimizerCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    kind: CandidateKind
    display_name: str = Field(min_length=1, max_length=240)
    size_bytes: int = Field(ge=0, le=100 * 1024 * 1024 * 1024 * 1024)
    last_used_days: int | None = Field(default=None, ge=0, le=36500)
    duplicate_group: str | None = Field(default=None, max_length=128)
    active_project_reference: bool = False
    security_component: bool = False
    backup_component: bool = False
    accessibility_component: bool = False
    os_declared_safe_temp: bool = False
    protected_location: bool = False
    reversible_move_available: bool = False


class OptimizerProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate: OptimizerCandidate
    action: OptimizerAction
    reason: str = Field(min_length=3, max_length=1000)
    estimated_reclaim_bytes: int = Field(ge=0)
    requires_confirmation: bool
    reversible: bool
    recovery_note: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def enforce_safety(self):
        c = self.candidate
        destructive_or_state_change = self.action in {
            OptimizerAction.CLEAR_APP_CACHE,
            OptimizerAction.MOVE_TO_TRASH,
            OptimizerAction.ARCHIVE,
            OptimizerAction.CLOUD_OFFLOAD,
            OptimizerAction.UNINSTALL_APP,
            OptimizerAction.DISABLE_STARTUP,
            OptimizerAction.ENABLE_STARTUP,
            OptimizerAction.UPDATE_APP,
        }

        if c.kind == CandidateKind.PROTECTED_SYSTEM or c.protected_location:
            if self.action is not OptimizerAction.KEEP:
                raise ValueError("protected system locations cannot be changed by Aura optimisation")

        if c.active_project_reference and self.action in {
            OptimizerAction.MOVE_TO_TRASH,
            OptimizerAction.ARCHIVE,
            OptimizerAction.CLOUD_OFFLOAD,
        }:
            raise ValueError("active project assets cannot be moved by an optimiser proposal")

        if (c.security_component or c.backup_component or c.accessibility_component) and self.action in {
            OptimizerAction.UNINSTALL_APP,
            OptimizerAction.DISABLE_STARTUP,
        }:
            raise ValueError("security, backup and accessibility components cannot be disabled for optimisation")

        if self.action is OptimizerAction.DELETE_OS_TEMP:
            if c.kind is not CandidateKind.OS_TEMPORARY or not c.os_declared_safe_temp:
                raise ValueError("automatic temp deletion requires OS-declared safe temporary data")
            if self.requires_confirmation:
                raise ValueError("OS-declared safe temp deletion is the only low-risk delete operation")

        if c.kind in {
            CandidateKind.USER_DOCUMENT,
            CandidateKind.PROJECT_ASSET,
            CandidateKind.DUPLICATE_FILE,
            CandidateKind.LARGE_FILE,
            CandidateKind.STALE_DOWNLOAD,
            CandidateKind.SCREENSHOT,
        }:
            if self.action not in {
                OptimizerAction.KEEP,
                OptimizerAction.MOVE_TO_TRASH,
                OptimizerAction.ARCHIVE,
                OptimizerAction.CLOUD_OFFLOAD,
            }:
                raise ValueError("personal/member files cannot receive a permanent-delete or app-state action")
            if self.action is not OptimizerAction.KEEP and not self.requires_confirmation:
                raise ValueError("member file changes always require confirmation")

        if destructive_or_state_change and self.action is not OptimizerAction.DELETE_OS_TEMP and not self.requires_confirmation:
            raise ValueError("device/file state changes require member confirmation")

        if self.estimated_reclaim_bytes > c.size_bytes and self.action not in {
            OptimizerAction.DISABLE_STARTUP,
            OptimizerAction.ENABLE_STARTUP,
            OptimizerAction.UPDATE_APP,
        }:
            raise ValueError("estimated reclaimed storage cannot exceed candidate size")

        if self.reversible and not self.recovery_note:
            raise ValueError("reversible proposal must describe its recovery path")
        if self.action is OptimizerAction.MOVE_TO_TRASH and not c.reversible_move_available:
            raise ValueError("move-to-trash proposal requires an available reversible trash mechanism")
        return self


class OptimizationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    device_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    scan_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    proposals: list[OptimizerProposal] = Field(default_factory=list, max_length=10000)
    member_documents_permanently_deleted: bool = False
    registry_cleaner_used: bool = False

    @model_validator(mode="after")
    def global_guards(self):
        if self.member_documents_permanently_deleted:
            raise ValueError("Aura Sec optimiser never permanently deletes member documents")
        if self.registry_cleaner_used:
            raise ValueError("Aura Sec does not use registry-cleaner optimisation")
        return self

    @property
    def estimated_reclaim_bytes(self) -> int:
        return sum(item.estimated_reclaim_bytes for item in self.proposals)

    @property
    def pending_confirmations(self) -> int:
        return sum(1 for item in self.proposals if item.requires_confirmation and item.action is not OptimizerAction.KEEP)


__all__ = [
    "CandidateKind",
    "OptimizationPlan",
    "OptimizerAction",
    "OptimizerCandidate",
    "OptimizerProposal",
]
