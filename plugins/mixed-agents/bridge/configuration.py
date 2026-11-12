"""Versioned template and project configuration, independent of dispatch policy."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid


class ConfigurationError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def _fail(code='invalid_argument', message='Invalid configuration argument.'):
    raise ConfigurationError(code, message)


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        _fail()
    return value.strip()


def _revision(value, minimum=1):
    if type(value) is not int or value < minimum:
        _fail()


def _identifier(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        _fail()
    return value


def _fields(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail()


class ConfigurationStore:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self._resources = None

    @contextmanager
    def _transaction(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, resources):
        self._resources = resources
        with self._transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS configuration_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL, is_default INTEGER NOT NULL, archived INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS configuration_versions (template_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(template_id, revision))')
            db.execute('CREATE TABLE IF NOT EXISTS configuration_projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, root TEXT NOT NULL, host_id TEXT NOT NULL, template_id TEXT, binding_revision INTEGER NOT NULL DEFAULT 0, UNIQUE(host_id, root))')
            db.execute('CREATE TABLE IF NOT EXISTS configuration_snapshots (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, resolution TEXT NOT NULL)')
            if not db.execute("SELECT 1 FROM configuration_templates WHERE id='default'").fetchone():
                providers = []
                for provider_id, provider in resources.get('providers', {}).items():
                    # Managed connections are never auto-granted; only the
                    # imported installation shapes the default template.
                    if provider.get('source') == 'managed':
                        continue
                    aliases = [alias for alias in provider.get('model_aliases', []) if resources.get('models', {}).get(alias, {}).get('agent_type') == 'kimi-code' and resources['models'][alias].get('provider_id') == provider_id and resources['models'][alias].get('source') != 'managed']
                    if aliases:
                        providers.append({'provider_id': provider_id, 'models': aliases})
                payload = {'schema_version': 1, 'agent_configs': [{'agent_type': 'kimi-code', 'enabled': True, 'providers': providers}] if providers else [], 'limits': {'max_parallel_per_project': 3}, 'selection': {'prefer_provider_discount_on_equal_fit': False}}
                self._validate(payload, resources, allow_empty=True)
                db.execute("INSERT INTO configuration_templates VALUES ('default', 'Default', 1, 1, 0)")
                db.execute('INSERT INTO configuration_versions VALUES (?, ?, ?)', ('default', 1, json.dumps(payload)))

    def _validate(self, payload, resources, allow_empty=False):
        _fields(payload, ('schema_version', 'agent_configs', 'limits', 'selection'))
        if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
            _fail()
        _fields(payload['limits'], ('max_parallel_per_project',))
        limit = payload['limits']['max_parallel_per_project']
        if type(limit) is not int or not 0 <= limit <= 100:
            _fail()
        _fields(payload['selection'], ('prefer_provider_discount_on_equal_fit',))
        if type(payload['selection']['prefer_provider_discount_on_equal_fit']) is not bool:
            _fail()
        configs = payload['agent_configs']
        if not isinstance(configs, list) or (not configs and not allow_empty):
            _fail()
        seen_agents = set()
        for config in configs:
            _fields(config, ('agent_type', 'enabled', 'providers'))
            agent = config['agent_type']
            if not isinstance(agent, str) or agent in seen_agents or agent not in resources.get('agents', {}):
                _fail()
            seen_agents.add(agent)
            if type(config['enabled']) is not bool or not isinstance(config['providers'], list):
                _fail()
            if resources['agents'][agent].get('status') != 'supported':
                if config['enabled'] or config['providers'] or resources['agents'][agent].get('status') != 'planned':
                    _fail()
                continue
            seen_providers, seen_models = set(), set()
            model_count = 0
            for provider in config['providers']:
                _fields(provider, ('provider_id', 'models'))
                provider_id = provider['provider_id']
                if not isinstance(provider_id, str) or provider_id in seen_providers or provider_id not in resources.get('providers', {}):
                    _fail()
                seen_providers.add(provider_id)
                models = provider['models']
                if not isinstance(models, list) or not models:
                    _fail()
                for alias in models:
                    if not isinstance(alias, str) or alias in seen_models:
                        _fail()
                    record = resources.get('models', {}).get(alias)
                    if not record or record.get('alias') != alias or record.get('provider_id') != provider_id or record.get('agent_type') != agent or alias not in resources['providers'][provider_id].get('model_aliases', []):
                        _fail()
                    seen_models.add(alias)
                    model_count += 1
            if config['enabled'] and not model_count:
                _fail()

    def _template(self, db, template_id, revision=None, allow_archived=False):
        _identifier(template_id)
        if revision is not None:
            _revision(revision)
        row = db.execute('SELECT * FROM configuration_templates WHERE id=?', (template_id,)).fetchone()
        if row is None or (row['archived'] and not allow_archived):
            _fail('not_found', 'Template not found.')
        actual_revision = row['revision'] if revision is None else revision
        version = db.execute('SELECT payload FROM configuration_versions WHERE template_id=? AND revision=?', (template_id, actual_revision)).fetchone()
        if version is None:
            _fail('not_found', 'Template revision not found.')
        return {'id': row['id'], 'name': row['name'], 'revision': actual_revision, 'payload': json.loads(version['payload']), 'is_default': bool(row['is_default']), 'archived': bool(row['archived'])}

    def list_templates(self):
        with self._transaction() as db:
            return [self._template(db, row['id'], allow_archived=True) for row in db.execute('SELECT id FROM configuration_templates ORDER BY is_default DESC, id').fetchall()]

    def get_template(self, template_id, revision=None):
        with self._transaction() as db:
            return self._template(db, template_id, revision)

    def create_template(self, name, source_template_id='default'):
        name = _name(name)
        with self._transaction() as db:
            source = self._template(db, source_template_id)
            template_id = uuid.uuid4().hex
            db.execute('INSERT INTO configuration_templates VALUES (?, ?, 1, 0, 0)', (template_id, name))
            db.execute('INSERT INTO configuration_versions VALUES (?, 1, ?)', (template_id, json.dumps(source['payload'])))
            return self._template(db, template_id)

    def save_template(self, template_id, payload, expected_revision, name=None, resources=None):
        _revision(expected_revision)
        if name is not None:
            name = _name(name)
        self._validate(payload, resources if resources is not None else self._resources or {})
        serialized = json.dumps(payload)
        with self._transaction() as db:
            current = self._template(db, template_id)
            if current['revision'] != expected_revision:
                _fail('revision_conflict', 'Template revision changed.')
            revision = expected_revision + 1
            db.execute('INSERT INTO configuration_versions VALUES (?, ?, ?)', (template_id, revision, serialized))
            db.execute('UPDATE configuration_templates SET revision=?, name=? WHERE id=?', (revision, current['name'] if name is None else name, template_id))
            return self._template(db, template_id)

    def archive_template(self, template_id, expected_revision):
        _revision(expected_revision)
        with self._transaction() as db:
            current = self._template(db, template_id)
            if current['is_default']:
                _fail('default_template_protected', 'Default template cannot be archived.')
            if current['revision'] != expected_revision:
                _fail('revision_conflict', 'Template revision changed.')
            if db.execute('SELECT 1 FROM configuration_projects WHERE template_id=?', (template_id,)).fetchone():
                _fail('template_in_use', 'Template is bound to a project.')
            db.execute('UPDATE configuration_templates SET archived=1 WHERE id=?', (template_id,))
            return self._template(db, template_id, allow_archived=True)

    def _project(self, db, project_id):
        _identifier(project_id)
        row = db.execute('SELECT id,name,host_id,template_id,binding_revision FROM configuration_projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            _fail('not_found', 'Project not found.')
        return dict(row)

    def register_project(self, name, root, host_id='local'):
        name, host_id = _name(name), _name(host_id)
        if not isinstance(root, str) or not root or '\x00' in root:
            _fail()
        try:
            canonical = str(Path(root).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            _fail()
        with self._transaction() as db:
            row = db.execute('SELECT id FROM configuration_projects WHERE host_id=? AND root=?', (host_id, canonical)).fetchone()
            project_id = row['id'] if row else uuid.uuid4().hex
            if row:
                db.execute('UPDATE configuration_projects SET name=? WHERE id=?', (name, project_id))
            else:
                db.execute('INSERT INTO configuration_projects VALUES (?, ?, ?, ?, NULL, 0)', (project_id, name, canonical, host_id))
            return self._project(db, project_id)

    def list_projects(self):
        with self._transaction() as db:
            return [self._project(db, row['id']) for row in db.execute('SELECT id FROM configuration_projects ORDER BY id').fetchall()]

    def bind_project(self, project_id, template_id, expected_binding_revision):
        _revision(expected_binding_revision, 0)
        with self._transaction() as db:
            project = self._project(db, project_id)
            if project['binding_revision'] != expected_binding_revision:
                _fail('revision_conflict', 'Project binding changed.')
            if template_id is not None:
                self._template(db, template_id)
            db.execute('UPDATE configuration_projects SET template_id=?, binding_revision=binding_revision+1 WHERE id=?', (template_id, project_id))
            return self._project(db, project_id)

    def resolve_project(self, project_id, resources, snapshot_id=None):
        if snapshot_id is not None:
            _identifier(snapshot_id)
        with self._transaction() as db:
            project = self._project(db, project_id)
            if snapshot_id is not None:
                snapshot = db.execute('SELECT * FROM configuration_snapshots WHERE id=?', (snapshot_id,)).fetchone()
                if snapshot:
                    if snapshot['project_id'] != project_id:
                        _fail('snapshot_conflict', 'Snapshot belongs to another project.')
                    return json.loads(snapshot['resolution'])
            template = self._template(db, project['template_id'] or 'default')
            resolution = self._resolution(template, project, resources)
            if snapshot_id is not None:
                resolution['snapshot_id'] = snapshot_id
                db.execute('INSERT INTO configuration_snapshots VALUES (?, ?, ?)', (snapshot_id, project_id, json.dumps(resolution)))
            return resolution

    def resolve_dispatch(self, db, resources, cwd=None, project_id=None):
        if cwd is not None and (not isinstance(cwd, str) or not cwd or '\x00' in cwd):
            _fail()
        try:
            path = Path(cwd).expanduser().resolve() if cwd is not None else None
        except (OSError, RuntimeError, ValueError):
            _fail('invalid_argument', 'Invalid execution directory.')
        project = None
        if project_id is not None:
            _identifier(project_id)
            row = db.execute('SELECT * FROM configuration_projects WHERE id=?', (project_id,)).fetchone()
            if row is None:
                _fail('not_found', 'Project not found.')
            if row['host_id'] != 'local' or (path is not None and not path.is_relative_to(Path(row['root']).resolve())):
                _fail('project_context_mismatch', 'Project does not match the local execution context.')
            project = dict(row)
        elif path is not None:
            matches = [dict(row) for row in db.execute("SELECT * FROM configuration_projects WHERE host_id='local'")
                       if path.is_relative_to(Path(row['root']).resolve())]
            project = max(matches, key=lambda row: len(Path(row['root']).parts), default=None)
        template = self._template(db, (project['template_id'] if project else None) or 'default')
        return self._resolution(template, project, resources)

    def _resolution(self, template, project, resources):
        candidates, unavailable = [], []
        for config in template['payload']['agent_configs']:
            if not config['enabled']:
                continue
            for provider in config['providers']:
                for alias in provider['models']:
                    record = resources.get('models', {}).get(alias)
                    provider_record = resources.get('providers', {}).get(provider['provider_id'])
                    # Saved references to known-but-disabled resources are
                    # retained but reported unavailable; reads never
                    # re-enable or substitute another model.
                    if resources.get('agents', {}).get(config['agent_type'], {}).get('status') != 'supported' or not record or record.get('alias') != alias or record.get('provider_id') != provider['provider_id'] or record.get('agent_type') != config['agent_type'] or not provider_record or alias not in provider_record.get('model_aliases', []) or provider_record.get('enabled') is False or provider_record.get('available') is False or record.get('enabled') is False or record.get('available') is False:
                        unavailable.append({'alias': alias, 'reason': 'resource_unavailable'})
                    else:
                        candidates.append({key: record[key] for key in ('alias', 'provider_id', 'agent_type', 'family', 'context', 'capabilities') if key in record})
        resolution = {'project_id': project['id'] if project else None, 'binding_revision': project['binding_revision'] if project else None, 'template_id': template['id'], 'template_revision': template['revision'], 'template_name': template['name'], 'payload': template['payload'], 'candidates': candidates, 'unavailable': unavailable, 'ready': bool(candidates) and not unavailable}
        return resolution
