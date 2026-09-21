import copy
import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import (  # noqa: E402
    EditorError,
    SaveStore,
    add_card,
    cleanup_slander,
    load_relaxed_json,
    next_bag_position,
    next_card_uid,
    place_card_in_inventory,
    remove_card_uid,
    save_summary,
    set_card_count,
)


class SaveMutationTests(unittest.TestCase):
    def setUp(self):
        self.sample = {
            "configId": 1,
            "name": "测试存档",
            "round": 12,
            "saveTime": "2026-01-01T00:00:00+08:00",
            "card_uid_index": 11,
            "cards": [
                {
                    "uid": 10,
                    "id": 123,
                    "count": 2,
                    "life": 0,
                    "equips": [],
                    "bag": 0,
                    "bagpos": 1,
                }
            ],
            "rites": [],
            "gen_cards": {"123": 2},
            "only_cards": [],
        }

    def test_add_card_uses_next_uid_and_updates_history(self):
        data = copy.deepcopy(self.sample)
        result = add_card(
            data,
            {
                "id": 456,
                "name": "黄金饰品",
                "is_only": False,
            },
            3,
        )
        self.assertEqual(result, {"uid": 11, "count": 3, "bag": 0, "bagpos": 2})
        self.assertEqual(data["card_uid_index"], 12)
        self.assertEqual(data["cards"][-1]["id"], 456)
        self.assertEqual(data["cards"][-1]["count"], 3)
        self.assertEqual(data["cards"][-1]["bagpos"], 2)
        self.assertEqual(data["gen_cards"]["456"], 3)

    def test_next_bag_position_ignores_hidden_and_discarded_history(self):
        data = copy.deepcopy(self.sample)
        data["cards"].extend(
            [
                {"uid": 20, "id": 1, "count": 1, "bag": 0, "bagpos": 0, "tag": {}},
                {"uid": 21, "id": 2, "count": 1, "bag": 0, "bagpos": 99, "tag": {"own": -1}},
                {"uid": 22, "id": 3, "count": 1, "bag": 0, "bagpos": 4, "tag": {}},
                {"uid": 23, "id": 4, "count": 1, "bag": 1, "bagpos": 8, "tag": {}},
            ]
        )
        self.assertEqual(next_bag_position(data), 5)

    def test_next_uid_never_collides_with_existing_cards(self):
        data = copy.deepcopy(self.sample)
        data["card_uid_index"] = 5
        self.assertEqual(next_card_uid(data), 11)

    def test_cleanup_preserves_slot_shape(self):
        data = copy.deepcopy(self.sample)
        data["cards"].append({"uid": 20, "id": 2000168, "count": 2, "equips": []})
        data["rites"] = [
            {
                "uid": 30,
                "id": 5001001,
                "cards": [{"uid": 21, "id": 2000168, "count": 4}, None, {"uid": 22, "id": 9, "count": 1}],
            },
            {"uid": 31, "id": 5001016, "cards": []},
        ]
        result = cleanup_slander(data)
        self.assertEqual(result, {"removed_cards": 6, "removed_rites": 1})
        self.assertEqual(len(data["rites"][0]["cards"]), 3)
        self.assertIsNone(data["rites"][0]["cards"][0])
        self.assertEqual(save_summary(data)["slander"], 0)
        self.assertEqual(save_summary(data)["teasing"], 0)

    def test_count_and_remove(self):
        data = copy.deepcopy(self.sample)
        set_card_count(data, 10, 9)
        self.assertEqual(data["cards"][0]["count"], 9)
        remove_card_uid(data, 10)
        self.assertEqual(data["cards"], [])

    def test_place_hidden_card_in_inventory(self):
        data = copy.deepcopy(self.sample)
        data["cards"].append(
            {"uid": 20, "id": 456, "count": 1, "bag": 0, "bagpos": 0, "tag": {}}
        )
        result = place_card_in_inventory(data, 20)
        self.assertEqual(result, {"uid": 20, "bag": 0, "bagpos": 2})
        self.assertEqual(data["cards"][-1]["bagpos"], 2)

    def test_unique_card_cannot_be_duplicated(self):
        data = copy.deepcopy(self.sample)
        with self.assertRaises(EditorError):
            add_card(data, {"id": 123, "name": "唯一卡", "is_only": True}, 1)


class RelaxedJsonTests(unittest.TestCase):
    def test_comments_and_trailing_commas(self):
        payload = '''{
          // line comment
          "url": "https://example.com/a//b",
          "items": [1, 2,],
          /* block comment */
        }'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(payload, encoding="utf-8")
            parsed = load_relaxed_json(path)
        self.assertEqual(parsed["url"], "https://example.com/a//b")
        self.assertEqual(parsed["items"], [1, 2])


class StoreIntegrationTests(unittest.TestCase):
    def test_linked_write_creates_backup_and_updates_all_current_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_base = root / "saves"
            account = save_base / "123456"
            (account / "USERARCHIVE").mkdir(parents=True)
            backup_root = root / "backups"
            catalog_path = root / "cards.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "2000818": {
                            "id": 2000818,
                            "name": "黄金角杯",
                            "title": "饰品",
                            "text": "测试",
                            "type": "item",
                            "rare": 4,
                            "tag": {"饰品": 1},
                            "is_only": 0,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            base_save = {
                "configId": 1,
                "name": "测试",
                "round": 9,
                "saveTime": "2026-01-01T00:00:00+08:00",
                "card_uid_index": 1,
                "cards": [],
                "rites": [],
                "gen_cards": {},
                "only_cards": [],
            }
            targets = [
                account / "auto_save.json",
                account / "USERARCHIVE" / "000.json",
                account / "round_9.json",
            ]
            for path in targets:
                path.write_text(json.dumps(base_save), encoding="utf-8")

            store = SaveStore(save_base, backup_root, catalog_path)
            result = store.mutate(
                "123456:auto_save.json",
                True,
                lambda data: add_card(data, store.catalog[2000818], 1),
            )
            self.assertEqual(len(result["targets"]), 3)
            for path in targets:
                written = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(written["cards"][0]["id"], 2000818)
                backup = backup_root / result["backup_id"] / "123456" / path.relative_to(account)
                self.assertTrue(backup.is_file())
                original = json.loads(backup.read_text(encoding="utf-8"))
                self.assertEqual(original["cards"], [])


if __name__ == "__main__":
    unittest.main()
