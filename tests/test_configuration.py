"""Kimi-family spec tests for bridge/configuration.py store lifecycle."""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"))
import configuration


MINIMAL_RESOURCES = {
    "agents": {
        "kimi-code": {"status": "supported"},
        "zcode": {"status": "planned"},
        "claude-code": {"status": "planned"},
    },
    "providers": {
        "kimi-code": {"id": "kimi-code", "model_aliases": ["kimi-code/k3-256k"]},
    },
    "models": {
        "kimi-code/k3-256k": {
            "alias": "kimi-code/k3-256k",
            "provider_id": "kimi-code",
            "agent_type": "kimi-code",
            "family": "kimi",
            "context": 256000,
            "capabilities": ["image_in"],
        }
    },
}


MULTI_PROVIDER_RESOURCES = {
    "agents": {
        "kimi-code": {"status": "supported"},
        "zcode": {"status": "planned"},
        "claude-code": {"status": "planned"},
    },
    "providers": {
        "kimi-code": {"id": "kimi-code", "model_aliases": ["kimi-code/k3-256k", "kimi-code/kimi-for-coding"]},
        "zai-coding-plan": {"id": "zai-coding-plan", "model_aliases": ["zai-coding-plan/glm-5.3"]},
    },
    "models": {
        "kimi-code/k3-256k": {
            "alias": "kimi-code/k3-256k",
            "provider_id": "kimi-code",
            "agent_type": "kimi-code",
            "family": "kimi",
            "context": 256000,
            "capabilities": ["image_in"],
        },
        "kimi-code/kimi-for-coding": {
            "alias": "kimi-code/kimi-for-coding",
            "provider_id": "kimi-code",
            "agent_type": "kimi-code",
            "family": "kimi",
            "context": 8192,
            "capabilities": [],
        },
        "zai-coding-plan/glm-5.3": {
            "alias": "zai-coding-plan/glm-5.3",
            "provider_id": "zai-coding-plan",
            "agent_type": "kimi-code",
            "family": "glm",
            "context": 128000,
            "capabilities": [],
        },
    },
}


def sample_payload(models):
    providers = {}
    for alias in models:
        record = MULTI_PROVIDER_RESOURCES["models"][alias]
        providers.setdefault(record["provider_id"], []).append(alias)
    return {
        "schema_version": 1,
        "agent_configs": [
            {
                "agent_type": "kimi-code",
                "enabled": True,
                "providers": [
                    {"provider_id": pid, "models": mlist} for pid, mlist in providers.items()
                ],
            }
        ],
        "limits": {"max_parallel_per_project": 3},
        "selection": {"prefer_provider_discount_on_equal_fit": False},
    }


class ConfigurationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "config.sqlite3"

    def store(self, resources=None):
        store = configuration.ConfigurationStore(self.db_path)
        store.initialize(resources or MINIMAL_RESOURCES)
        return store

    def test_initialize_creates_default_template_once(self):
        store = self.store()
        templates = store.list_templates()
        self.assertEqual(len(templates), 1)
        default = templates[0]
        self.assertEqual(default["id"], "default")
        self.assertEqual(default["name"], "Default")
        self.assertEqual(default["revision"], 1)
        self.assertTrue(default["is_default"])
        self.assertFalse(default["archived"])
        self.assertEqual(default["payload"]["schema_version"], 1)
        self.assertIn("agent_configs", default["payload"])
        self.assertIn("limits", default["payload"])
        self.assertIn("selection", default["payload"])

    def test_initialize_empty_resources_creates_empty_default(self):
        store = configuration.ConfigurationStore(self.db_path)
        store.initialize({"agents": {}, "providers": {}, "models": {}})
        default = store.get_template("default")
        self.assertEqual(default["payload"]["agent_configs"], [])
        self.assertEqual(default["payload"]["limits"]["max_parallel_per_project"], 3)
        self.assertFalse(default["payload"]["selection"]["prefer_provider_discount_on_equal_fit"])

    def test_initialize_is_idempotent_across_restarts(self):
        store = self.store()
        first = store.get_template("default")
        store.initialize(MINIMAL_RESOURCES)
        store.initialize(MINIMAL_RESOURCES)
        second = store.get_template("default")
        self.assertEqual(second["revision"], 1)
        self.assertEqual(first["payload"], second["payload"])
        versions = self.db_count("configuration_versions", "template_id='default'")
        self.assertEqual(versions, 1)

    def test_concurrent_initialize_single_default_winner(self):
        store = configuration.ConfigurationStore(self.db_path)
        results = []

        def init():
            try:
                store.initialize(MINIMAL_RESOURCES)
                results.append("ok")
            except Exception as exc:  # noqa: BLE001
                results.append(type(exc).__name__)

        threads = [threading.Thread(target=init) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len([r for r in results if r == "ok"]), 4)
        self.assertEqual(self.db_count("configuration_templates"), 1)
        self.assertEqual(self.db_count("configuration_versions"), 1)

    def db_count(self, table, where="1=1"):
        with contextlib.closing(sqlite3.connect(self.db_path)) as db:
            return db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


class TemplateCopyAndSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "config.sqlite3"
        self.store = configuration.ConfigurationStore(self.db_path)
        self.store.initialize(MULTI_PROVIDER_RESOURCES)

    def test_create_template_copies_source_independently(self):
        custom = self.store.create_template("Custom", source_template_id="default")
        self.assertNotEqual(custom["id"], "default")
        self.assertEqual(custom["name"], "Custom")
        self.assertEqual(custom["revision"], 1)
        self.assertEqual(custom["payload"], self.store.get_template("default")["payload"])

        payload = custom["payload"].copy()
        payload["limits"] = {"max_parallel_per_project": 7}
        self.store.save_template(custom["id"], payload, expected_revision=1)
        unchanged = self.store.get_template("default")
        self.assertEqual(unchanged["payload"]["limits"]["max_parallel_per_project"], 3)

    def test_save_template_creates_new_revision_and_preserves_history(self):
        custom = self.store.create_template("Custom")
        payload = sample_payload(["kimi-code/k3-256k"])
        payload["limits"]["max_parallel_per_project"] = 5
        updated = self.store.save_template(custom["id"], payload, expected_revision=1)
        self.assertEqual(updated["revision"], 2)
        historical = self.store.get_template(custom["id"], revision=1)
        self.assertEqual(historical["payload"]["limits"]["max_parallel_per_project"], 3)
        self.assertEqual(self.store.get_template(custom["id"], revision=2)["payload"]["limits"]["max_parallel_per_project"], 5)

    def test_save_template_requires_cas_and_rejects_conflict(self):
        custom = self.store.create_template("Custom")
        payload = sample_payload(["kimi-code/k3-256k"])
        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.save_template(custom["id"], payload, expected_revision=99)
        self.assertEqual(ctx.exception.code, "revision_conflict")

    def test_save_template_concurrent_winner_loser(self):
        custom = self.store.create_template("Custom")
        results = []

        def attempt(rev):
            try:
                payload = sample_payload(["kimi-code/k3-256k"])
                payload["limits"]["max_parallel_per_project"] = rev
                self.store.save_template(custom["id"], payload, expected_revision=1)
                results.append(("ok", rev))
            except configuration.ConfigurationError as exc:
                results.append(("conflict", exc.code))

        t1 = threading.Thread(target=attempt, args=(5,))
        t2 = threading.Thread(target=attempt, args=(6,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        ok = [r for r in results if r[0] == "ok"]
        conflicts = [r for r in results if r[0] == "conflict"]
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][1], "revision_conflict")
        final = self.store.get_template(custom["id"])
        self.assertEqual(final["revision"], 2)

    def test_save_rejects_unknown_fields_and_bad_types(self):
        custom = self.store.create_template("Custom")
        base = sample_payload(["kimi-code/k3-256k"])
        invalids = [
            {**base, "extra_field": True},
            {k: v for k, v in base.items() if k != "schema_version"},
            {**base, "schema_version": "1"},
            {**base, "limits": {"max_parallel_per_project": 3, "extra": 1}},
            {**base, "selection": {"prefer_provider_discount_on_equal_fit": False, "extra": 1}},
        ]
        for payload in invalids:
            with self.subTest(payload=payload):
                with self.assertRaises(configuration.ConfigurationError):
                    self.store.save_template(custom["id"], payload, expected_revision=1)
        self.assertEqual(self.store.get_template(custom["id"])["revision"], 1)

    def test_save_rejects_bool_as_integer_and_invalid_limits(self):
        custom = self.store.create_template("Custom")
        base = sample_payload(["kimi-code/k3-256k"])
        invalids = [
            {**base, "limits": {"max_parallel_per_project": True}},
            {**base, "limits": {"max_parallel_per_project": -1}},
            {**base, "limits": {"max_parallel_per_project": 101}},
            {**base, "limits": {"max_parallel_per_project": "3"}},
            {**base, "limits": {"max_parallel_per_project": 2.5}},
            {**base, "selection": {"prefer_provider_discount_on_equal_fit": 0}},
        ]
        for payload in invalids:
            with self.subTest(payload=payload):
                with self.assertRaises(configuration.ConfigurationError):
                    self.store.save_template(custom["id"], payload, expected_revision=1)

    def test_save_accepts_zero_limit_at_range_boundaries(self):
        base = sample_payload(["kimi-code/k3-256k"])
        for limit in (0, 1, 100):
            with self.subTest(limit=limit):
                custom = self.store.create_template("L%d" % limit)
                payload = json.loads(json.dumps(base))
                payload["limits"]["max_parallel_per_project"] = limit
                saved = self.store.save_template(custom["id"], payload,
                                                 expected_revision=1)
                self.assertEqual(saved["revision"], 2)
                self.assertEqual(
                    saved["payload"]["limits"]["max_parallel_per_project"], limit)
                # Zero is stored as 0 (not normalized away) for Project limits.
                self.assertEqual(
                    self.store.get_template(custom["id"])["payload"]["limits"]["max_parallel_per_project"],
                    limit)

    def test_zero_limit_saves_resolves_and_snapshots_without_partial_writes(self):
        custom = self.store.create_template("Custom")
        payload = sample_payload(["kimi-code/k3-256k"])
        payload["limits"]["max_parallel_per_project"] = 0
        saved = self.store.save_template(custom["id"], payload, expected_revision=1)
        self.assertEqual(saved["payload"]["limits"]["max_parallel_per_project"], 0)
        project = self.store.register_project("zero", self.tmp.name)
        self.store.bind_project(project["id"], custom["id"],
                                expected_binding_revision=0)
        resolution = self.store.resolve_project(project["id"],
                                                MULTI_PROVIDER_RESOURCES)
        self.assertTrue(resolution["ready"])
        self.assertEqual(resolution["template_id"], custom["id"])
        snapshot_id = "zero-snap"
        pinned = self.store.resolve_project(project["id"],
                                            MULTI_PROVIDER_RESOURCES,
                                            snapshot_id=snapshot_id)
        self.assertEqual(pinned["snapshot_id"], snapshot_id)
        # Restart: the pinned snapshot and the zero limit survive a fresh store.
        restarted = configuration.ConfigurationStore(self.db_path)
        again = restarted.resolve_project(project["id"],
                                          MULTI_PROVIDER_RESOURCES,
                                          snapshot_id=snapshot_id)
        self.assertEqual(again["template_revision"], pinned["template_revision"])
        template = restarted.get_template(custom["id"])
        self.assertEqual(template["payload"]["limits"]["max_parallel_per_project"], 0)
        # CAS is still enforced for zero-limit saves (validate with resources
        # available so the revision check is what fires).
        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.save_template(custom["id"], template["payload"],
                                     expected_revision=99)
        self.assertEqual(ctx.exception.code, "revision_conflict")

    def test_save_rejects_duplicate_agent_and_model(self):
        custom = self.store.create_template("Custom")
        payload = {
            "schema_version": 1,
            "agent_configs": [
                {
                    "agent_type": "kimi-code",
                    "enabled": True,
                    "providers": [{"provider_id": "kimi-code", "models": ["kimi-code/k3-256k", "kimi-code/k3-256k"]}],
                }
            ],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], payload, expected_revision=1)

        payload["agent_configs"][0]["providers"][0]["models"] = ["kimi-code/k3-256k"]
        payload["agent_configs"].append(payload["agent_configs"][0].copy())
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], payload, expected_revision=1)

    def test_save_rejects_unknown_agent_and_unauthorized_model(self):
        custom = self.store.create_template("Custom")
        base = sample_payload(["kimi-code/k3-256k"])
        # Unknown agent type.
        bad = json.loads(json.dumps(base))
        bad["agent_configs"][0]["agent_type"] = "unknown-agent"
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], bad, expected_revision=1)
        # Model belongs to a different provider.
        bad = json.loads(json.dumps(base))
        bad["agent_configs"][0]["providers"][0]["provider_id"] = "zai-coding-plan"
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], bad, expected_revision=1)
        # Model not in provider aliases.
        bad = json.loads(json.dumps(base))
        bad["agent_configs"][0]["providers"][0]["models"] = ["zai-coding-plan/glm-5.3"]
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], bad, expected_revision=1)

    def test_save_rejects_provider_model_ownership(self):
        custom = self.store.create_template("Custom")
        # Two providers claim the same model alias.
        payload = {
            "schema_version": 1,
            "agent_configs": [
                {
                    "agent_type": "kimi-code",
                    "enabled": True,
                    "providers": [
                        {"provider_id": "kimi-code", "models": ["kimi-code/k3-256k"]},
                        {"provider_id": "zai-coding-plan", "models": ["kimi-code/k3-256k"]},
                    ],
                }
            ],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], payload, expected_revision=1)

    def test_save_rejects_enabled_without_models(self):
        custom = self.store.create_template("Custom")
        payload = {
            "schema_version": 1,
            "agent_configs": [{"agent_type": "kimi-code", "enabled": True, "providers": []}],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], payload, expected_revision=1)

    def test_save_rejects_empty_configs(self):
        custom = self.store.create_template("Custom")
        payload = {
            "schema_version": 1,
            "agent_configs": [],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        with self.assertRaises(configuration.ConfigurationError):
            self.store.save_template(custom["id"], payload, expected_revision=1)

    def test_save_allows_disabled_future_placeholder(self):
        resources = {
            **MULTI_PROVIDER_RESOURCES,
            "agents": {**MULTI_PROVIDER_RESOURCES["agents"], "zcode": {"status": "planned"}},
        }
        store = configuration.ConfigurationStore(self.db_path)
        store.initialize(resources)
        custom = store.create_template("Future")
        payload = {
            "schema_version": 1,
            "agent_configs": [
                {"agent_type": "kimi-code", "enabled": True, "providers": [{"provider_id": "kimi-code", "models": ["kimi-code/k3-256k"]}]},
                {"agent_type": "zcode", "enabled": False, "providers": []},
            ],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        saved = store.save_template(custom["id"], payload, expected_revision=1)
        self.assertEqual(saved["revision"], 2)

    def test_save_allows_all_disabled_known_agent_as_paused_config(self):
        custom = self.store.create_template("Paused")
        payload = {
            "schema_version": 1,
            "agent_configs": [
                {"agent_type": "kimi-code", "enabled": False, "providers": [{"provider_id": "kimi-code", "models": ["kimi-code/k3-256k"]}]},
            ],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        saved = self.store.save_template(custom["id"], payload, expected_revision=1)
        self.assertEqual(saved["revision"], 2)

    def test_save_rejects_enabled_future_agent(self):
        resources = {
            **MULTI_PROVIDER_RESOURCES,
            "agents": {**MULTI_PROVIDER_RESOURCES["agents"], "zcode": {"status": "planned"}},
        }
        store = configuration.ConfigurationStore(self.db_path)
        store.initialize(resources)
        custom = store.create_template("Future")
        payload = {
            "schema_version": 1,
            "agent_configs": [
                {"agent_type": "zcode", "enabled": True, "providers": [{"provider_id": "kimi-code", "models": ["kimi-code/k3-256k"]}]},
            ],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        with self.assertRaises(configuration.ConfigurationError):
            store.save_template(custom["id"], payload, expected_revision=1)


class TemplateArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "config.sqlite3"
        self.store = configuration.ConfigurationStore(self.db_path)
        self.store.initialize(MINIMAL_RESOURCES)
        self.custom = self.store.create_template("ToArchive")

    def test_archive_default_rejected(self):
        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.archive_template("default", expected_revision=1)
        self.assertEqual(ctx.exception.code, "default_template_protected")

    def test_archive_bound_template_rejected(self):
        project = self.store.register_project("demo", self.tmp.name)
        self.store.bind_project(project["id"], self.custom["id"], expected_binding_revision=0)
        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.archive_template(self.custom["id"], expected_revision=1)
        self.assertEqual(ctx.exception.code, "template_in_use")

    def test_archive_unknown_rejected(self):
        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.archive_template("no-such-template", expected_revision=1)
        self.assertEqual(ctx.exception.code, "not_found")

    def test_archive_unbound_template_succeeds(self):
        archived = self.store.archive_template(self.custom["id"], expected_revision=1)
        self.assertTrue(archived["archived"])
        listed = self.store.list_templates()
        self.assertIn(self.custom["id"], {t["id"] for t in listed})


class ProjectIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "config.sqlite3"
        self.store = configuration.ConfigurationStore(self.db_path)
        self.store.initialize(MINIMAL_RESOURCES)

    def test_project_canonical_identity_is_idempotent(self):
        root = Path(self.tmp.name) / "project-a"
        root.mkdir()
        first = self.store.register_project("Project A", str(root))
        second = self.store.register_project("Renamed A", str(root))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["name"], "Renamed A")
        self.assertEqual(self.store.list_projects(), [second])

    def test_project_canonical_resolves_symlinks(self):
        real = Path(self.tmp.name) / "real-project"
        real.mkdir()
        link = Path(self.tmp.name) / "link-project"
        link.symlink_to(real)
        first = self.store.register_project("Real", str(real))
        second = self.store.register_project("Link", str(link))
        self.assertEqual(first["id"], second["id"])

    def test_different_roots_are_different_projects(self):
        root_a = Path(self.tmp.name) / "project-a"
        root_b = Path(self.tmp.name) / "project-b"
        root_a.mkdir()
        root_b.mkdir()
        a = self.store.register_project("A", str(root_a))
        b = self.store.register_project("B", str(root_b))
        self.assertNotEqual(a["id"], b["id"])


class ProjectBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "config.sqlite3"
        self.store = configuration.ConfigurationStore(self.db_path)
        self.store.initialize(MINIMAL_RESOURCES)
        self.custom = self.store.create_template("Custom")
        self.project = self.store.register_project("demo", self.tmp.name)

    def test_bind_project_uses_cas_and_null_means_default(self):
        bound = self.store.bind_project(self.project["id"], self.custom["id"], expected_binding_revision=0)
        self.assertEqual(bound["template_id"], self.custom["id"])
        self.assertEqual(bound["binding_revision"], 1)

        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.bind_project(self.project["id"], None, expected_binding_revision=0)
        self.assertEqual(ctx.exception.code, "revision_conflict")

        rebound = self.store.bind_project(self.project["id"], None, expected_binding_revision=1)
        self.assertIsNone(rebound["template_id"])
        self.assertEqual(rebound["binding_revision"], 2)

    def test_bind_unknown_template_fails(self):
        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.bind_project(self.project["id"], "missing-template", expected_binding_revision=0)
        self.assertEqual(ctx.exception.code, "not_found")


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "config.sqlite3"
        self.store = configuration.ConfigurationStore(self.db_path)
        self.store.initialize(MULTI_PROVIDER_RESOURCES)
        self.project = self.store.register_project("demo", self.tmp.name)

    def test_resolve_default_uses_default_template(self):
        resolution = self.store.resolve_project(self.project["id"], MULTI_PROVIDER_RESOURCES)
        self.assertEqual(resolution["project_id"], self.project["id"])
        self.assertEqual(resolution["template_id"], "default")
        self.assertEqual(resolution["template_name"], "Default")
        self.assertTrue(resolution["ready"])
        aliases = {c["alias"] for c in resolution["candidates"]}
        self.assertIn("kimi-code/k3-256k", aliases)
        self.assertIn("zai-coding-plan/glm-5.3", aliases)
        self.assertEqual(resolution["unavailable"], [])

    def test_resolve_custom_template(self):
        custom = self.store.create_template("Custom")
        payload = sample_payload(["kimi-code/k3-256k"])
        self.store.save_template(custom["id"], payload, expected_revision=1)
        self.store.bind_project(self.project["id"], custom["id"], expected_binding_revision=0)
        resolution = self.store.resolve_project(self.project["id"], MULTI_PROVIDER_RESOURCES)
        self.assertEqual(resolution["template_id"], custom["id"])
        self.assertEqual({c["alias"] for c in resolution["candidates"]}, {"kimi-code/k3-256k"})

    def test_resolve_missing_alias_reports_unavailable_no_substitution(self):
        depleted_resources = json.loads(json.dumps(MULTI_PROVIDER_RESOURCES))
        del depleted_resources["models"]["kimi-code/k3-256k"]
        resolution = self.store.resolve_project(self.project["id"], depleted_resources)
        unavailable = {u["alias"] for u in resolution["unavailable"]}
        self.assertIn("kimi-code/k3-256k", unavailable)
        self.assertFalse(resolution["ready"])
        # No unauthorized replacement alias appears.
        candidate_aliases = {c["alias"] for c in resolution["candidates"]}
        self.assertNotIn("kimi-code/k3-256k", candidate_aliases)

    def test_resolve_excludes_disabled_agents(self):
        custom = self.store.create_template("DisabledAgent")
        payload = {
            "schema_version": 1,
            "agent_configs": [
                {"agent_type": "kimi-code", "enabled": False, "providers": [{"provider_id": "kimi-code", "models": ["kimi-code/k3-256k"]}]},
            ],
            "limits": {"max_parallel_per_project": 3},
            "selection": {"prefer_provider_discount_on_equal_fit": False},
        }
        self.store.save_template(custom["id"], payload, expected_revision=1)
        self.store.bind_project(self.project["id"], custom["id"], expected_binding_revision=0)
        resolution = self.store.resolve_project(self.project["id"], MULTI_PROVIDER_RESOURCES)
        self.assertEqual(resolution["candidates"], [])
        self.assertFalse(resolution["ready"])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "config.sqlite3"
        self.store = configuration.ConfigurationStore(self.db_path)
        self.store.initialize(MINIMAL_RESOURCES)
        self.project = self.store.register_project("demo", self.tmp.name)

    def test_snapshot_pinned_across_restart(self):
        snapshot_id = "snap-1"
        first = self.store.resolve_project(self.project["id"], MINIMAL_RESOURCES, snapshot_id=snapshot_id)
        self.assertEqual(first["snapshot_id"], snapshot_id)

        # Simulate process restart by opening a fresh store against the same db.
        restarted = configuration.ConfigurationStore(self.db_path)
        pinned = restarted.resolve_project(self.project["id"], MINIMAL_RESOURCES, snapshot_id=snapshot_id)
        self.assertEqual(pinned["snapshot_id"], snapshot_id)
        self.assertEqual(pinned["template_revision"], first["template_revision"])
        self.assertEqual(pinned["candidates"], first["candidates"])

    def test_snapshot_project_conflict_fails(self):
        snapshot_id = "shared-snap"
        other_root = Path(self.tmp.name) / "other"
        other_root.mkdir()
        other_project = self.store.register_project("other", str(other_root))
        self.store.resolve_project(self.project["id"], MINIMAL_RESOURCES, snapshot_id=snapshot_id)
        with self.assertRaises(configuration.ConfigurationError) as ctx:
            self.store.resolve_project(other_project["id"], MINIMAL_RESOURCES, snapshot_id=snapshot_id)
        self.assertEqual(ctx.exception.code, "snapshot_conflict")


if __name__ == "__main__":
    unittest.main()
