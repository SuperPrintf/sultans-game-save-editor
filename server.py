#!/usr/bin/env python3
"""Local-only save editor for Sultan's Game.

The server intentionally uses only Python's standard library.  It binds to
127.0.0.1, validates every save path, creates a backup before each mutation,
and writes JSON atomically.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


APP_VERSION = "1.2.0"
APP_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = APP_ROOT / "static"
BACKUP_ROOT = APP_ROOT / "backups"
DEFAULT_SAVE_BASE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "com.DoubleCross.SultansGame"
    / "SAVEDATA"
)


def card_config_for(game_root: Path) -> Path:
    return (
        game_root
        / "Sultan's Game.app"
        / "Contents"
        / "Resources"
        / "Data"
        / "StreamingAssets"
        / "config"
        / "cards.json"
    )


def discover_game_root() -> Path:
    configured = os.environ.get("SULTANS_GAME_ROOT")
    candidates = [
        Path(configured).expanduser() if configured else None,
        APP_ROOT.parent,
        Path.home()
        / "Library"
        / "Application Support"
        / "Steam"
        / "steamapps"
        / "common"
        / "Sultan's Game",
        Path.home()
        / ".local"
        / "share"
        / "Steam"
        / "steamapps"
        / "common"
        / "Sultan's Game",
    ]
    for candidate in candidates:
        if candidate is not None and card_config_for(candidate).is_file():
            return candidate
    return candidates[2]  # macOS Steam default; produces a useful error message.


GAME_ROOT = discover_game_root()
CARD_CONFIG = card_config_for(GAME_ROOT)

SLANDER_CARD_ID = 2000168
TEASING_RITE_IDS = {5001002, 5001016, 5001018, 5008120}
SAVE_ID_RE = re.compile(
    r"^(?P<account>[A-Za-z0-9_-]+):(?P<rel>auto_save\.json|USERARCHIVE/\d{3}\.json|round_\d+(?:_end)?\.json)$"
)
SAFE_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

PARAMETER_LABELS = {
    "configId": "配置编号",
    "configVersion": "配置版本",
    "name": "存档角色名",
    "difficulty": "难度",
    "round": "当前回合",
    "min_round": "最低回合",
    "saveTime": "存档时间",
    "card_uid_index": "下一卡牌 UID",
    "rite_uid_index": "下一仪式 UID",
    "sudan_box_show": "显示苏丹卡盒",
    "story_unshow": "隐藏故事",
    "prestige_unshow": "隐藏威望",
    "deadline_unshow": "隐藏期限",
    "helpbtn_unshow": "隐藏帮助按钮",
    "location_icon_show": "地点图标状态",
    "change_desk_bg": "桌面背景",
    "after_round_auto_sort": "回合后自动整理",
    "sudan_card_init_life": "苏丹卡初始期限",
    "sudan_redraw_count": "苏丹卡重抽次数",
    "sudan_redraw_times_per_round": "每回合重抽上限",
    "sudan_redraw_times": "当前可重抽次数",
    "sudan_redraw_times_recovery_round": "重抽恢复回合",
    "wizard_first_show": "已显示首次引导",
    "success": "通关状态",
    "over_reason": "结束原因代码",
    "BagIndex": "当前袋索引",
    "rite_auto_result": "仪式自动结算",
    "disable_auto_gen_sudan_card": "禁用自动生成苏丹卡",
    "end_open": "终局开启",
    "is_armageddon": "末日状态",
    "armageddon_rite_id": "末日仪式 ID",
}

COLLECTION_LABELS = {
    "cards": "顶层卡牌实例",
    "rites": "仪式实例",
    "pins": "固定项目",
    "sudan_pool_cards": "苏丹卡池实例",
    "sudan_card_pool": "苏丹卡池",
    "only_cards": "唯一卡记录",
    "only_rites": "唯一仪式记录",
    "event_status": "事件状态",
    "delay_ops": "延迟操作",
    "end_rites": "已结束仪式",
    "gen_cards": "已生成卡牌种类",
    "gen_tags": "已生成标签",
    "timing_rounds": "计时回合记录",
    "notes": "回合笔记",
    "cached_event": "缓存事件",
    "last_round_rite_data": "上一回合仪式数据",
}


class EditorError(Exception):
    """A safe, user-facing error."""


def _strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments without touching string contents."""

    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(text)

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""

        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < length and text[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < length and not (
                text[index] == "*" and text[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue

        output.append(char)
        index += 1

    # The game configuration also contains trailing commas.
    return re.sub(r",(?=\s*[}\]])", "", "".join(output))


def load_relaxed_json(path: Path) -> Any:
    return json.loads(_strip_json_comments(path.read_text(encoding="utf-8-sig")))


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def card_catalog(path: Path = CARD_CONFIG) -> dict[int, dict[str, Any]]:
    if not path.exists():
        raise EditorError(f"找不到卡牌配置：{path}")
    raw = load_relaxed_json(path)
    result: dict[int, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            card_id = int(value.get("id", key))
        except (TypeError, ValueError):
            continue
        result[card_id] = {
            "id": card_id,
            "name": str(value.get("name", f"卡牌 {card_id}")),
            "title": str(value.get("title", "")),
            "text": str(value.get("text", "")),
            "type": str(value.get("type", "")),
            "rare": int(value.get("rare", 0) or 0),
            "tags": list((value.get("tag") or {}).keys()),
            "is_only": bool(value.get("is_only", 0)),
            "stackable": "可堆叠" in (value.get("tag") or {}),
        }
    return result


def save_id_for(account: str, relative: str) -> str:
    return f"{account}:{relative}"


def safe_resolve_save(save_base: Path, save_id: str) -> tuple[str, str, Path]:
    match = SAVE_ID_RE.fullmatch(save_id)
    if not match:
        raise EditorError("无效的存档标识。")
    account = match.group("account")
    relative = match.group("rel")
    account_root = (save_base / account).resolve()
    candidate = (account_root / relative).resolve()
    try:
        candidate.relative_to(account_root)
    except ValueError as exc:
        raise EditorError("存档路径越界。") from exc
    if not candidate.is_file():
        raise EditorError(f"存档不存在：{relative}")
    return account, relative, candidate


def all_card_objects(value: Any):
    if isinstance(value, dict):
        if isinstance(value.get("id"), int) and "count" in value and "uid" in value:
            yield value
        for child in value.values():
            yield from all_card_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_card_objects(child)


def slander_count(data: dict[str, Any]) -> int:
    return sum(
        max(0, int(card.get("count", 1) or 0))
        for card in all_card_objects(data)
        if card.get("id") == SLANDER_CARD_ID
    )


def teasing_count(data: dict[str, Any]) -> int:
    return sum(
        1
        for rite in data.get("rites", [])
        if isinstance(rite, dict) and rite.get("id") in TEASING_RITE_IDS
    )


def save_summary(data: dict[str, Any]) -> dict[str, Any]:
    cards = data.get("cards", [])
    rites = data.get("rites", [])
    return {
        "config_id": data.get("configId"),
        "name": data.get("name", "未命名"),
        "round": data.get("round"),
        "save_time": data.get("saveTime", ""),
        "card_instances": len(cards) if isinstance(cards, list) else 0,
        "hand_instances": sum(
            1 for card in cards if isinstance(card, dict) and card_is_in_inventory(card)
        ) if isinstance(cards, list) else 0,
        "rite_instances": len(rites) if isinstance(rites, list) else 0,
        "event_states": len(data.get("event_status", {}))
        if isinstance(data.get("event_status"), dict)
        else 0,
        "notes": len(data.get("notes", []))
        if isinstance(data.get("notes"), list)
        else 0,
        "slander": slander_count(data),
        "teasing": teasing_count(data),
        "card_uid_index": data.get("card_uid_index"),
    }


def parameter_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string"


def save_parameters(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    values: list[dict[str, Any]] = []
    collections: list[dict[str, Any]] = []
    for key, value in data.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            values.append(
                {
                    "group": "基础参数",
                    "key": key,
                    "label": PARAMETER_LABELS.get(key, key),
                    "value": value,
                    "type": parameter_value_type(value),
                }
            )
        elif isinstance(value, (list, dict)):
            collections.append(
                {
                    "key": key,
                    "label": COLLECTION_LABELS.get(key, key),
                    "kind": "数组" if isinstance(value, list) else "对象",
                    "count": len(value),
                }
            )

    for source_key, group in (
        ("counter", "剧情计数器"),
        ("global_counter_cacher", "全局计数器"),
    ):
        source = data.get(source_key)
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if value is None or isinstance(value, (str, int, float, bool)):
                values.append(
                    {
                        "group": group,
                        "key": f"{source_key}.{key}",
                        "label": str(key),
                        "value": value,
                        "type": parameter_value_type(value),
                    }
                )
    return {"values": values, "collections": collections}


def remove_card_from_equips(cards: list[Any], card_id: int) -> tuple[list[Any], int]:
    removed = 0
    result: list[Any] = []
    for card in cards:
        if isinstance(card, dict) and card.get("id") == card_id:
            removed += max(1, int(card.get("count", 1) or 1))
            continue
        if isinstance(card, dict) and isinstance(card.get("equips"), list):
            card["equips"], child_removed = remove_card_from_equips(
                card["equips"], card_id
            )
            removed += child_removed
        result.append(card)
    return result, removed


def cleanup_slander(data: dict[str, Any]) -> dict[str, int]:
    removed_cards = 0
    inventory = data.get("cards")
    if isinstance(inventory, list):
        data["cards"], count = remove_card_from_equips(inventory, SLANDER_CARD_ID)
        removed_cards += count

    rites = data.get("rites")
    removed_rites = 0
    if isinstance(rites, list):
        filtered_rites: list[Any] = []
        for rite in rites:
            if isinstance(rite, dict) and rite.get("id") in TEASING_RITE_IDS:
                removed_rites += 1
                continue
            if isinstance(rite, dict) and isinstance(rite.get("cards"), list):
                updated_slots: list[Any] = []
                for card in rite["cards"]:
                    if isinstance(card, dict) and card.get("id") == SLANDER_CARD_ID:
                        removed_cards += max(1, int(card.get("count", 1) or 1))
                        updated_slots.append(None)
                    else:
                        updated_slots.append(card)
                rite["cards"] = updated_slots
            filtered_rites.append(rite)
        data["rites"] = filtered_rites

    if isinstance(data.get("ithink_card"), dict) and data["ithink_card"].get(
        "id"
    ) == SLANDER_CARD_ID:
        removed_cards += max(1, int(data["ithink_card"].get("count", 1) or 1))
        data["ithink_card"] = None

    return {"removed_cards": removed_cards, "removed_rites": removed_rites}


def next_card_uid(data: dict[str, Any]) -> int:
    declared = int(data.get("card_uid_index", 1) or 1)
    maximum = max(
        (int(card.get("uid", 0) or 0) for card in all_card_objects(data)),
        default=0,
    )
    return max(declared, maximum + 1)


def card_is_in_inventory(card: dict[str, Any]) -> bool:
    """Whether a top-level card is placed in a visible bag/hand slot."""

    tag = card.get("tag")
    is_discarded_history = isinstance(tag, dict) and tag.get("own") == -1
    try:
        bag_position = int(card.get("bagpos", 0) or 0)
    except (TypeError, ValueError):
        bag_position = 0
    return bag_position > 0 and not is_discarded_history


EQUIPMENT_SLOT_LABELS = {
    "武器": "武器位",
    "服装": "服装位",
    "饰品": "饰品位",
    "动物管理": "驯兽位",
}


def _card_metadata(
    card: dict[str, Any], catalog: dict[int, dict[str, Any]]
) -> tuple[int, dict[str, Any]]:
    card_id = int(card.get("id", 0) or 0)
    return card_id, catalog.get(card_id, {})


def _equipment_slot_label(meta: dict[str, Any], index: int) -> str:
    tags = set(meta.get("tags", []))
    for tag, label in EQUIPMENT_SLOT_LABELS.items():
        if tag in tags:
            return label
    return f"装备栏 {index + 1}"


def save_card_instances(
    data: dict[str, Any],
    catalog: dict[int, dict[str, Any]],
    rite_lookup: Callable[[int], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect live card instances and explain their save-container state.

    Equipped cards and cards used by rites are moved out of the top-level
    ``cards`` array. Historical snapshots under ``notes`` are not live and are
    intentionally excluded.
    """

    instances: list[dict[str, Any]] = []
    seen_uids: set[int] = set()

    def append_instance(
        card: Any,
        source: str,
        relation: str,
        *,
        state_code: str,
        state_label: str,
        state_tone: str,
        extra_flags: list[dict[str, str]] | None = None,
    ) -> None:
        if not isinstance(card, dict):
            return
        try:
            uid = int(card.get("uid"))
        except (TypeError, ValueError):
            return
        if uid in seen_uids:
            return
        seen_uids.add(uid)

        card_id, meta = _card_metadata(card, catalog)
        tag = card.get("tag") if isinstance(card.get("tag"), dict) else {}
        in_inventory = source == "cards" and card_is_in_inventory(card)
        flags = list(extra_flags or [])
        lock_tags = [
            f"{key}={value}"
            for key, value in tag.items()
            if str(key).startswith("lock_") and value
        ]
        if lock_tags:
            flags.append(
                {"code": "locked", "label": "锁定", "detail": "、".join(lock_tags)}
            )
        if tag.get("reading"):
            flags.append({"code": "reading", "label": "阅读中", "detail": "reading=1"})
        if tag.get("own") == -1:
            flags.append({"code": "lost", "label": "已失去", "detail": "own=-1"})
        elif tag.get("own"):
            flags.append(
                {"code": "owned", "label": "持有标记", "detail": f"own={tag['own']}"}
            )
        if tag.get("adsorb_spec"):
            flags.append(
                {
                    "code": "absorbed",
                    "label": "特殊吸附",
                    "detail": f"adsorb_spec={tag['adsorb_spec']}",
                }
            )
        if tag.get("weapon_keep"):
            flags.append(
                {
                    "code": "equipment_kept",
                    "label": "装备保留",
                    "detail": f"weapon_keep={tag['weapon_keep']}",
                }
            )

        if source == "cards":
            if tag.get("own") == -1:
                state_code, state_label, state_tone = "lost", "已失去记录", "danger"
            elif tag.get("reading"):
                state_code, state_label, state_tone = "reading", "阅读中", "blue"
            elif in_inventory:
                state_code, state_label, state_tone = "inventory", "手牌 / 背包", "green"
            elif tag.get("adsorb_spec"):
                state_code, state_label, state_tone = "absorbed", "特殊吸附", "violet"

        bag = card.get("bag", 0)
        bagpos = card.get("bagpos", 0)
        relations = [relation] if relation else []
        if source == "cards" and in_inventory:
            relations.insert(0, f"袋 {bag} · 位 {bagpos}")
        elif source == "cards" and (bag or bagpos):
            relations.append(f"记录位置：袋 {bag} · 位 {bagpos}")
        life = card.get("life", 0)
        if life:
            relations.append(f"期限 / life：{life}")

        instance_tags = [
            {"key": str(key), "value": value}
            for key, value in tag.items()
            if key in {"own", "reading", "adsorb_spec", "weapon_keep", "collected"}
            or str(key).startswith("lock_")
        ]
        editable = source == "cards"
        instances.append(
            {
                "uid": uid,
                "id": card_id,
                "count": card.get("count", 1),
                "life": life,
                "bag": bag,
                "bagpos": bagpos,
                "in_inventory": in_inventory,
                "source": source,
                "editable": editable,
                "can_place": editable and not in_inventory,
                "name": meta.get("name", f"未知卡牌 {card_id}"),
                "title": meta.get("title", ""),
                "text": meta.get("text", ""),
                "type": meta.get("type", ""),
                "rare": meta.get("rare", 0),
                "tags": meta.get("tags", []),
                "instance_tags": instance_tags,
                "stackable": meta.get("stackable", False),
                "is_only": meta.get("is_only", False),
                "state": {
                    "code": state_code,
                    "label": state_label,
                    "tone": state_tone,
                    "flags": flags,
                    "relations": relations,
                },
            }
        )

    def append_equips(equips: Any, owner: dict[str, Any]) -> None:
        if not isinstance(equips, list):
            return
        owner_id, owner_meta = _card_metadata(owner, catalog)
        owner_name = owner_meta.get("name", f"未知卡牌 {owner_id}")
        owner_uid = owner.get("uid", "—")
        for index, equipped in enumerate(equips):
            if not isinstance(equipped, dict):
                continue
            _, equipped_meta = _card_metadata(equipped, catalog)
            slot = _equipment_slot_label(equipped_meta, index)
            append_instance(
                equipped,
                "equipped",
                f"装备于 {owner_name}（UID {owner_uid}） · {slot}",
                state_code="equipped",
                state_label="已装备",
                state_tone="gold",
            )
            append_equips(equipped.get("equips"), equipped)

    top_cards = data.get("cards", [])
    if isinstance(top_cards, list):
        for card in top_cards:
            if not isinstance(card, dict):
                continue
            equipped_count = (
                len([item for item in card.get("equips", []) if isinstance(item, dict)])
                if isinstance(card.get("equips"), list)
                else 0
            )
            owner_flags = (
                [{"code": "equipment_owner", "label": f"装备 {equipped_count} 件", "detail": ""}]
                if equipped_count
                else []
            )
            append_instance(
                card,
                "cards",
                "",
                state_code="registered",
                state_label="顶层登记",
                state_tone="muted",
                extra_flags=owner_flags,
            )
            append_equips(card.get("equips"), card)

    rites = data.get("rites", [])
    if isinstance(rites, list):
        for rite in rites:
            if not isinstance(rite, dict) or not isinstance(rite.get("cards"), list):
                continue
            rite_id = int(rite.get("id", 0) or 0)
            meta = rite_lookup(rite_id) if rite_lookup else {}
            rite_name = meta.get("name", f"仪式 {rite_id}")
            rite_uid = rite.get("uid", "—")
            facts: list[str] = []
            flags: list[dict[str, str]] = []
            if rite.get("start"):
                facts.append("已开始")
                flags.append({"code": "rite_started", "label": "已开始", "detail": "start=true"})
            facts.append("已显示" if rite.get("is_show") else "未显示")
            if rite.get("life"):
                facts.append(f"life={rite['life']}")
            for index, card in enumerate(rite["cards"]):
                append_instance(
                    card,
                    "rite",
                    f"《{rite_name}》 · 槽 {index + 1} · 仪式 UID {rite_uid} · {' / '.join(facts)}",
                    state_code="rite",
                    state_label="事件 / 仪式中",
                    state_tone="violet",
                    extra_flags=flags,
                )
                if isinstance(card, dict):
                    append_equips(card.get("equips"), card)

    sudan_pool = data.get("sudan_card_pool", [])
    if isinstance(sudan_pool, list):
        for index, card in enumerate(sudan_pool):
            pool_index = ""
            if isinstance(card, dict) and isinstance(card.get("tag"), dict):
                pool_index = card["tag"].get("sudan_pool_index", "")
            relation = f"卡池槽 {index + 1}"
            if pool_index != "":
                relation += f" · 池索引 {pool_index}"
            append_instance(
                card,
                "sudan_pool",
                relation,
                state_code="sudan_pool",
                state_label="苏丹卡池",
                state_tone="danger",
            )

    if isinstance(data.get("ithink_card"), dict):
        append_instance(
            data["ithink_card"],
            "ithink_card",
            "当前思考 / 选中卡牌",
            state_code="thinking",
            state_label="思考区",
            state_tone="blue",
        )

    return instances


def next_bag_position(data: dict[str, Any], bag: int = 0) -> int:
    """Allocate the next visible slot, ignoring discarded historical records."""

    positions: list[int] = []
    for card in data.get("cards", []):
        if not isinstance(card, dict) or card.get("bag", 0) != bag:
            continue
        if not card_is_in_inventory(card):
            continue
        try:
            positions.append(int(card.get("bagpos", 0) or 0))
        except (TypeError, ValueError):
            continue
    return max(positions, default=0) + 1


def add_card(
    data: dict[str, Any], card: dict[str, Any], count: int = 1
) -> dict[str, int]:
    if not 1 <= count <= 999:
        raise EditorError("卡牌数量必须在 1 到 999 之间。")
    inventory = data.setdefault("cards", [])
    if not isinstance(inventory, list):
        raise EditorError("存档中的 cards 字段格式异常。")
    card_id = int(card["id"])
    if card.get("is_only") and any(
        isinstance(item, dict) and item.get("id") == card_id for item in inventory
    ):
        raise EditorError("这是一张唯一卡，当前存档已经拥有，未重复添加。")

    uid = next_card_uid(data)
    bag_position = next_bag_position(data)
    inventory.append(
        {
            "uid": uid,
            "id": card_id,
            "count": count,
            "life": 0,
            "rareup": 0,
            "tag": {},
            "equip_slots": [],
            "equips": [],
            "bag": 0,
            "bagpos": bag_position,
            "custom_name": "",
            "custom_text": "",
        }
    )
    data["card_uid_index"] = uid + 1
    generated = data.setdefault("gen_cards", {})
    if isinstance(generated, dict):
        key = str(card_id)
        generated[key] = int(generated.get(key, 0) or 0) + count
    if card.get("is_only"):
        only_cards = data.setdefault("only_cards", [])
        if isinstance(only_cards, list) and card_id not in only_cards:
            only_cards.append(card_id)
    return {"uid": uid, "count": count, "bag": 0, "bagpos": bag_position}


def find_top_level_card(data: dict[str, Any], uid: int) -> dict[str, Any]:
    for card in data.get("cards", []):
        if isinstance(card, dict) and card.get("uid") == uid:
            return card
    raise EditorError(f"找不到 UID 为 {uid} 的卡牌实例。")


def remove_card_uid(data: dict[str, Any], uid: int) -> dict[str, int]:
    inventory = data.get("cards")
    if not isinstance(inventory, list):
        raise EditorError("存档中的 cards 字段格式异常。")
    original = len(inventory)
    data["cards"] = [
        card
        for card in inventory
        if not (isinstance(card, dict) and card.get("uid") == uid)
    ]
    if len(data["cards"]) == original:
        raise EditorError(f"找不到 UID 为 {uid} 的手牌。")
    return {"removed": 1}


def place_card_in_inventory(data: dict[str, Any], uid: int) -> dict[str, int]:
    card = find_top_level_card(data, uid)
    bag_position = next_bag_position(data)
    card["bag"] = 0
    card["bagpos"] = bag_position
    tag = card.get("tag")
    if isinstance(tag, dict) and tag.get("own") == -1:
        tag.pop("own", None)
    return {"uid": uid, "bag": 0, "bagpos": bag_position}


def set_card_count(data: dict[str, Any], uid: int, count: int) -> dict[str, int]:
    if not 1 <= count <= 999:
        raise EditorError("卡牌数量必须在 1 到 999 之间。")
    card = find_top_level_card(data, uid)
    old_count = int(card.get("count", 1) or 1)
    card["count"] = count
    return {"old_count": old_count, "count": count}


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    mode = path.stat().st_mode
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class SaveStore:
    def __init__(
        self,
        save_base: Path = DEFAULT_SAVE_BASE,
        backup_root: Path = BACKUP_ROOT,
        catalog_path: Path = CARD_CONFIG,
    ) -> None:
        self.save_base = save_base
        self.backup_root = backup_root
        self.catalog_path = catalog_path
        self.catalog = card_catalog(catalog_path)
        self.rite_root = catalog_path.parent / "rite"
        self._rite_cache: dict[int, dict[str, Any]] = {}
        self.lock = threading.RLock()

    def _rite_metadata(self, rite_id: int) -> dict[str, Any]:
        if rite_id in self._rite_cache:
            return self._rite_cache[rite_id]
        path = self.rite_root / f"{rite_id}.json"
        result: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = load_relaxed_json(path)
                if isinstance(raw, dict):
                    result = {
                        "id": rite_id,
                        "name": str(raw.get("name", f"仪式 {rite_id}")),
                        "text": str(raw.get("text", "")),
                    }
            except (OSError, json.JSONDecodeError):
                result = {}
        self._rite_cache[rite_id] = result
        return result

    def _archive_labels(self, account_root: Path) -> dict[str, str]:
        labels: dict[str, str] = {}
        index_path = account_root / "user_archive.json"
        if not index_path.exists():
            return labels
        try:
            entries = load_json(index_path)
        except (OSError, json.JSONDecodeError):
            return labels
        if not isinstance(entries, list):
            return labels
        for index, entry in enumerate(entries):
            if isinstance(entry, dict):
                relative = str(entry.get("path", f"USERARCHIVE/{index:03d}.json"))
                labels[relative] = str(entry.get("name", "未命名"))
        return labels

    def scan(self) -> dict[str, Any]:
        accounts: list[dict[str, Any]] = []
        saves: list[dict[str, Any]] = []
        if not self.save_base.exists():
            return {
                "version": APP_VERSION,
                "save_base": str(self.save_base),
                "accounts": [],
                "saves": [],
                "warning": "尚未找到游戏存档目录。请先运行一次游戏。",
            }

        for account_root in sorted(path for path in self.save_base.iterdir() if path.is_dir()):
            account = account_root.name
            labels = self._archive_labels(account_root)
            account_saves: list[dict[str, Any]] = []
            candidates: list[tuple[int, Path, str, str]] = []
            auto_path = account_root / "auto_save.json"
            if auto_path.exists():
                candidates.append((0, auto_path, "自动存档", "auto"))
            archive_root = account_root / "USERARCHIVE"
            if archive_root.exists():
                for path in sorted(archive_root.glob("[0-9][0-9][0-9].json")):
                    relative = path.relative_to(account_root).as_posix()
                    display_slot = int(path.stem) + 1
                    slot_name = labels.get(relative, "未命名")
                    candidates.append(
                        (
                            100 + display_slot,
                            path,
                            f"槽位 {display_slot:03d} · {slot_name}",
                            "archive",
                        )
                    )
            round_paths = sorted(
                account_root.glob("round_*.json"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )[:4]
            for order, path in enumerate(round_paths):
                candidates.append((1000 + order, path, f"回合快照 · {path.stem}", "round"))

            for order, path, label, kind in sorted(candidates, key=lambda item: item[0]):
                relative = path.relative_to(account_root).as_posix()
                try:
                    data = load_json(path)
                    summary = save_summary(data)
                    error = None
                except (OSError, json.JSONDecodeError) as exc:
                    summary = {}
                    error = str(exc)
                entry = {
                    "id": save_id_for(account, relative),
                    "account": account,
                    "relative": relative,
                    "label": label,
                    "kind": kind,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    "summary": summary,
                    "error": error,
                }
                account_saves.append(entry)
                saves.append(entry)
            accounts.append({"id": account, "saves": len(account_saves)})

        return {
            "version": APP_VERSION,
            "save_base": str(self.save_base),
            "accounts": accounts,
            "saves": saves,
            "warning": None,
        }

    def get_save(self, save_id: str) -> dict[str, Any]:
        account, relative, path = safe_resolve_save(self.save_base, save_id)
        data = load_json(path)
        inventory = save_card_instances(data, self.catalog, self._rite_metadata)
        summary = save_summary(data)
        summary["card_instances"] = len(inventory)
        summary["equipped_instances"] = sum(
            1 for card in inventory if card["state"]["code"] == "equipped"
        )
        summary["rite_card_instances"] = sum(
            1 for card in inventory if card["state"]["code"] == "rite"
        )
        return {
            "id": save_id,
            "account": account,
            "relative": relative,
            "path": str(path),
            "summary": summary,
            "cards": inventory,
            "parameters": save_parameters(data),
        }

    def search_catalog(
        self, query: str = "", rare: int | None = None, card_type: str = "", limit: int = 500
    ) -> list[dict[str, Any]]:
        query_folded = query.strip().casefold()
        matches: list[dict[str, Any]] = []
        for card in self.catalog.values():
            haystack = f"{card['id']} {card['name']} {card['title']} {' '.join(card['tags'])}".casefold()
            if query_folded and query_folded not in haystack:
                continue
            if rare is not None and card["rare"] != rare:
                continue
            if card_type and card["type"] != card_type:
                continue
            matches.append(card)
        matches.sort(key=lambda item: (-item["rare"], item["type"], item["id"]))
        return matches[: max(1, min(limit, 2000))]

    def _linked_targets(self, selected: Path, account_root: Path) -> list[Path]:
        data = load_json(selected)
        round_value = data.get("round")
        relative = selected.relative_to(account_root).as_posix()
        targets = [selected]
        linked_current = relative == "auto_save.json" or relative == "USERARCHIVE/000.json"
        if linked_current:
            for candidate in (
                account_root / "auto_save.json",
                account_root / "USERARCHIVE" / "000.json",
                account_root / f"round_{round_value}.json",
            ):
                if candidate.is_file() and candidate not in targets:
                    targets.append(candidate)
        return targets

    def _backup_bundle(self, account: str, paths: list[Path], account_root: Path) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        bundle = self.backup_root / stamp
        for path in paths:
            relative = path.relative_to(account_root)
            destination = bundle / account / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        return stamp

    def mutate(
        self,
        save_id: str,
        sync_linked: bool,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self.lock:
            account, _, selected = safe_resolve_save(self.save_base, save_id)
            account_root = (self.save_base / account).resolve()
            targets = (
                self._linked_targets(selected, account_root) if sync_linked else [selected]
            )
            # Read and transform every target before writing any of them.
            transformed: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
            for path in targets:
                data = load_json(path)
                details = operation(data)
                transformed.append((path, data, details))
            backup_id = self._backup_bundle(account, targets, account_root)
            for path, data, _ in transformed:
                atomic_write_json(path, data)
            return {
                "backup_id": backup_id,
                "targets": [path.relative_to(account_root).as_posix() for path in targets],
                "results": [details for _, _, details in transformed],
                "save": self.get_save(save_id),
            }

    def backups(self, save_id: str) -> list[dict[str, Any]]:
        account, relative, _ = safe_resolve_save(self.save_base, save_id)
        entries: list[dict[str, Any]] = []
        if not self.backup_root.exists():
            return entries
        for bundle in sorted(self.backup_root.iterdir(), reverse=True):
            path = bundle / account / relative
            if not path.is_file():
                continue
            try:
                summary = save_summary(load_json(path))
            except (OSError, json.JSONDecodeError):
                summary = {}
            entries.append(
                {
                    "id": bundle.name,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                    "summary": summary,
                }
            )
        return entries[:50]

    def restore(self, save_id: str, backup_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"\d{8}-\d{6}-\d{6}", backup_id):
            raise EditorError("无效的备份标识。")
        with self.lock:
            account, relative, target = safe_resolve_save(self.save_base, save_id)
            source = self.backup_root / backup_id / account / relative
            if not source.is_file():
                raise EditorError("找不到这份备份。")
            account_root = (self.save_base / account).resolve()
            safety_id = self._backup_bundle(account, [target], account_root)
            data = load_json(source)
            atomic_write_json(target, data)
            return {
                "backup_id": safety_id,
                "restored_from": backup_id,
                "save": self.get_save(save_id),
            }


class EditorHandler(BaseHTTPRequestHandler):
    server_version = "SultansGameEditor/1.0"

    @property
    def store(self) -> SaveStore:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "").rsplit(":", 1)[0]
        return host in SAFE_HOSTS

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urllib.parse.urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise EditorError("请求长度无效。") from exc
        if length <= 0 or length > 1_000_000:
            raise EditorError("请求内容为空或过大。")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EditorError("请求 JSON 无效。") from exc
        if not isinstance(payload, dict):
            raise EditorError("请求格式无效。")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._error("不允许的 Host。", HTTPStatus.FORBIDDEN)
            return
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._json({"ok": True, "version": APP_VERSION})
                return
            if parsed.path == "/api/state":
                self._json({"ok": True, **self.store.scan()})
                return
            if parsed.path == "/api/save":
                params = urllib.parse.parse_qs(parsed.query)
                self._json({"ok": True, "save": self.store.get_save(params.get("id", [""])[0])})
                return
            if parsed.path == "/api/catalog":
                params = urllib.parse.parse_qs(parsed.query)
                rare_text = params.get("rare", [""])[0]
                rare = int(rare_text) if rare_text else None
                cards = self.store.search_catalog(
                    query=params.get("q", [""])[0],
                    rare=rare,
                    card_type=params.get("type", [""])[0],
                    limit=int(params.get("limit", ["500"])[0]),
                )
                self._json({"ok": True, "cards": cards})
                return
            if parsed.path == "/api/backups":
                params = urllib.parse.parse_qs(parsed.query)
                self._json(
                    {"ok": True, "backups": self.store.backups(params.get("id", [""])[0])}
                )
                return
            self._serve_static(parsed.path)
        except EditorError as exc:
            self._error(str(exc))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self._error(f"读取失败：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_allowed() or not self._origin_allowed():
            self._error("请求来源不被允许。", HTTPStatus.FORBIDDEN)
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/action":
            self._error("接口不存在。", HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            action = str(payload.get("action", ""))
            save_id = str(payload.get("save_id", ""))
            sync_linked = bool(payload.get("sync_linked", False))

            if action == "cleanup_slander":
                result = self.store.mutate(save_id, sync_linked, cleanup_slander)
            elif action == "add_card":
                card_id = int(payload.get("card_id"))
                count = int(payload.get("count", 1))
                card = self.store.catalog.get(card_id)
                if not card:
                    raise EditorError(f"卡牌 ID {card_id} 不存在。")
                result = self.store.mutate(
                    save_id,
                    sync_linked,
                    lambda data: add_card(data, card, count),
                )
            elif action == "remove_card":
                uid = int(payload.get("uid"))
                result = self.store.mutate(
                    save_id,
                    sync_linked,
                    lambda data: remove_card_uid(data, uid),
                )
            elif action == "place_card":
                uid = int(payload.get("uid"))
                result = self.store.mutate(
                    save_id,
                    sync_linked,
                    lambda data: place_card_in_inventory(data, uid),
                )
            elif action == "set_card_count":
                uid = int(payload.get("uid"))
                count = int(payload.get("count"))
                result = self.store.mutate(
                    save_id,
                    sync_linked,
                    lambda data: set_card_count(data, uid, count),
                )
            elif action == "restore_backup":
                result = self.store.restore(save_id, str(payload.get("backup_id", "")))
            else:
                raise EditorError("未知操作。")
            self._json({"ok": True, **result})
        except EditorError as exc:
            self._error(str(exc))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._error(f"修改失败：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._error("静态文件路径越界。", HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self._error("页面不存在。", HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: SaveStore):
        super().__init__(address, EditorHandler)
        self.store = store


def main() -> None:
    parser = argparse.ArgumentParser(description="苏丹的游戏 · 本地存档修改器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后打开默认浏览器")
    parser.add_argument("--save-base", type=Path, default=DEFAULT_SAVE_BASE)
    parser.add_argument(
        "--game-root",
        type=Path,
        default=GAME_ROOT,
        help="游戏安装目录；默认自动查找 Steam 安装",
    )
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("安全起见，只允许监听本机回环地址。")

    store = SaveStore(
        save_base=args.save_base.expanduser(),
        catalog_path=card_config_for(args.game_root.expanduser()),
    )
    server = EditorServer((args.host, args.port), store)
    url = f"http://127.0.0.1:{server.server_port}/"
    print("苏丹的游戏 · 本地存档修改器")
    print(f"访问地址：{url}")
    print("按 Ctrl+C 停止服务。")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
