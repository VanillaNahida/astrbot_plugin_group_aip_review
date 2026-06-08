from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger

from .group_info_cache import GroupInfoCache

DEFAULT_GROUP_ID = "__default__"


class AuditPageService:
    def __init__(self, config: AstrBotConfig, plugin_dir: Path, group_cache: GroupInfoCache):
        self.config = config
        self.plugin_dir = plugin_dir
        self.group_cache = group_cache
        self.schema = self._load_schema()

    def _load_schema(self) -> dict[str, Any]:
        schema_path = self.plugin_dir / "_conf_schema.json"
        try:
            return json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"Failed to load _conf_schema.json: {exc}")
            return {}

    @property
    def default_schema(self) -> dict[str, Any]:
        disposal = self.schema.get("disposal", {})
        return disposal.get("items", {}).get("default", {}).get("items", {})

    @property
    def templates(self) -> dict[str, Any]:
        disposal = self.schema.get("disposal", {})
        group_custom = disposal.get("items", {}).get("group_custom", {})
        return group_custom.get("templates", {})

    @property
    def global_schema(self) -> dict[str, Any]:
        """Top-level schema items that are not inside disposal (baidu_audit etc.)."""
        result = {}
        for key in ("baidu_audit",):
            if key in self.schema:
                result[key] = self.schema[key]
        return result

    # ── bootstrap ──

    async def get_bootstrap(self) -> dict[str, Any]:
        groups = [self._build_default_entry()]
        live_groups = await self.group_cache.list_groups()

        for cfg in self._get_group_configs():
            group_id = cfg.get("group_id", "")
            live_info = self._find_live_group(live_groups, group_id)
            groups.append(self._build_group_entry(cfg, live_info))

        return {
            "schema": {
                "default": self.default_schema,
                "templates": self.templates,
                "global": self.global_schema,
            },
            "groups": groups,
            "enabled_groups": self.config.get("enabled_groups", []),
            "global_config": self._get_global_config(),
        }

    ## available groups (for add-group picker)

    async def get_available_groups(self) -> list[dict[str, Any]]:
        """Return QQ groups that do not yet have a custom config entry."""
        live_groups = await self.group_cache.list_groups()
        existing_ids = {cfg.get("group_id") for cfg in self._get_group_configs()}
        available = []
        for g in live_groups:
            gid = g.get("group_id", "")
            if gid and gid not in existing_ids:
                available.append(g)
        return available

    # ── config read ──

    def _get_default_config(self) -> dict[str, Any]:
        disposal = self.config.get("disposal", {})
        return copy.deepcopy(disposal.get("default", {}))

    def _get_group_configs(self) -> list[dict[str, Any]]:
        disposal = self.config.get("disposal", {})
        return copy.deepcopy(disposal.get("group_custom", []))

    def _get_global_config(self) -> dict[str, Any]:
        """Read top-level config items (baidu_audit, enable_text_censor, etc.)."""
        result = {}
        for key in ("baidu_audit", "enable_text_censor", "enable_image_censor"):
            if key in self.config:
                result[key] = copy.deepcopy(self.config[key])
        return result

    # ── config write ──

    def _save_config(self, disposal: dict[str, Any]) -> None:
        self.config["disposal"] = disposal
        self.config.save_config()

    def _save_default_config(self, data: dict[str, Any]) -> dict[str, Any]:
        disposal = self.config.get("disposal", {})
        disposal["default"] = data
        self._save_config(disposal)
        return self._build_default_entry()

    def _save_custom_group_config(self, group_id: str, data: dict[str, Any]) -> dict[str, Any]:
        group_configs = self._get_group_configs()

        found = False
        for cfg in group_configs:
            if cfg.get("group_id") == group_id:
                template_key = cfg.get("__template_key", "default_group_config")
                cfg.clear()
                cfg.update(data)
                cfg["group_id"] = group_id
                cfg["__template_key"] = template_key
                found = True
                break

        if not found:
            data["group_id"] = group_id
            data["__template_key"] = "default_group_config"
            group_configs.append(data)

        self._save_group_configs(group_configs)

        # Add to enabled_groups
        self._add_to_enabled_groups(group_id)

        for cfg in group_configs:
            if cfg.get("group_id") == group_id:
                return self._build_group_entry(cfg)

        return self._build_group_entry({"group_id": group_id, **data})

    def _save_group_configs(self, group_configs: list[dict[str, Any]]) -> None:
        disposal = self.config.get("disposal", {})
        disposal["group_custom"] = group_configs
        self._save_config(disposal)

    # ── single group CRUD ──

    async def get_group_config(self, group_id: str) -> dict[str, Any]:
        if group_id == DEFAULT_GROUP_ID:
            return self._build_default_group_detail()

        live_info = None
        try:
            live_info = await self.group_cache.get_group(group_id)
        except Exception:
            pass

        group_configs = self._get_group_configs()
        for cfg in group_configs:
            if cfg.get("group_id") == group_id:
                return self._build_group_detail(cfg, live_info)

        raise ValueError(f"Group config not found: {group_id}")

    def save_group_config(self, group_id: str, data: dict[str, Any], global_config: dict[str, Any] | None = None) -> dict[str, Any]:
        if group_id == DEFAULT_GROUP_ID:
            # Sync top-level enable_text_censor / enable_image_censor
            if "enable_text_censor" in data:
                self.config["enable_text_censor"] = data["enable_text_censor"]
            if "enable_image_censor" in data:
                self.config["enable_image_censor"] = data["enable_image_censor"]
            # Save global config (baidu_audit etc.)
            if global_config:
                self._save_global_config(global_config)
            return self._save_default_config(data)

        return self._save_custom_group_config(group_id, data)

    def _save_global_config(self, global_config: dict[str, Any]) -> None:
        """Save top-level config items (baidu_audit etc.)."""
        for key, value in global_config.items():
            self.config[key] = value
        self.config.save_config()

    def delete_group_config(self, group_id: str) -> None:
        group_configs = self._get_group_configs()
        group_configs = [c for c in group_configs if c.get("group_id") != group_id]
        self._save_group_configs(group_configs)
        self._remove_from_enabled_groups(group_id)

    # ── entry builders (list) ──

    def _build_default_entry(self) -> dict[str, Any]:
        return {
            "group_id": DEFAULT_GROUP_ID,
            "group_name": "默认全局配置",
            "avatar": "",
            "is_default_group": True,
            "template_key": "",
            "template_name": "",
            "config": self._get_default_config(),
        }

    def _build_group_entry(
        self,
        cfg: dict[str, Any],
        live_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        group_id = cfg.get("group_id", "")
        template_key = cfg.get("__template_key", "")
        template_name = ""
        if template_key and template_key in self.templates:
            template_name = self.templates[template_key].get("name", template_key)

        # Use live group info for name and avatar
        group_name = f"群 {group_id}"
        avatar = GroupInfoCache._build_avatar(group_id) if group_id else ""
        member_count = 0

        if live_info:
            group_name = live_info.get("group_name", group_name)
            avatar = live_info.get("avatar", avatar)
            member_count = live_info.get("member_count", 0)

        return {
            "group_id": group_id,
            "group_name": group_name,
            "avatar": avatar,
            "member_count": member_count,
            "is_default_group": False,
            "template_key": template_key,
            "template_name": template_name,
            "config": copy.deepcopy(cfg),
        }

    # ── entry builders (detail) ──

    def _build_default_group_detail(self) -> dict[str, Any]:
        return {
            "group_id": DEFAULT_GROUP_ID,
            "group_info": {
                "group_id": DEFAULT_GROUP_ID,
                "group_name": "默认全局配置",
                "avatar": "",
                "member_count": 0,
            },
            "is_default_group": True,
            "config": self._get_default_config(),
        }

    def _build_group_detail(
        self,
        cfg: dict[str, Any],
        live_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        group_id = cfg.get("group_id", "")

        group_name = f"群 {group_id}"
        avatar = GroupInfoCache._build_avatar(group_id) if group_id else ""
        member_count = 0

        if live_info:
            group_name = live_info.get("group_name", group_name)
            avatar = live_info.get("avatar", avatar)
            member_count = live_info.get("member_count", 0)

        return {
            "group_id": group_id,
            "group_info": {
                "group_id": group_id,
                "group_name": group_name,
                "avatar": avatar,
                "member_count": member_count,
            },
            "is_default_group": False,
            "config": copy.deepcopy(cfg),
        }

    # ── helpers ──

    def _add_to_enabled_groups(self, group_id: str) -> None:
        enabled: list = list(self.config.get("enabled_groups", []))
        if group_id not in enabled:
            enabled.append(group_id)
            self.config["enabled_groups"] = enabled
            self.config.save_config()

    def _remove_from_enabled_groups(self, group_id: str) -> None:
        enabled: list = list(self.config.get("enabled_groups", []))
        if group_id in enabled:
            enabled.remove(group_id)
            self.config["enabled_groups"] = enabled
            self.config.save_config()

    @staticmethod
    def _find_live_group(
        live_groups: list[dict[str, Any]], group_id: str
    ) -> dict[str, Any] | None:
        for g in live_groups:
            if g.get("group_id") == group_id:
                return g
        return None
