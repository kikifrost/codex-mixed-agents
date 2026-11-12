#!/usr/bin/env node
/* Browser-level console checks (Node + playwright) against a stdlib Python
 * console launched with a fully synthetic provider TOML and temporary state.
 * No real credentials, no model invocation, no remote reads.
 *
 * Run:
 *   NODE_PATH=~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules \
 *   ~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node \
 *   tests/test_template_console_browser.js
 */
'use strict';
const {chromium} = require('playwright');
const {spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const BRIDGE = path.join(REPO, 'plugins', 'mixed-agents', 'bridge');
const VALIDATION = path.join(REPO, '.validation');
const PYTHON = process.env.MIXED_AGENTS_TEST_PYTHON || 'python3';

// Symbolic references only; these environment variables are never set.
const SYNTHETIC_TOML = `# Synthetic browser-test installation; no real credentials.
[models."kimi-code/k3-256k"]
model = "k3-256k"
provider = "managed:kimi-code"

[models."zai-synth/glm-5.3"]
model = "glm-5.3"
provider = "zai-managed"

[providers."managed:kimi-code"]
base_url = "https://api.kimi.com/coding/v1"
api_key_env = "MIXED_AGENTS_SYNTHETIC_KIMI_KEY"

[providers."zai-managed"]
base_url = "https://api.z.ai/api/coding"
api_key_env = "MIXED_AGENTS_SYNTHETIC_ZAI_KEY"
`;

const results = []; // {name, status: pass|fail|skip, detail}
let browser = null, page = null, dialogSeen = null;
function record(name, status, detail) {
  results.push({name, status, detail: detail || ''});
  console.log(`${status.toUpperCase().padEnd(4)} ${name}${detail && status === 'fail' ? ` :: ${detail}` : ''}`);
}
function assert(cond, message) { if (!cond) throw new Error(message); }

async function step(name, deps, fn) {
  const blocked = (deps || []).filter(dep => {
    const result = results.find(r => r.name === dep);
    return result && result.status !== 'pass';
  });
  if (blocked.length) {
    record(name, 'skip', `upstream not passed: ${blocked.join(', ')}`);
    return;
  }
  try { await fn(); record(name, 'pass'); } catch (error) {
    const detail = String(error && error.message || error);
    if (typeof page !== 'undefined' && page) {
      try {
        const status = await page.locator('#status').innerText().catch(() => '');
        const slug = name.replace(/[^a-z0-9]+/gi, '-').slice(0, 40).toLowerCase();
        await page.screenshot({path: path.join(VALIDATION, `console-browser-failure-${slug}.png`), fullPage: true});
        record(name, 'fail', `${detail} [status: ${status}]`);
        return;
      } catch { /* keep original detail */ }
    }
    record(name, 'fail', detail);
  }
}

function httpGet(port, pathname) {
  return new Promise((resolve, reject) => {
    const request = http.request({host: '127.0.0.1', port, path: pathname, method: 'GET',
                                  headers: {Host: `127.0.0.1:${port}`}}, response => {
      let body = '';
      response.on('data', chunk => body += chunk);
      response.on('end', () => resolve({status: response.statusCode, headers: response.headers, body}));
    });
    request.on('error', reject);
    request.end();
  });
}

async function main() {
  fs.mkdirSync(VALIDATION, {recursive: true});
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mixed-agents-console-'));
  const configPath = path.join(tmp, 'config.toml');
  fs.writeFileSync(configPath, SYNTHETIC_TOML, 'utf8');
  const stateDir = path.join(tmp, 'state');
  const projectRoot = path.join(tmp, 'proj-root');
  fs.mkdirSync(projectRoot, {recursive: true});

  const env = {...process.env, KIMI_CODE_HOME: tmp, MIXED_AGENTS_STATE_DIR: stateDir};
  delete env.MIXED_AGENTS_SYNTHETIC_KIMI_KEY;
  delete env.MIXED_AGENTS_SYNTHETIC_ZAI_KEY;
  const child = spawn(PYTHON, ['console.py', '--port', '0'], {cwd: BRIDGE, env, stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '';
  child.stderr.on('data', chunk => stderr += chunk);

  const origin = await new Promise((resolve, reject) => {
    let out = '';
    const timer = setTimeout(() => reject(new Error(`console did not report origin; stderr: ${stderr}`)), 15000);
    child.stdout.on('data', chunk => {
      out += chunk;
      const line = out.split('\n').find(value => value.startsWith('http://127.0.0.1:'));
      if (line) { clearTimeout(timer); resolve(line.trim()); }
    });
    child.on('exit', code => { clearTimeout(timer); reject(new Error(`console exited ${code}; stderr: ${stderr}`)); });
  });
  const port = Number(origin.split(':').pop());
  const page0 = await httpGet(port, '/');
  const token = (/nonce-([A-Za-z0-9_-]+)/.exec(page0.headers['content-security-policy'] || '') || [])[1];

  try {
    assert(token, 'console page must embed a CSP nonce token');
    browser = await chromium.launch();
    page = await browser.newPage({viewport: {width: 1280, height: 800}});
    page.setDefaultTimeout(8000);
    page.on('dialog', async dialog => {
      dialogSeen = {type: dialog.type(), message: dialog.message()};
      await dialog.accept();
    });
    const api = (method, args = {}) => page.evaluate(async ({method, args, token}) => {
      const response = await fetch('/api/call', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Console-Token': token},
        body: JSON.stringify({method, arguments: args})});
      const value = await response.json();
      if (!response.ok) throw new Error(`${method} -> ${value.error || response.status}: ${value.message || ''}`);
      return value;
    }, {method, args, token});
    const stateViaApi = () => page.evaluate(t => fetch('/api/state', {headers: {'X-Console-Token': t}}).then(r => r.json()), token);

    async function until(fn, timeout = 6000) {
      const end = Date.now() + timeout;
      let last;
      while (Date.now() < end) {
        try { const value = await fn(); if (value) return value; } catch (error) { last = error; }
        await page.waitForTimeout(120);
      }
      throw new Error(`timeout: ${last ? String(last.message || last) : 'condition not met'}`);
    }
    const contentText = async () => await page.locator('#content').innerText();
    const statusText = async () => await page.locator('#status').innerText();
    const draftMarker = () => page.locator('#draft-marker');
    const storedSession = () => page.evaluate(() => JSON.stringify(Object.entries(sessionStorage)));
    const clickButton = name => page.getByRole('button', {name, exact: true}).first().click();
    const gotoResources = async () => {
      await page.locator('nav [data-page=resources]').click();
      await until(async () => await page.locator('button', {hasText: '＋ 新增 Provider'}).count());
    };
    const kimiModel = () => page.locator('input[type=checkbox][aria-label*="256k" i]').first();

    await page.goto(origin);

    await step('templates page renders local editor surface', [], async () => {
      await until(async () => await page.locator('[data-page=templates][aria-current=page]').count());
      for (const label of ['模板名称', '编辑 Agent', '单 Project 并发'])
        await until(async () => await page.locator(`#content label:has-text("${label}")`).count());
      assert(await page.locator('nav [data-page=resources]').count(), 'resources nav missing');
      assert(await page.locator('nav [data-page=projects]').count(), 'projects nav missing');
    });

    await step('default template lists future agents as not enableable', [], async () => {
      await until(async () => await page.locator('select[aria-label="编辑 Agent"] option[value=zcode]').count());
      try {
        await page.locator('select[aria-label="编辑 Agent"]').selectOption('zcode');
        await until(async () => (await contentText()).includes('尚未接入'));
        assert(await page.locator('input[aria-label="启用此 Agent"]').isDisabled(), 'future agent enable checkbox must be disabled');
      } finally {
        // Restore the supported agent so later steps always see model checkboxes.
        const select = page.locator('select[aria-label="编辑 Agent"]');
        if (await select.count()) await select.selectOption('kimi-code').catch(() => {});
      }
      await until(async () => await kimiModel().count());
      assert(!(await page.locator('input[aria-label="启用此 Agent"]').isDisabled()),
             'supported agent must be enableable');
    });

    await step('template project concurrency accepts zero in the editor', ['default template lists future agents as not enableable'], async () => {
      const parallel = page.locator('input[aria-label="单 Project 并发上限（0 = 不限制）"]');
      await until(async () => await parallel.count());
      assert((await parallel.inputValue()) === '3', 'default template limit starts at 3');
      // Invalid values are rejected with the draft retained.
      await parallel.fill('-1');
      await clickButton('保存模板');
      await until(async () => (await statusText()).includes('0–100 的整数'));
      assert((await parallel.inputValue()) === '-1', 'invalid input must not lose draft state');
      await parallel.fill('0');
      await until(async () => await draftMarker().isVisible());
      await clickButton('保存模板');
      await until(async () => (await statusText()).includes('模板已发布'));
      let saved = await api('get_template', {template_id: 'default'});
      assert(saved.payload.limits.max_parallel_per_project === 0,
             'zero Project concurrency must persist as 0, not drop the limit');
      // Restore the default so later steps start from the documented default.
      await parallel.fill('3');
      await until(async () => await draftMarker().isVisible());
      await clickButton('保存模板');
      await until(async () => (await statusText()).includes('模板已发布'));
      saved = await api('get_template', {template_id: 'default'});
      assert(saved.payload.limits.max_parallel_per_project === 3,
             'restoring a finite Project concurrency must persist');
    });

    await step('template draft survives navigation and reload with unload warning', ['template project concurrency accepts zero in the editor'], async () => {
      const agentSelect = page.locator('select[aria-label="编辑 Agent"]');
      if (await agentSelect.count() && (await agentSelect.inputValue()) !== 'kimi-code')
        await agentSelect.selectOption('kimi-code');
      await until(async () => await kimiModel().count());
      assert(await kimiModel().isChecked(), 'seeded kimi model should start granted');
      await kimiModel().uncheck();
      await until(async () => await draftMarker().isVisible());
      assert((await draftMarker().innerText()) === '未保存草稿', 'draft marker must announce unsaved drafts');
      dialogSeen = null;
      await page.reload();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      assert(dialogSeen && dialogSeen.type === 'beforeunload', 'dirty draft must warn before unload');
      assert(!(await kimiModel().isChecked()), 'draft must persist across reload');
      await until(async () => await draftMarker().isVisible());
      await page.locator('nav [data-page=resources]').click();
      await page.locator('nav [data-page=templates]').click();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      assert(!(await kimiModel().isChecked()), 'draft must persist across internal navigation');
      await until(async () => await draftMarker().isVisible());
    });

    let customId = '';
    await step('copy template copies the saved version, not the draft', [], async () => {
      await page.locator('input[aria-label="复制已保存版本：新模板名称"]').fill('定制副本');
      await clickButton('复制模板');
      await until(async () => (await statusText()).includes('已从保存版本复制'));
      const templates = await api('list_templates');
      customId = templates.templates.find(t => t.name === '定制副本').id;
      await until(async () => (await page.locator('#content h2').first().innerText()) === '定制副本');
      assert(await kimiModel().isChecked(), 'copy must come from the saved version (model still granted)');
      await page.locator('.objects button', {hasText: 'Default'}).first().click();
      await until(async () => !(await kimiModel().isChecked()));
    });

    await step('save publishes draft and stale revision conflicts keep edits', [], async () => {
      // Discard the leftover default-template draft so the marker has a clean baseline.
      await page.locator('.objects button', {hasText: 'Default'}).first().click();
      await until(async () => (await page.locator('#content h2').first().innerText()) === 'Default');
      await until(async () => await draftMarker().isVisible());
      await clickButton('丢弃草稿并重载');
      await until(async () => (await statusText()).includes('草稿已丢弃'));
      await until(async () => !(await draftMarker().isVisible()));
      await page.locator('.objects button', {hasText: '定制副本'}).first().click();
      await until(async () => (await page.locator('#content h2').first().innerText()) === '定制副本');
      await page.locator('input[aria-label="模板名称"]').fill('定制副本 改');
      await until(async () => await draftMarker().isVisible());
      await clickButton('保存模板');
      await until(async () => (await statusText()).includes('模板已发布'));
      await until(async () => !(await draftMarker().isVisible()), 6000);
      await until(async () => (await contentText()).includes('保存版本 v2'));
      // A second writer publishes revision 3 outside the page.
      const current = await api('get_template', {template_id: customId});
      await api('save_template', {template_id: customId, payload: current.payload,
                                  expected_revision: 2, name: '外部写入'});
      await page.locator('input[aria-label="模板名称"]').fill('冲突草稿');
      await clickButton('保存模板');
      await until(async () => (await statusText()).includes('版本冲突'));
      assert((await page.locator('input[aria-label="模板名称"]').inputValue()) === '冲突草稿',
             'CAS conflict must retain the draft edits');
      await clickButton('丢弃草稿并重载');
      await until(async () => (await page.locator('#content h2').first().innerText()) === '外部写入');
      await until(async () => (await contentText()).includes('保存版本 v3'));
      const saved = await api('get_template', {template_id: customId});
      assert(saved.revision === 3 && saved.name === '外部写入', 'discard must reload the saved version');
    });

    await step('project registration keeps root private; bind/apply/preview use saved data', [], async () => {
      await page.locator('nav [data-page=projects]').click();
      await until(async () => await page.locator('#content label:has-text("Project 名称")').count());
      await page.locator('input[aria-label="Project 名称"]').fill('Demo 项目');
      await page.locator('input[aria-label="Project root（私有路径）"]').fill(projectRoot);
      await page.locator('input[aria-label="Host ID"]').fill('local');
      // The private root survives internal navigation in memory only.
      await page.locator('nav [data-page=templates]').click();
      await page.locator('nav [data-page=projects]').click();
      await until(async () => await page.locator('input[aria-label="Project root（私有路径）"]').count());
      assert((await page.locator('input[aria-label="Project root（私有路径）"]').inputValue()) === projectRoot,
             'private root must survive internal navigation');
      assert(!((await storedSession()).includes(projectRoot)),
             'private root must never be written to browser storage');
      await page.reload();
      await until(async () => await page.locator('input[aria-label="Project root（私有路径）"]').count());
      assert((await page.locator('input[aria-label="Project root（私有路径）"]').inputValue()) === '',
             'private root must clear on browser refresh');
      assert((await page.locator('input[aria-label="Project 名称"]').inputValue()) === 'Demo 项目',
             'registration draft fields should persist across refresh');
      await page.locator('input[aria-label="Project root（私有路径）"]').fill(projectRoot);
      await clickButton('注册 Project');
      await until(async () => (await statusText()).includes('Project 已注册'));
      await until(async () => await page.locator('#content h2', {hasText: 'Demo 项目'}).count());
      const body = await page.locator('body').innerText();
      assert(!body.includes(projectRoot), 'project root path must never render in the browser');
      const projects = await api('list_projects');
      assert(!JSON.stringify(projects).includes(projectRoot), 'project root must stay out of API listings');
      await page.locator('select[aria-label="待应用模板"]').selectOption(customId);
      await clickButton('应用绑定');
      await until(async () => (await statusText()).includes('Project 绑定已应用'));
      await until(async () => await page.locator('#content p', {hasText: '当前生效：外部写入 · v3'}).count());
      await clickButton('预览当前绑定');
      const preview = await until(async () => {
        const text = await page.locator('#content pre').first().innerText();
        return text.includes('配置 readiness') ? text : null;
      });
      assert(preview.includes('外部写入') && preview.includes('Stage 3'),
             'preview must show the saved template and the stage-3 boundary');
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-projects.png'), fullPage: true});
    });

    await step('provider drafts survive page switching', [], async () => {
      await page.locator('nav [data-page=resources]').click();
      await until(async () => await page.locator('button', {hasText: '＋ 新增 Provider'}).count());
      await page.locator('.objects button', {hasText: 'kimi-code'}).first().click();
      await until(async () => await page.locator('#content label:has-text("连接名称")').count());
      await until(async () => await page.locator('#content label:has-text("凭据引用（不是凭据值）")').count());
      await page.locator('input[aria-label="连接名称"]').fill('kimi-code 改名草稿');
      await page.locator('nav [data-page=templates]').click();
      await page.locator('nav [data-page=resources]').click();
      await until(async () => await page.locator('input[aria-label="连接名称"]').count());
      assert((await page.locator('input[aria-label="连接名称"]').inputValue()) === 'kimi-code 改名草稿',
             'provider draft must survive page switching');
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-resources.png'), fullPage: true});
    });

    await step('malformed credential references never persist to browser storage', [], async () => {
      await page.locator('nav [data-page=resources]').click();
      await clickButton('＋ 新增 Provider');
      await until(async () => await page.locator('input[aria-label="凭据引用（不是凭据值）"]').count());
      const malformed = 'plaintext-secret-lookalike-0123456789abcdef';
      await page.locator('input[aria-label="连接名称"]').fill('坏引用连接');
      await page.locator('input[aria-label="凭据引用（不是凭据值）"]').fill(malformed);
      await until(async () => await draftMarker().isVisible());
      assert(!((await storedSession()).includes(malformed)),
             'malformed credential_ref must not be persisted to browser storage');
      await page.reload();
      await until(async () => await page.locator('input[aria-label="凭据引用（不是凭据值）"]').count());
      assert((await page.locator('input[aria-label="凭据引用（不是凭据值）"]').inputValue()) === '',
             'malformed credential_ref must be dropped from the restored draft');
      assert((await page.locator('input[aria-label="连接名称"]').inputValue()) === '坏引用连接',
             'non-secret draft fields should persist across refresh');
      await page.locator('input[aria-label="连接名称"]').fill('');
    });

    await step('imported providers expose stage-2 public metadata', [], async () => {
      const resources = await api('configuration_resources');
      const kimi = resources.providers['kimi-code'];
      assert(kimi && kimi.provider_type === 'kimi', 'kimi provider_type must classify by configured origin');
      const zai = resources.providers['zai-synth'];
      assert(zai && zai.provider_type === 'zai', 'zai provider_type must classify by configured origin');
      for (const provider of [kimi, zai]) {
        for (const key of ['label', 'endpoint', 'protocol', 'credential_ref', 'enabled', 'revision', 'source'])
          assert(key in provider, `provider field ${key} missing`);
        assert(/^env:[A-Z0-9_]+$/.test(provider.credential_ref),
               `credential_ref must stay a symbolic env reference, got ${provider.credential_ref}`);
      }
    });

    await step('header concurrency panel opens on every page and explains zero', ['imported providers expose stage-2 public metadata'], async () => {
      assert(await page.locator('#concurrency-settings').isHidden(), 'panel must start collapsed');
      await clickButton('编辑并发上限');
      await until(async () => await page.locator('#concurrency-settings:visible').count());
      for (const id of ['shared-limit', 'zai-limit', 'account-rows'])
        await until(async () => await page.locator('#concurrency-settings #' + id).count());
      assert((await page.locator('#shared-limit').inputValue()) === '5',
             'default shared cap displays 5');
      assert((await page.locator('#zai-limit').inputValue()) === '3',
             'default Z.ai cap displays 3');
      const capacity = await page.locator('#capacity').innerText();
      assert(capacity.includes('当前占用') && capacity.includes('并发上限') && capacity.includes('Z.ai'),
             'header must distinguish occupancy from configured maximum and include Z.ai');
      const panelText = await page.locator('#concurrency-settings').innerText();
      assert(panelText.includes('0 = 不设置这一层上限'), 'panel must explain the zero semantics');
      assert((await page.locator('#edit-concurrency').innerText()).includes('收起'),
             'open button must offer collapse');
      // Stays open across page switches: concurrency is global, not per page.
      await page.locator('nav [data-page=projects]').click();
      await until(async () => await page.locator('#content label:has-text("Project 名称")').count());
      assert(await page.locator('#concurrency-settings:visible').count(),
             'panel must stay visible on the projects page');
      await page.locator('nav [data-page=templates]').click();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      assert(await page.locator('#concurrency-settings:visible').count(),
             'panel must stay visible on the templates page');
      // Collapse is allowed and restores the original button label.
      await clickButton('收起并发设置');
      await until(async () => await page.locator('#concurrency-settings').isHidden());
      assert((await page.locator('#edit-concurrency').innerText()).includes('编辑并发上限'),
             'collapse must restore the edit label');
      await clickButton('编辑并发上限');
      await until(async () => await page.locator('#concurrency-settings:visible').count());
    });

    await step('opening concurrency panel performs no configuration writes', ['header concurrency panel opens on every page and explains zero'], async () => {
      const before = await stateViaApi();
      await clickButton('收起并发设置');
      await until(async () => await page.locator('#concurrency-settings').isHidden());
      await clickButton('编辑并发上限');
      await until(async () => await page.locator('#concurrency-settings:visible').count());
      assert(JSON.stringify(await stateViaApi()) === JSON.stringify(before),
             'opening or collapsing the panel must not write configuration');
    });

    await step('zero concurrency inputs normalize to unlimited and display as 0', ['opening concurrency panel performs no configuration writes'], async () => {
      // Legacy unlimited state displays as 0.
      await api('set_concurrency', {limit: 'unlimited'});
      await clickButton('刷新配置');
      await until(async () => (await page.locator('#shared-limit').inputValue()) === '0');
      await page.locator('#shared-limit').fill('0');
      await page.locator('#shared button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      let state = await stateViaApi();
      assert(state.kimi_cap === null && state.kimi_unlimited === true,
             'shared zero must persist as unlimited');
      assert((await page.locator('#shared-limit').inputValue()) === '0',
             'unlimited shared cap must display as 0');
      // Z.ai numeric zero goes through the split concurrency form only.
      await page.locator('#zai-limit').fill('0');
      await page.locator('#zai-concurrency button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      state = await stateViaApi();
      assert(state.zai_policy.cap === null && state.zai_policy.unlimited === true,
             'Z.ai zero must persist as unlimited');
      assert((await page.locator('#zai-limit').inputValue()) === '0',
             'unlimited Z.ai cap must display as 0');
      // Kimi account numeric zero normalizes to the unlimited sentinel.
      const accountInput = page.locator('#account-rows input').first();
      const prefix = await accountInput.getAttribute('data-account');
      await accountInput.fill('0');
      await page.locator('#accounts button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      state = await stateViaApi();
      assert(state.kimi_accounts.limits[prefix] === 'unlimited',
             'account zero must persist as the unlimited sentinel');
      await until(async () => (await page.locator('#account-rows input').first().inputValue()) === '0');
      // Restore documented defaults for later steps.
      await page.locator('#shared-limit').fill('5');
      await page.locator('#shared button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      await page.locator('#zai-limit').fill('3');
      await page.locator('#zai-concurrency button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
    });

    await step('invalid concurrency input rejected without losing draft', ['zero concurrency inputs normalize to unlimited and display as 0'], async () => {
      // #shared-limit is type=number min=0 step=1 required: the browser runs
      // native constraint validation before submit, so a negative value never
      // reaches the server and the JS 非负整数 error path stays untriggered.
      // That native block is the spec's "reject invalid input without losing
      // draft state" behavior; verify it plus no configuration write.
      const before = await stateViaApi();
      await page.locator('#shared-limit').fill('-1');
      await page.locator('#shared button').click();
      assert(!(await page.locator('#shared-limit').evaluate(el => el.validity.valid)),
             'native min=0 constraint must reject a negative shared cap');
      assert(JSON.stringify(await stateViaApi()) === JSON.stringify(before),
             'rejected submit must not write configuration');
      assert((await page.locator('#shared-limit').inputValue()) === '-1',
             'rejected input must keep the draft value');
      await clickButton('丢弃并发草稿并重载');
      await until(async () => (await statusText()).includes('并发草稿已丢弃'));
      assert((await page.locator('#shared-limit').inputValue()) === '5',
             'discard must reload the saved shared cap');
    });

    await step('concurrency drafts survive provider and page changes', ['invalid concurrency input rejected without losing draft'], async () => {
      await page.locator('#shared-limit').fill('7');
      await page.locator('nav [data-page=resources]').click();
      await page.locator('.objects button', {hasText: 'zai-synth'}).first().click();
      await until(async () => await page.locator('#zai:visible').count());
      assert((await page.locator('#shared-limit').inputValue()) === '7',
             'concurrency draft must survive provider switching');
      await page.locator('nav [data-page=templates]').click();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      await page.locator('nav [data-page=resources]').click();
      await until(async () => await page.locator('button', {hasText: '＋ 新增 Provider'}).count());
      assert((await page.locator('#shared-limit').inputValue()) === '7',
             'concurrency draft must survive page switching');
      await clickButton('丢弃并发草稿并重载');
      await until(async () => (await page.locator('#shared-limit').inputValue()) === '5',
                  6000);
    });

    await step('kimi provider shows kimi-specific policy forms only', ['header concurrency panel opens on every page and explains zero'], async () => {
      await gotoResources();
      await page.locator('.objects button', {hasText: 'kimi-code'}).first().click();
      await until(async () => await page.locator('#kimi:visible').count());
      assert(!(await page.locator('#zai:visible').count()), 'zai quota form must be hidden for a kimi provider');
      assert(!(await page.locator('#cost:visible').count()), 'cost form must be hidden for a kimi provider');
      // Concurrency controls live in the header panel, never duplicated or
      // filtered inside the per-provider policy area.
      assert(!(await page.locator('#policies #shared').count()), 'shared form must not be in the provider policy area');
      assert(!(await page.locator('#policies #zai-concurrency').count()), 'zai concurrency form must not be in the provider policy area');
      assert(!(await page.locator('#policies #accounts').count()), 'accounts form must not be duplicated in the provider policy area');
      assert(await page.locator('#concurrency-settings:visible').count(),
             'concurrency panel must stay visible regardless of the selected provider');
      assert(await page.locator('#concurrency-settings #shared-limit').count(),
             'shared concurrency stays editable in the header panel');
    });

    await step('zai provider shows zai-specific policy forms only', ['header concurrency panel opens on every page and explains zero'], async () => {
      await gotoResources();
      await page.locator('.objects button', {hasText: 'zai-synth'}).first().click();
      await until(async () => await page.locator('#zai:visible').count());
      await until(async () => await page.locator('#cost:visible').count());
      assert(!(await page.locator('#kimi:visible').count()), 'kimi form must be hidden for a zai provider');
      assert(!(await page.locator('#policies #accounts').count()), 'no duplicate accounts form in the provider policy area');
      assert(await page.locator('#concurrency-settings:visible').count(),
             'concurrency panel must stay visible for a zai provider');
    });

    await step('provider settings line reports policy scope and handler', ['imported providers expose stage-2 public metadata'], async () => {
      await page.locator('.objects button', {hasText: 'kimi-code'}).first().click();
      const kimiLine = await until(async () => {
        const text = await page.locator('#content small', {hasText: '策略 scope'}).last().innerText();
        return text.includes('handler：kimi') ? text : null;
      });
      assert(kimiLine.includes('策略 scope'), 'settings line must describe the shared policy scope');
      await page.locator('.objects button', {hasText: 'zai-synth'}).first().click();
      await until(async () => (await page.locator('#content small', {hasText: '策略 scope'}).last().innerText()).includes('handler：zai'));
    });

    await step('zai concurrency and quota forms save independently', ['zai provider shows zai-specific policy forms only'], async () => {
      await gotoResources();
      await page.locator('.objects button', {hasText: 'zai-synth'}).first().click();
      await until(async () => await page.locator('#zai:visible').count());
      const before = await stateViaApi();
      await page.locator('#zai-limit').fill('4');
      await page.locator('#zai-concurrency button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      let state = await stateViaApi();
      assert(state.zai_policy.cap === 4, `zai cap must persist, got ${state.zai_policy.cap}`);
      assert(state.kimi_policy.probe_cooldown_minutes === before.kimi_policy.probe_cooldown_minutes,
             'saving the zai concurrency form must not touch kimi policy');
      // The quota policy form owns guard/reserve and must not touch the limit.
      await page.locator('#zai-guard').check();
      await page.locator('#zai-reserve').fill('12');
      await page.locator('#zai button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      state = await stateViaApi();
      assert(state.zai_policy.cap === 4 && state.zai_policy.usage_guard === true &&
             state.zai_policy.stop_remaining_percent === 12,
             'quota form must persist guard settings without touching the concurrency limit');
      // The concurrency form saves only the limit; quota settings survive it.
      await page.locator('#zai-limit').fill('9');
      await page.locator('#zai-concurrency button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      state = await stateViaApi();
      assert(state.zai_policy.cap === 9 && state.zai_policy.usage_guard === true,
             'concurrency form must update only the limit; guard stays enabled');
      // Discard restores the last saved values.
      await page.locator('#zai-limit').fill('8');
      await page.locator('#discard-concurrency').click();
      await until(async () => (await statusText()).includes('并发草稿已丢弃'));
      assert((await page.locator('#zai-limit').inputValue()) === '9', 'discard must reload saved concurrency values');
      // Restore documented defaults for later steps.
      await page.locator('#zai-limit').fill('3');
      await page.locator('#zai-concurrency button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      await page.locator('#zai-guard').uncheck();
      await page.locator('#zai-reserve').fill('10');
      await page.locator('#zai button').click();
      await until(async () => (await statusText()).includes('全局策略已保存'));
      state = await stateViaApi();
      assert(state.zai_policy.cap === 3 && state.zai_policy.usage_guard === false,
             'zai defaults must be restored');
    });

    await step('create generic provider with symbolic credential reference', [], async () => {
      await gotoResources();
      await clickButton('＋ 新增 Provider');
      await until(async () => await page.locator('#content h2', {hasText: '新增公共 Provider'}).count());
      await page.locator('input[aria-label="不可变 Provider ID"]').fill('generic-synth');
      await page.locator('input[aria-label="连接名称"]').fill('通用连接');
      await page.locator('input[aria-label="HTTPS endpoint"]').fill('https://api.example-synth.invalid/v1');
      await page.locator('select[aria-label="Provider 类型"]').selectOption('generic');
      await page.locator('select[aria-label="协议"]').selectOption('openai');
      await page.locator('input[aria-label="凭据引用（不是凭据值）"]').fill('env:MIXED_AGENTS_SYNTHETIC_GENERIC_KEY');
      await clickButton('创建 Provider');
      await until(async () => (await statusText()).includes('公共 Provider 已创建'));
      await until(async () => await page.locator('.objects button', {hasText: '通用连接'}).count());
      assert(!(await page.locator('#kimi:visible').count()) && !(await page.locator('#zai:visible').count()),
             'generic provider must not show kimi/zai policy forms');
    });

    await step('model entry is created and editable without auto-granting templates', ['create generic provider with symbolic credential reference'], async () => {
      await page.locator('.objects button', {hasText: '通用连接'}).first().click();
      await until(async () => await page.locator('#content label:has-text("精确别名")').count());
      await page.locator('input[aria-label="精确别名"]').fill('generic-synth/glm-5.3');
      await page.locator('input[aria-label="Upstream model"]').fill('glm-5.3');
      await page.locator('section:has(input[aria-label="精确别名"]) input[aria-label="显示名称"]').fill('GLM 5.3 通用');
      await page.locator('section:has(input[aria-label="精确别名"]) input[aria-label="Context tokens"]').fill('200000');
      await clickButton('创建模型入口');
      await until(async () => (await statusText()).includes('模型入口已创建'));
      await until(async () => await page.locator('#content h3', {hasText: 'generic-synth/glm-5.3'}).count());
      const templates = await api('list_templates');
      assert(!JSON.stringify(templates).includes('generic-synth'),
             'creating a model must not auto-grant it to saved templates');
      await page.locator('section:has(h3:text-is("generic-synth/glm-5.3")) input[aria-label="显示名称"]').fill('GLM 5.3 通用 改');
      await clickButton('保存模型');
      await until(async () => (await statusText()).includes('公共模型已保存'));
    });

    await step('create-model error after bottom submit stays in viewport and keeps the draft', ['model entry is created and editable without auto-granting templates'], async () => {
      const newModel = page.locator('section:has(h3:text-is("新增模型入口"))');
      await until(async () => await newModel.count());
      const aliasInput = newModel.locator('input[aria-label="精确别名"]');
      const upstreamInput = newModel.locator('input[aria-label="Upstream model"]');
      const displayInput = newModel.locator('input[aria-label="显示名称"]');
      const contextInput = newModel.locator('input[aria-label="Context tokens"]');
      await aliasInput.fill('generic-synth/glm 5.3');  // whitespace: invalid alias
      await upstreamInput.fill('glm-5.3');
      await displayInput.fill('GLM 5.3 错误草稿');
      await contextInput.fill('200000');
      await until(async () => await draftMarker().isVisible());
      // Submit from the bottom of the form: the failure must surface where
      // the user is looking, not at the top of the document.
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await newModel.getByRole('button', {name: '创建模型入口', exact: true}).click();
      const status = page.locator('#status');
      await until(async () => await status.evaluate(el => el.classList.contains('error') && el.textContent.trim().length > 0));
      const box = await status.boundingBox();
      const viewport = page.viewportSize();
      assert(box && box.y >= -1 && box.y + box.height <= viewport.height + 1,
             `error feedback must stay inside the viewport, got box ${JSON.stringify(box)} for viewport height ${viewport.height}`);
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-error-feedback.png')});
      // The rejected submit must not lose the draft values.
      assert((await aliasInput.inputValue()) === 'generic-synth/glm 5.3', 'invalid alias draft must be retained');
      assert((await upstreamInput.inputValue()) === 'glm-5.3', 'upstream draft must be retained');
      assert((await displayInput.inputValue()) === 'GLM 5.3 错误草稿', 'display-name draft must be retained');
      assert((await contextInput.inputValue()) === '200000', 'context draft must be retained');
      // Restore a pristine create-model form so later steps start clean.
      await page.evaluate(() => {
        const key = Object.keys(sessionStorage).find(k => k.startsWith('mixed-agents-console-drafts:'));
        const saved = key && JSON.parse(sessionStorage.getItem(key) || 'null');
        if (saved) {
          delete saved.drafts.newModel['generic-synth'];
          sessionStorage.setItem(key, JSON.stringify(saved));
        }
      });
      await page.reload();
      await page.locator('nav [data-page=resources]').click();
      await page.locator('.objects button', {hasText: '通用连接'}).first().click();
      await until(async () => await newModel.count());
      assert((await aliasInput.inputValue()) === 'generic-synth/', 'create-model draft must be restored to the pristine default');
      // Other suite steps intentionally leave unrelated provider/template
      // drafts, so check only this step's new-model draft for generic-synth:
      // it must be gone, or back at the app's pristine default (the console
      // re-persists an untouched create-model form).
      const newModelDraft = await page.evaluate(() => {
        const key = Object.keys(sessionStorage).find(k => k.startsWith('mixed-agents-console-drafts:'));
        const saved = key && JSON.parse(sessionStorage.getItem(key) || 'null');
        return saved && saved.drafts.newModel ? saved.drafts.newModel['generic-synth'] : undefined;
      });
      assert(newModelDraft === undefined || Object.entries(newModelDraft).every(([k, v]) =>
               k === 'capabilities' ? v.length === 0 :
               k === 'context' ? v === 256000 :
               k === 'alias' ? (v === '' || v === 'generic-synth/') : !v),
             `invalid new-model draft must be cleared, got: ${JSON.stringify(newModelDraft)}`);
    });

    await step('disabling a provider retains references and reports unavailable', ['model entry is created and editable without auto-granting templates'], async () => {
      // Grant the new model through the template editor, then disable the provider.
      await page.locator('nav [data-page=templates]').click();
      await page.locator('.objects button', {hasText: '外部写入'}).first().click();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      const grantBox = page.locator('.group', {has: page.locator('h3', {hasText: '通用连接'})}).locator('input[type=checkbox]').first();
      await until(async () => await grantBox.count());
      await grantBox.check();
      await clickButton('保存模板');
      await until(async () => (await statusText()).includes('模板已发布'));
      let saved = await api('get_template', {template_id: customId});
      assert(JSON.stringify(saved.payload).includes('generic-synth/glm-5.3'), 'grant must be saved in the template');
      await page.locator('nav [data-page=resources]').click();
      await page.locator('.objects button', {hasText: '通用连接'}).first().click();
      await until(async () => await page.locator('input[aria-label="公共连接启用"]').count());
      await page.locator('input[aria-label="公共连接启用"]').uncheck();
      await clickButton('保存公共连接');
      await until(async () => (await statusText()).includes('公共连接已保存'));
      await until(async () => await page.locator('.objects button', {hasText: '通用连接 · 已停用'}).count());
      saved = await api('get_template', {template_id: customId});
      assert(JSON.stringify(saved.payload).includes('generic-synth/glm-5.3'),
             'disabling must retain template references');
      const projects = await api('list_projects');
      const resolution = await api('resolve_project_config', {project_id: projects.projects[0].id});
      const unavailable = (resolution.unavailable || []).map(m => m.alias);
      assert(unavailable.includes('generic-synth/glm-5.3'),
             'disabled resource must be reported unavailable, not substituted');
      assert(!resolution.ready, 'resolution with unavailable references must not be ready');
    });

    await step('templates page at 320px has no horizontal overflow', [], async () => {
      await page.locator('nav [data-page=templates]').click();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      await page.setViewportSize({width: 320, height: 600});
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-320-templates.png'), fullPage: true});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert(overflow <= 1, `templates page overflows 320px by ${overflow}px`);
    });
    await step('resources page at 320px has no horizontal overflow', [], async () => {
      await page.locator('nav [data-page=resources]').click();
      await until(async () => await page.locator('button', {hasText: '＋ 新增 Provider'}).count());
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-320-resources.png'), fullPage: true});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert(overflow <= 1, `resources page overflows 320px by ${overflow}px`);
    });
    await step('projects page at 320px has no horizontal overflow', [], async () => {
      await page.locator('nav [data-page=projects]').click();
      await until(async () => await page.locator('#content label:has-text("Project 名称")').count());
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-320-projects.png'), fullPage: true});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert(overflow <= 1, `projects page overflows 320px by ${overflow}px`);
    });
    await step('concurrency panel at 320px has no horizontal overflow', [], async () => {
      await page.locator('nav [data-page=templates]').click();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      if (await page.locator('#concurrency-settings').isHidden())
        await clickButton('编辑并发上限');
      await until(async () => await page.locator('#concurrency-settings:visible').count());
      await page.setViewportSize({width: 320, height: 600});
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-320-concurrency.png'), fullPage: true});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert(overflow <= 1, `concurrency panel overflows 320px by ${overflow}px`);
      await page.setViewportSize({width: 1280, height: 800});
    });

    await step('desktop screenshots captured', [], async () => {
      await page.setViewportSize({width: 1280, height: 800});
      await page.locator('nav [data-page=templates]').click();
      await until(async () => await page.locator('#content label:has-text("模板名称")').count());
      await page.screenshot({path: path.join(VALIDATION, 'console-browser-templates.png'), fullPage: true});
    });
  } finally {
    if (browser) await browser.close().catch(() => {});
    child.kill('SIGTERM');
    await new Promise(resolve => child.on('exit', resolve));
    fs.rmSync(tmp, {recursive: true, force: true});
  }

  const passed = results.filter(r => r.status === 'pass').length;
  const failed = results.filter(r => r.status === 'fail');
  const skipped = results.filter(r => r.status === 'skip');
  console.log(`\nconsole browser: ${passed} passed, ${failed.length} failed, ${skipped.length} skipped`);
  for (const result of failed) console.log(`  FAIL ${result.name} :: ${result.detail}`);
  for (const result of skipped) console.log(`  SKIP ${result.name} :: ${result.detail}`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch(error => { console.error(error); process.exitCode = 1; });
