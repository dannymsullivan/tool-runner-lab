'use strict';

const $ = (id) => document.getElementById(id);
const staticLab = window.LAB_STATIC || (document.documentElement.dataset.hosting === 'pages' ? {} : null);
const recordedNotice = 'Recorded example from a real Tool Runner, Python, and SQLite run. Open a lab in Colab to execute and edit the code yourself.';
let config = null;
let selectedScenario = null;
let activeStateTab = 'orders';
let currentState = { orders: [], inventory: [], returns: [] };
let initialState = null;
let lastResult = null;
let running = false;
let checking = false;
let eventTotal = 0;
const eventIds = new Set();
let curriculum = null;
let currentStepId = 'loop';
let currentView = 'demo';
let completedSteps = new Set();
let checkpointResults = {};
let exerciseResults = {};
let exerciseMetadata = null;
let activeExercise = null;
let guidedRunStep = null;
let guidedWorkspaceOpen = false;
let checkpointBusy = false;
let exerciseBusy = false;
let connectBusy = false;
const visitedSteps = new Set();
let demoMode = 'fixture';
let demoResult = null;
let demoComparisonResult = null;
let demoLiveOpen = false;
let notebook = null;
let notebookIndex = 0;
let notebookChapter = 'intro';
let notebookModuleId = null;
let notebookBusy = false;
const notebookOutputs = new Map();
const notebookVariants = new Map();
let tutorialSeries = null;
let seriesModuleId = null;
let seriesBusy = false;
const modulePreviews = {
  'why-tool-runner': ['why'],
  'build-tools': ['inventory', 'schema', 'runner', 'compare', 'verify'],
  'verify-actions': ['errors', 'persistence'],
  'make-it-yours': ['trace', 'limits'],
};
const recordedInstructions = {
  inventory: 'Explore a recorded inventory lookup from the practice store. Predict what a count of zero means, then reveal the output.',
  schema: 'Inspect how the SDK described the inventory function to the model. Reveal the generated schema from the recorded run.',
  runner: 'Follow a recorded Tool Runner conversation. The SDK executed the Python tools and sent their results back to the scripted model.',
  compare: 'Compare a recorded run with replacement stock available. Notice how the inventory result changed the next action.',
  verify: 'Inspect the database state saved during the recorded run. The application checks this evidence separately from the final answer.',
  why: 'Compare two recordings of the same task: a manual loop and Tool Runner. Both used the same tools and store. Notice which bookkeeping the SDK handled.',
  trace: 'Choose a recorded stock setting and follow the messages and tool results, one model turn at a time.',
  limits: 'Compare recordings with two different turn limits. Notice whether the tools acted before the conversation stopped.',
  errors: 'Compare the outage and restored-service recordings. An unavailable inventory service is different from known zero stock.',
  persistence: 'Compare recordings with and without a real database write. A success-shaped tool result alone does not prove an action happened.',
};

const icons = { normal: '↩', unknown: '?', policy: '≡', outage: '⌁', duplicate: '⧉', eligibility: '◷' };
const fallbackNames = { normal: 'A damaged order', unknown: 'Unknown order', policy: 'Check the policy', outage: 'Inventory outage', duplicate: 'Duplicate return', eligibility: 'Outside the return window' };

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined && content !== null) node.textContent = String(content);
  return node;
}

function niceName(value) {
  return String(value).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function serializableText(value) {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function setStatus(text, type = '') {
  $('run-status').textContent = text;
  $('run-status').className = `status-badge ${type}`;
}

function showError(message) {
  $('run-error').textContent = message;
  $('run-error').hidden = false;
}

function toggleBusy(value) {
  running = value;
  $('run-button').disabled = value || !config || !selectedScenario;
  $('run-button').querySelector('span').textContent = value ? 'Running experiment…' : 'Run experiment';
  $('reset-button').disabled = value || (!lastResult && eventTotal === 0);
  $('checks-button').disabled = value || checking || !config;
  ['run-mode', 'stock-count', 'inventory-outage', 'missing-write', 'live-prompt'].forEach((id) => { $(id).disabled = value; });
  document.querySelectorAll('.scenario-button').forEach((button) => { button.disabled = value; });
  document.querySelectorAll('[data-run-scenario]').forEach((button) => { button.disabled = value || !config; });
  $('checkpoint-button').disabled = value || checkpointBusy || !curriculum;
  $('connect-button').disabled = value || connectBusy;
  ['api-key', 'disconnect-button', 'connected-model', 'start-live-button'].forEach((id) => { $(id).disabled = value; });
  syncDemoControls();
  syncNotebookControls();
}

function setMode() {
  const live = $('run-mode').value === 'live';
  $('request-text').hidden = live;
  $('live-prompt').hidden = !live;
  document.querySelector('label[for="live-prompt"]').hidden = !live;
  $('mode-description').textContent = live ? `Real Claude responses · ${config?.default_model || 'server-selected model'}. Uses your API account.` : 'Scripted model responses. Real SDK and tools.';
  document.querySelector('.bar-note').textContent = live ? 'Live Claude. Real SDK and tools.' : 'Scripted model. Real SDK and tools.';
  $('request-hint').textContent = live ? 'Edit the request and observe Claude’s decisions.' : 'Practice uses a fixed request for a repeatable run.';
  $('run-caption').textContent = live ? 'Live API usage. Fresh store data each run.' : 'Each run starts with fresh store data.';
}

function selectScenario(scenario) {
  if (running) return;
  selectedScenario = scenario;
  document.querySelectorAll('.scenario-button').forEach((button) => {
    const active = button.dataset.scenario === scenario.id;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  $('request-text').textContent = scenario.prompt || scenario.description || '';
  $('live-prompt').value = scenario.prompt || '';
  $('scenario-label').textContent = scenario.title || fallbackNames[scenario.id] || niceName(scenario.id);
  $('inventory-outage').checked = scenario.id === 'outage';
  setMode();
  if (lastResult) $('run-summary').textContent = 'Settings changed. The evidence below belongs to the previous run. Run again to compare.';
}

function renderScenarios(scenarios) {
  $('scenario-list').replaceChildren();
  for (const scenario of scenarios) {
    const button = element('button', 'scenario-button');
    button.type = 'button';
    button.dataset.scenario = scenario.id;
    button.title = scenario.description || scenario.title || scenario.id;
    button.setAttribute('aria-pressed', 'false');
    const icon = element('span', 'scenario-dot', icons[scenario.id] || '◇');
    icon.setAttribute('aria-hidden', 'true');
    const arrow = element('span', 'scenario-arrow', '›');
    arrow.setAttribute('aria-hidden', 'true');
    button.append(icon, element('span', '', scenario.title || fallbackNames[scenario.id] || niceName(scenario.id)), arrow);
    button.addEventListener('click', () => selectScenario(scenario));
    $('scenario-list').append(button);
  }
  if (scenarios.length) selectScenario(scenarios.find((scenario) => scenario.id === 'normal') || scenarios[0]);
}

function renderTools(tools) {
  $('tool-count').textContent = String(tools.length);
  $('tool-list').replaceChildren();
  for (const tool of tools) {
    const item = element('div', 'tool-item');
    item.append(element('code', '', tool.name), element('p', '', tool.description));
    const details = element('details');
    details.append(element('summary', '', 'Input schema'), element('pre', '', JSON.stringify(tool.input_schema || {}, null, 2)));
    item.append(details);
    $('tool-list').append(item);
  }
}

function normalizeRows(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.entries(value).map(([id, row]) => typeof row === 'object' && row !== null ? { id, ...row } : { item: id, count: row });
  return [];
}

function renderState(state, label) {
  if (state) currentState = { orders: normalizeRows(state.orders), inventory: normalizeRows(state.inventory), returns: normalizeRows(state.returns) };
  if (label) $('state-badge').textContent = label;
  for (const key of ['orders', 'inventory', 'returns']) $(key + '-count').textContent = currentState[key].length;
  $('state-json').textContent = JSON.stringify(currentState, null, 2);
  const panel = $('state-data');
  panel.replaceChildren();
  panel.setAttribute('aria-labelledby', activeStateTab + '-tab');
  const rows = currentState[activeStateTab];
  if (!rows.length) {
    const message = activeStateTab === 'returns' ? 'No return records.\nA successful return should appear here.' : !initialState && !lastResult ? 'Run an experiment to inspect the store data.' : `No ${activeStateTab === 'inventory' ? 'inventory' : 'order'} records.`;
    panel.append(element('div', 'state-empty', message));
    return;
  }
  rows.forEach((row, index) => {
    if (!row || typeof row !== 'object') row = { value: row };
    const card = element('article', 'record-card');
    const heading = element('div', 'record-heading');
    const primaryKey = ['return_id', 'order_id', 'product', 'item', 'sku', 'id', 'name'].find((key) => row[key] !== undefined);
    heading.append(element('span', '', primaryKey ? serializableText(row[primaryKey]) : `${niceName(activeStateTab).replace(/s$/, '')} ${index + 1}`));
    if (row.status) heading.append(element('span', 'record-status', niceName(row.status)));
    card.append(heading);
    const values = element('dl');
    Object.entries(row).filter(([key]) => key !== primaryKey && key !== 'status').forEach(([key, value]) => {
      const pair = element('div', 'record-row');
      pair.append(element('dt', '', niceName(key)), element('dd', '', serializableText(value)));
      values.append(pair);
    });
    card.append(values);
    panel.append(card);
  });
}

function renderChecks(checks) {
  const list = $('checks-list');
  list.replaceChildren();
  if (!checks?.length) {
    list.append(element('p', 'empty-copy', 'No verification results were returned.'));
    $('check-score').textContent = '—';
    $('check-score').className = 'check-score';
    return;
  }
  let passed = 0;
  for (const check of checks) {
    if (check.passed) passed += 1;
    const item = element('div', `check-item ${check.passed ? 'pass' : 'fail'}`);
    const indicator = element('span', 'check-indicator', check.passed ? '✓' : '!');
    indicator.setAttribute('aria-label', check.passed ? 'Passed' : 'Failed');
    const copy = element('div');
    copy.append(element('strong', '', check.name || 'State check'));
    if (check.detail) copy.append(element('p', '', serializableText(check.detail)));
    item.append(indicator, copy);
    list.append(item);
  }
  $('check-score').textContent = `${passed} / ${checks.length} passed`;
  $('check-score').className = `check-score ${passed === checks.length ? 'pass' : 'fail'}`;
  $('checks-note').textContent = passed === checks.length ? 'Checks passed within this lab’s test coverage.' : 'A claim and the underlying evidence do not agree.';
}

function classifyEvent(type) {
  const value = String(type || '').toLowerCase();
  if (/error|fail|denied/.test(value)) return ['error', '!'];
  if (/check|verify|verification/.test(value)) return ['verification', '✓'];
  if (/tool/.test(value)) return ['tool', '⌘'];
  if (/model|assistant|message/.test(value)) return ['model', '✳'];
  if (/state|return|database/.test(value)) return ['verification', '↳'];
  return ['', '·'];
}

function addEvent(event) {
  if (event.type === 'verification') return;
  if (event.id && eventIds.has(event.id)) return;
  if (event.id) eventIds.add(event.id);
  eventTotal += 1;
  $('trace-empty').hidden = true;
  $('event-count').textContent = `${eventTotal} event${eventTotal === 1 ? '' : 's'}`;
  const [kind, symbol] = classifyEvent(event.type);
  const trace = $('event-list');
  const followTrace = trace.scrollHeight - trace.scrollTop - trace.clientHeight < 60;
  const item = element('li', `event-item ${kind}`);
  const icon = element('span', 'event-icon', symbol);
  icon.setAttribute('aria-hidden', 'true');
  const body = element('div', 'event-body');
  const heading = element('div', 'event-topline');
  heading.append(element('strong', '', event.title || niceName(event.type || 'Event')));
  if (Number.isFinite(event.elapsed_ms)) heading.append(element('span', 'event-time', event.elapsed_ms < 1000 ? `${Math.round(event.elapsed_ms)} ms` : `${(event.elapsed_ms / 1000).toFixed(1)} s`));
  body.append(heading);
  if (event.detail) body.append(element('p', 'event-detail', serializableText(event.detail)));
  if (event.data !== undefined && event.data !== null) {
    const details = element('details', 'event-payload');
    details.append(element('summary', '', 'Inspect data'), element('pre', '', JSON.stringify(event.data, null, 2)));
    body.append(details);
  }
  item.append(icon, body);
  trace.append(item);
  if (followTrace) trace.scrollTop = trace.scrollHeight;
  const liveState = event.data?.state;
  if (liveState && typeof liveState === 'object') renderState(liveState, 'During run');
}

function renderMetrics(metrics) {
  $('metrics').replaceChildren();
  const values = [
    [metrics?.tool_calls, 'tool calls'],
    [metrics?.assistant_messages, 'model messages'],
    [metrics?.errors, 'tool errors'],
    [Number.isFinite(metrics?.duration_ms) ? `${(metrics.duration_ms / 1000).toFixed(2)}s` : null, 'elapsed'],
  ];
  if (lastResult?.mode === 'live') values.push([metrics?.input_tokens, 'input tokens'], [metrics?.output_tokens, 'output tokens']);
  values.filter(([value]) => value !== undefined && value !== null).forEach(([value, label]) => {
    const item = element('span');
    item.append(element('strong', '', value), document.createTextNode(' ' + label));
    $('metrics').append(item);
  });
}

function finishRun(result) {
  lastResult = result;
  if (eventTotal === 0 && Array.isArray(result.events)) result.events.forEach(addEvent);
  if (result.state) renderState(result.state, 'After run');
  $('state-help').textContent = 'Actual store records after tool execution.';
  renderChecks(result.checks || []);
  $('answer-card').hidden = false;
  $('answer-text').textContent = result.answer || 'The run ended without a final customer-facing answer.';
  $('answer-mode').textContent = result.mode === 'live' ? 'Live Claude' : 'Scripted model';
  renderMetrics(result.metrics);
  const completed = result.completed !== false;
  const verificationFailed = (result.checks || []).some((check) => !check.passed);
  setStatus(verificationFailed ? 'Verification failed' : completed ? 'Complete' : 'Stopped', verificationFailed || !completed ? 'error' : 'complete');
  const stop = result.stop_reason ? ` · ${niceName(result.stop_reason)}` : '';
  const verificationNote = verificationFailed ? 'The model finished, but the task failed verification. Inspect the failed checks and actual store records.' : 'Inspect the trace and check the state.';
  $('run-summary').textContent = `${result.mode === 'live' ? 'Live Claude run' : 'Practice run: scripted model, real tools'}${stop}. ${verificationNote}`;
  $('download-button').hidden = false;
  if (guidedRunStep) {
    if (currentStepId === guidedRunStep) {
      const passed = (result.checks || []).filter((check) => check.passed).length;
      $('step-run-status').textContent = `Run saved. ${passed} of ${(result.checks || []).length} checks passed. Inspect the workspace, then check this step.`;
    }
    guidedRunStep = null;
  }
}

function resetEvidence() {
  lastResult = null;
  eventTotal = 0;
  eventIds.clear();
  $('event-list').replaceChildren();
  $('trace-empty').hidden = false;
  $('event-count').textContent = 'Waiting for a run';
  $('answer-card').hidden = true;
  $('run-error').hidden = true;
  $('run-summary').textContent = '';
  $('download-button').hidden = true;
  $('check-score').textContent = '—';
  $('check-score').className = 'check-score';
  $('checks-list').replaceChildren(element('p', 'empty-copy', 'Run the agent to check its answer against store records.'));
  $('checks-note').textContent = 'Run an experiment first.';
  $('state-help').textContent = 'The source of truth behind the conversation.';
  renderState(initialState || { orders: [], inventory: [], returns: [] }, 'Before run');
  setStatus('Ready');
}

async function responseError(response) {
  try {
    const data = await response.json();
    return data.error || data.detail || `Request failed (${response.status}).`;
  } catch {
    return `Request failed (${response.status}). Check the local server and try again.`;
  }
}

// Both the opening demo and the detailed workbench consume the same real run.
async function streamRun(payload, onEvent) {
  if (staticLab) {
    if (payload.mode !== 'fixture' || payload.scenario !== 'normal' || payload.inventory_outage || payload.simulate_missing_write || ![0, 3].includes(payload.inventory_count)) {
      throw new Error('This recording covers the opening example. Open a Colab lab to run your own experiments.');
    }
    const recording = staticLab.runs[String(payload.inventory_count)];
    if (!recording) throw new Error('This recorded example is unavailable.');
    const result = structuredClone(recording);
    (result.events || []).forEach(onEvent);
    return result;
  }
  const response = await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error(await responseError(response));
  if (!response.body) throw new Error('Your browser cannot read the event stream. Try a current browser.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  let result = null;
  const accept = (line) => {
    if (!line.trim()) return;
    const message = JSON.parse(line);
    if (message.kind === 'event' && message.event) onEvent(message.event);
    else if (message.kind === 'result' && message.result) result = message.result;
    else if (message.kind === 'error') throw new Error(message.error || 'The run failed.');
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lines = pending.split('\n');
      pending = lines.pop() || '';
      lines.forEach(accept);
      if (done) break;
    }
    accept(pending);
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  if (!result) throw new Error('The connection ended before a final result arrived. Try again.');
  return result;
}

async function runExperiment() {
  if (running || !selectedScenario || !config) return;
  const mode = $('run-mode').value;
  const prompt = $('live-prompt').value.trim();
  if (mode === 'live' && !prompt) { showError('Enter a customer request before starting Live Claude.'); $('live-prompt').focus(); return; }
  resetEvidence();
  toggleBusy(true);
  setStatus('Running', 'running');
  $('run-summary').textContent = mode === 'live' ? 'Waiting for Claude. Events will appear as the server sends them.' : 'Running a repeatable scenario against the real tool implementations.';
  const payload = {
    scenario: selectedScenario.id,
    mode,
    inventory_count: Number($('stock-count').value),
    inventory_outage: $('inventory-outage').checked,
    simulate_missing_write: $('missing-write').checked,
    max_iterations: 6,
    error_threshold: 2,
  };
  if (mode === 'live') payload.prompt = prompt;
  try {
    finishRun(await streamRun(payload, addEvent));
  } catch (error) {
    setStatus('Run failed', 'error');
    showError(error.message || 'Unable to run the experiment.');
    $('run-summary').textContent = 'The run did not finish successfully. Review the error and try again.';
  } finally {
    toggleBusy(false);
  }
}

async function runChecks() {
  if (!config || checking || running) return;
  checking = true;
  $('checks-button').disabled = true;
  $('checks-button').querySelector('span').textContent = 'Checking…';
  try {
    const response = await fetch('/api/checks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    if (!response.ok) throw new Error(await responseError(response));
    const data = await response.json();
    if (!Array.isArray(data.checks)) throw new Error('The server returned no verification results.');
    $('suite-list').replaceChildren();
    const passed = data.checks.filter((check) => check.passed).length;
    $('suite-summary').textContent = `Test suite: ${passed} / ${data.checks.length} passed`;
    $('suite-results').hidden = false;
    $('suite-results').open = true;
    $('suite-status').textContent = 'Local tool contract tests. These results are separate from the current experiment.';
    data.checks.forEach((check) => {
      const item = element('div', `check-item ${check.passed ? 'pass' : 'fail'}`);
      const indicator = element('span', 'check-indicator', check.passed ? '✓' : '!');
      indicator.setAttribute('aria-label', check.passed ? 'Passed' : 'Failed');
      const copy = element('div');
      copy.append(element('strong', '', check.name || 'Test'));
      if (check.detail) copy.append(element('p', '', check.detail));
      item.append(indicator, copy);
      $('suite-list').append(item);
    });
  } catch (error) {
    $('suite-status').textContent = error.message || 'Unable to run the test suite.';
  } finally {
    checking = false;
    $('checks-button').disabled = running || !config;
    $('checks-button').querySelector('span').textContent = 'Run test suite';
  }
}

function selectView(view) {
  if (staticLab && !['demo', 'notebook', 'series'].includes(view)) view = 'series';
  currentView = view;
  document.body.dataset.view = view;
  const focused = view === 'demo' || view === 'notebook' || view === 'series';
  $('demo-view').hidden = view !== 'demo';
  $('notebook-view').hidden = view !== 'notebook';
  $('series-view').hidden = view !== 'series';
  $('course-intro').hidden = focused;
  $('course-navigation').hidden = focused;
  $('home-demo').hidden = focused;
  $('open-lessons').hidden = view !== 'demo';
  $('guided-view').hidden = view !== 'guided';
  $('explore-view').hidden = view !== 'explore' && !(view === 'guided' && guidedWorkspaceOpen);
  $('guided-workspace-banner').hidden = view !== 'guided' || !guidedWorkspaceOpen;
  $('how-view').hidden = view !== 'how';
  const connection = $('live-extension');
  if (view === 'demo') $('demo-mode-note').after(connection);
  else $('explore-view').before(connection);
  connection.hidden = view === 'demo' ? !demoLiveOpen : view === 'notebook' || view === 'series' || view === 'how' || (view === 'guided' && currentStepId !== 'capstone');
  if (staticLab) connection.hidden = true;
  $('start-live-button').textContent = view === 'demo' ? 'Use live Claude →' : 'Open live playground →';
  if (view === 'demo') requestAnimationFrame(resizeDemoPrompt);
  [['guided-tab', view === 'guided'], ['explore-tab', view === 'explore'], ['how-tab', view === 'how']].forEach(([id, active]) => {
    $(id).classList.toggle('active', active);
    $(id).setAttribute('aria-selected', String(active));
  });
}

async function loadConfig() {
  try {
    config = await jsonRequest('/api/config');
    if (!Array.isArray(config.scenarios) || !config.scenarios.length) throw new Error('No scenarios are configured for this lab.');
    initialState = config.initial_state || null;
    renderScenarios(config.scenarios);
    renderTools(config.tools || []);
    renderState(initialState || currentState, 'Before run');
    configureLiveAccess(config.models || []);
    configureSourceLinks();
    $('demo-prompt').value = demoScenario()?.prompt || '';
    refreshDemoMode();
    toggleBusy(false);
  } catch (error) {
    config = null;
    $('demo-status').textContent = 'The lab server is unavailable. Start it, then reload this page.';
    syncDemoControls();
    showError(error.message || 'Unable to load the lab configuration.');
    $('scenario-list').replaceChildren(element('p', 'empty-copy', 'The lab server is unavailable. Start it, then reload this page.'));
    $('request-text').textContent = 'Once the server is ready, choose a customer scenario and run the experiment.';
    setStatus('Unavailable', 'error');
  }
}

$('run-button').addEventListener('click', runExperiment);
$('checks-button').addEventListener('click', runChecks);
$('reset-button').addEventListener('click', () => { if (!running) { resetEvidence(); toggleBusy(false); } });
$('run-mode').addEventListener('change', () => { setMode(); if (lastResult) $('run-summary').textContent = 'Mode changed. Run again to create new evidence.'; });
$('stock-count').addEventListener('input', () => { $('stock-value').value = $('stock-count').value; });
['stock-count', 'inventory-outage', 'missing-write'].forEach((id) => $(id).addEventListener('change', () => { if (lastResult) $('run-summary').textContent = 'Conditions changed. The results shown are from the previous run. Run again to compare.'; }));
$('explore-tab').addEventListener('click', () => selectView('explore'));
$('guided-tab').addEventListener('click', () => selectView('guided'));
$('how-tab').addEventListener('click', () => selectView('how'));
document.querySelectorAll('[data-state]').forEach((button) => button.addEventListener('click', () => {
  activeStateTab = button.dataset.state;
  document.querySelectorAll('[data-state]').forEach((tab) => { const active = tab === button; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); });
  renderState();
}));
$('download-button').addEventListener('click', () => {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastResult, null, 2) + '\n'], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = element('a');
  link.href = url;
  link.download = `support-agent-run-${String(lastResult.run_id || Date.now()).replace(/[^a-zA-Z0-9_-]/g, '')}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
initialize();

async function jsonRequest(url, options = {}) {
  if (staticLab) {
    let value;
    if (url === '/api/config') value = staticLab.config;
    else if (url === '/api/notebook') value = staticLab.notebook;
    else if (url === '/api/tutorials') value = staticLab.tutorials;
    else if (url === '/api/notebook-cell' && options.method === 'POST') {
      const request = JSON.parse(options.body || '{}');
      value = staticLab.cells[request.variant ? `${request.cell_id}:${request.variant}` : request.cell_id];
    }
    if (!value) throw new Error('Open a Colab lab to run this feature.');
    return structuredClone(value);
  }
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
}

function postJSON(url, data) {
  return jsonRequest(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
}

function rememberLocation() {
  try { localStorage.setItem('tool-runner-lab-navigation', JSON.stringify({ current: currentStepId, visited: [...visitedSteps] })); } catch { /* Browser storage is optional; no completion authority lives here. */ }
}

function restoreLocation() {
  try {
    const saved = JSON.parse(localStorage.getItem('tool-runner-lab-navigation') || '{}');
    if (typeof saved.current === 'string') currentStepId = saved.current;
    if (Array.isArray(saved.visited)) saved.visited.forEach((id) => visitedSteps.add(id));
  } catch { /* A new session can start without saved navigation. */ }
}

function stepTitle(step) { return String(step.title || step.id).replace(/^\d+\.\s*/, ''); }
function currentStep() { return curriculum?.steps?.find((step) => step.id === currentStepId); }

function renderProgress() {
  if (!curriculum) return;
  const total = curriculum.steps.length;
  const checked = curriculum.steps.filter((step) => completedSteps.has(step.id)).length;
  $('progress-label').textContent = `${checked} of ${total} steps checked`;
  $('progress-fill').style.width = `${total ? (checked / total) * 100 : 0}%`;
  $('step-list').replaceChildren();
  curriculum.steps.forEach((step, index) => {
    const complete = completedSteps.has(step.id);
    const button = element('button', `step-link${step.id === currentStepId ? ' active' : ''}${complete ? ' complete' : ''}`);
    button.type = 'button';
    if (step.id === currentStepId) button.setAttribute('aria-current', 'step');
    button.setAttribute('aria-label', `Step ${index + 1}: ${stepTitle(step)}${complete ? ', checked' : ''}`);
    const marker = element('span', 'step-link-number', complete ? '✓' : String(index + 1));
    marker.setAttribute('aria-hidden', 'true');
    button.append(marker, element('span', 'step-link-title', stepTitle(step)));
    button.addEventListener('click', () => { showStep(step.id); });
    $('step-list').append(button);
  });
  $('progress-note').textContent = checked === total ? 'All seven steps passed checks in this server session. Export your learning report to keep the evidence.' : 'Progress comes from checked runs and code in this server session. Your browser only remembers where you were.';
}

async function syncReport() {
  const report = await jsonRequest('/api/report');
  completedSteps = new Set(Array.isArray(report.completed_steps) ? report.completed_steps : []);
  checkpointResults = report.checkpoints || {};
  exerciseResults = report.exercise_results || {};
  renderProgress();
  updateStepState();
  return report;
}

function updateStepState() {
  const complete = completedSteps.has(currentStepId);
  $('step-state').textContent = complete ? 'Checked & complete' : 'Ready to explore';
  $('step-state').className = `step-state${complete ? ' complete' : ''}`;
}

function showStep(id) {
  if (!curriculum?.steps.some((step) => step.id === id)) return;
  currentStepId = id;
  visitedSteps.add(id);
  rememberLocation();
  guidedWorkspaceOpen = false;
  selectView('guided');
  renderLesson();
}

function addGuidedAction(label, scenario, fault = false) {
  const button = element('button', $('step-actions').children.length ? 'secondary-button' : 'primary-button', label);
  button.type = 'button';
  button.dataset.runScenario = scenario;
  button.disabled = running || !config;
  button.addEventListener('click', () => runGuidedScenario(scenario, fault));
  $('step-actions').append(button);
}

function renderLesson() {
  const step = currentStep();
  if (!step) return;
  const index = curriculum.steps.findIndex((item) => item.id === step.id);
  $('step-kicker').textContent = `STEP ${index + 1} OF ${curriculum.steps.length}`;
  $('guided-heading').textContent = stepTitle(step);
  $('step-objective').textContent = step.objective || '';
  $('step-why').textContent = step.why || '';
  $('step-instructions').textContent = step.instruction || '';
  $('step-run-status').textContent = '';
  $('step-actions').replaceChildren();
  $('checkpoint-results').replaceChildren();
  $('checkpoint-button').disabled = running || checkpointBusy;
  $('checkpoint-hint').textContent = step.quiz ? 'Choose your answer, then check the saved run evidence.' : 'Complete the activity, then check the saved evidence.';
  $('previous-step').disabled = index === 0;
  $('next-step').disabled = index === curriculum.steps.length - 1;
  $('exercise-panel').hidden = step.action?.type !== 'exercise';
  activeExercise = step.action?.exercise || null;
  exerciseMetadata = null;
  if (step.action?.type === 'fault') {
    addGuidedAction('Run with the broken write', step.action.scenario || 'normal', true);
    addGuidedAction('Restore the write and run again', step.action.scenario || 'normal', false);
  } else if (step.action?.type === 'run') {
    const scenarios = step.action.scenarios || [step.action.scenario || 'normal'];
    scenarios.forEach((id) => {
      const title = config?.scenarios.find((item) => item.id === id)?.title || niceName(id);
      addGuidedAction(scenarios.length === 1 ? step.action.label || 'Run the example' : `Run: ${title}`, id);
    });
  }
  if (step.action?.type === 'exercise') {
    $('copy-path-button').textContent = 'Copy file path';
    $('exercise-file').textContent = `exercises/${activeExercise === 'inventory' ? 'inventory_tool.py' : 'runner_exercise.py'}`;
    $('exercise-starter').textContent = 'Loading the workspace file…';
    $('exercise-instructions').textContent = '';
    $('exercise-feedback').replaceChildren();
    $('exercise-solution').textContent = '';
    $('solution-panel').hidden = true;
    $('exercise-check-button').disabled = true;
    $('reveal-solution-button').disabled = true;
    loadExercise(activeExercise);
  }
  $('quiz-options').replaceChildren();
  $('step-quiz').hidden = !step.quiz;
  if (step.quiz) {
    $('quiz-question').textContent = step.quiz.question;
    step.quiz.options.forEach((option) => {
      const label = element('label', 'quiz-option');
      const input = element('input');
      input.type = 'radio'; input.name = `quiz-${step.id}`; input.value = option.value;
      label.append(input, element('span', '', option.label));
      $('quiz-options').append(label);
    });
  }
  if (checkpointResults[step.id]) displayValidation($('checkpoint-results'), checkpointResults[step.id]);
  updateStepState(); renderProgress();
  $('live-extension').hidden = currentView === 'demo' ? !demoLiveOpen : currentView !== 'explore' && step.id !== 'capstone';
}

async function runGuidedScenario(id, missingWrite) {
  if (running || !config) return;
  const scenario = config.scenarios.find((item) => item.id === id);
  if (!scenario) { $('step-run-status').textContent = 'This example is not available. Reload the lab and try again.'; return; }
  selectScenario(scenario);
  $('run-mode').value = 'fixture'; $('stock-count').value = '0'; $('stock-value').value = '0';
  $('inventory-outage').checked = id === 'outage' || id === 'batch_errors';
  $('missing-write').checked = missingWrite; setMode();
  guidedRunStep = currentStepId;
  guidedWorkspaceOpen = true;
  $('workspace-lesson-title').textContent = stepTitle(currentStep());
  $('step-run-status').textContent = 'Running the example. Follow the trace in the workspace below.';
  selectView('guided');
  $('explore-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
  await runExperiment();
  if (guidedRunStep) {
    $('step-run-status').textContent = $('run-error').textContent || 'The run did not finish. Try again.';
    guidedRunStep = null;
  }
}

function displayValidation(container, result) {
  container.replaceChildren();
  container.append(element('p', `checkpoint-feedback${result.passed ? '' : ' fail'}`, result.feedback || (result.passed ? 'All checks passed.' : 'Review the checks below, then try again.')));
  for (const check of result.checks || []) {
    const item = element('div', `check-item ${check.passed ? 'pass' : 'fail'}`);
    const indicator = element('span', 'check-indicator', check.passed ? '✓' : '!');
    indicator.setAttribute('aria-label', check.passed ? 'Passed' : 'Failed');
    const text = element('div');
    text.append(element('strong', '', check.name || 'Check'));
    if (check.detail) text.append(element('p', '', check.detail));
    item.append(indicator, text);
    container.append(item);
  }
  if (result.schema) {
    const details = element('details', 'exercise-code');
    details.append(element('summary', '', 'Inspect the generated tool schema'), element('pre', '', JSON.stringify(result.schema, null, 2)));
    container.append(details);
  }
}

async function checkCheckpoint() {
  const step = currentStep();
  if (!step || checkpointBusy || running) return;
  const answers = {};
  if (step.quiz) {
    const chosen = $('quiz-options').querySelector('input:checked');
    if (chosen) answers[step.quiz.key] = chosen.value;
  }
  checkpointBusy = true;
  const checkedStep = step.id;
  $('checkpoint-button').disabled = true;
  $('checkpoint-button').textContent = 'Checking your evidence…';
  try {
    const result = await postJSON('/api/checkpoint', { step_id: checkedStep, answers });
    checkpointResults[checkedStep] = result;
    if (result.passed) completedSteps.add(checkedStep);
    else completedSteps.delete(checkedStep);
    if (currentStepId === checkedStep) displayValidation($('checkpoint-results'), result);
    updateStepState(); renderProgress();
    try { await syncReport(); } catch { /* The checkpoint response is authoritative even if report refresh fails. */ }
  } catch (error) {
    if (currentStepId === checkedStep) displayValidation($('checkpoint-results'), { passed: false, feedback: error.message, checks: [] });
  } finally {
    checkpointBusy = false;
    $('checkpoint-button').disabled = running;
    $('checkpoint-button').textContent = 'Check this step →';
  }
}

async function loadExercise(exercise) {
  try {
    const data = await jsonRequest(`/api/exercise?exercise=${encodeURIComponent(exercise)}`);
    if (activeExercise !== exercise) return;
    exerciseMetadata = data;
    $('exercise-file').textContent = data.file || `exercises/${exercise === 'inventory' ? 'inventory_tool.py' : 'runner_exercise.py'}`;
    $('exercise-instructions').textContent = Array.isArray(data.instructions) ? data.instructions.join('\n') : data.instructions || '';
    $('exercise-starter').textContent = data.starter || 'No starter content was returned.';
    $('exercise-check-button').disabled = exerciseBusy;
    $('reveal-solution-button').disabled = !data.solution;
    configureExerciseSource();
    if (exerciseResults[exercise]) displayValidation($('exercise-feedback'), exerciseResults[exercise]);
  } catch (error) {
    if (activeExercise === exercise) $('exercise-feedback').textContent = error.message;
  }
}

async function checkExercise() {
  if (!activeExercise || exerciseBusy) return;
  const exercise = activeExercise;
  exerciseBusy = true;
  $('exercise-check-button').disabled = true;
  $('exercise-check-button').textContent = 'Checking saved code…';
  try {
    const result = await postJSON('/api/exercise', { exercise });
    exerciseResults[exercise] = result;
    if (activeExercise === exercise) displayValidation($('exercise-feedback'), result);
    await loadExercise(exercise);
  } catch (error) {
    if (activeExercise === exercise) displayValidation($('exercise-feedback'), { passed: false, feedback: error.message, checks: [] });
  } finally {
    exerciseBusy = false;
    $('exercise-check-button').disabled = false;
    $('exercise-check-button').textContent = 'Check my code';
  }
}

function safeWebURL(value) {
  try { const url = new URL(value); return url.protocol === 'https:' ? url : null; } catch { return null; }
}

function safeColabURL(value) {
  const url = safeWebURL(value);
  return url && url.hostname === 'colab.research.google.com' && !url.username && !url.password && !url.port && !url.search && !url.hash && /^\/github\/[A-Za-z0-9-]+\/[A-Za-z0-9._-]+\/blob\/.+\/tutorials\/[a-z0-9.-]+\.ipynb$/.test(url.pathname) ? url.href : null;
}

function configureNotebookLink(id, downloadPath, colabURL, label) {
  const link = $(id);
  const colab = safeColabURL(colabURL);
  link.href = colab || (staticLab ? `.${downloadPath}` : downloadPath);
  link.textContent = colab ? label : id === 'series-download' ? 'Download for Colab ↓' : 'Download the complete reference ↓';
  if (colab) {
    link.removeAttribute('download');
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  } else {
    link.download = downloadPath.split('/').pop();
    link.removeAttribute('target');
    link.removeAttribute('rel');
  }
  return Boolean(colab);
}

function configureSourceLinks() {
  const source = safeWebURL(config?.source_url);
  $('source-link').href = source ? source.href : 'https://github.com/codespaces';
  $('source-link').textContent = `${config?.source_label || (source ? 'Open source in Codespaces' : 'Open Codespaces')} ↗`;
  $('source-note').textContent = config?.source_notice || (config?.editor_url ? 'Edit the actual Python files in your workspace.' : 'Upload this lab to a repository to open its source.');
}

function configureExerciseSource() {
  const base = safeWebURL(config?.editor_url);
  $('exercise-source-link').hidden = !base;
  if (!base) return;
  $('exercise-source-link').href = base.href;
}

function configureLiveAccess(models = []) {
  const available = Boolean(config?.live_available);
  $('run-mode').querySelector('[value="live"]').disabled = !available;
  $('run-mode').hidden = !available;
  $('practice-chip').hidden = available;
  if (!available) $('run-mode').value = 'fixture';
  $('connected-settings').hidden = !available;
  $('connect-form').hidden = available;
  if (available) {
    $('connected-model').replaceChildren();
    const choices = models.length ? models : [{ id: config.default_model, display_name: config.default_model }];
    choices.forEach((model) => {
      const option = element('option', '', model.display_name || model.id);
      option.value = model.id;
      $('connected-model').append(option);
    });
    if (choices.some((model) => model.id === config.default_model)) $('connected-model').value = config.default_model;
    $('connection-status').textContent = 'Live Claude is available. API usage is billed to the connected account.';
  }
  setMode();
  if (!available && demoMode === 'live') {
    demoMode = 'fixture';
    resetDemo();
  }
  refreshDemoMode();
}

async function connectClaude(event) {
  event.preventDefault();
  if (connectBusy || running) return;
  const key = $('api-key').value.trim();
  if (!key) { $('connection-status').textContent = 'Enter an Anthropic API key to connect.'; $('api-key').focus(); return; }
  $('api-key').value = '';
  connectBusy = true;
  $('connect-button').disabled = true;
  $('connection-status').textContent = 'Validating the key with Anthropic. No model request is sent.';
  try {
    const result = await postJSON('/api/connect', { api_key: key });
    if (!result.connected) throw new Error('The connection was not confirmed.');
    config.live_available = true;
    config.default_model = result.model || config.default_model;
    configureLiveAccess(result.models || []);
  } catch (error) {
    $('connection-status').textContent = String(error.message || 'Unable to connect.').split(key).join('[redacted]');
  } finally {
    connectBusy = false;
    $('connect-button').disabled = false;
  }
}

async function disconnectClaude() {
  if (running) return;
  $('disconnect-button').disabled = true;
  try {
    await postJSON('/api/disconnect', {});
    const refreshed = await jsonRequest('/api/config');
    config.live_available = refreshed.live_available;
    config.default_model = refreshed.default_model;
    configureLiveAccess();
    $('connection-status').textContent = refreshed.live_available ? 'Session key removed. A server-provided connection remains available.' : 'Session key removed. Practice mode remains available.';
  } catch (error) {
    $('connection-status').textContent = error.message || 'Unable to disconnect.';
  } finally { $('disconnect-button').disabled = false; }
}

async function changeLiveModel() {
  if (running) return;
  const model = $('connected-model').value;
  $('connected-model').disabled = true;
  try {
    const result = await postJSON('/api/model', { model });
    config.default_model = result.model || model;
    setMode();
    $('connection-status').textContent = `Live model selected: ${config.default_model}.`;
  } catch (error) {
    $('connected-model').value = config.default_model;
    $('connection-status').textContent = error.message || 'Unable to change model.';
  } finally { $('connected-model').disabled = false; }
}

function downloadJSON(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = element('a'); link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function demoScenario() { return config?.scenarios?.find((scenario) => scenario.id === 'normal'); }

function syncDemoControls() {
  const ready = Boolean(demoScenario());
  $('demo-send').disabled = running || !ready;
  $('demo-prompt').disabled = running || !ready;
  $('demo-test').disabled = running || !demoResult || !document.querySelector('input[name="demo-prediction"]:checked');
  document.querySelectorAll('input[name="demo-prediction"]').forEach((input) => { input.disabled = running; });
  ['demo-mode-switch', 'demo-connect', 'demo-restart', 'demo-build', 'demo-explain', 'home-demo', 'open-lessons'].forEach((id) => { $(id).disabled = running; });
  ['demo-connect', 'demo-mode-switch'].forEach((id) => { $(id).disabled = running || !config; });
}

function refreshDemoMode() {
  const live = demoMode === 'live';
  $('demo-prompt').readOnly = !live || Boolean(demoResult);
  document.querySelector('label[for="demo-prompt"]').textContent = live ? 'Your request' : 'Try this request';
  $('demo-mode-label').textContent = live ? 'Live Claude' : 'Practice · no key';
  $('demo-mode-note').textContent = live
    ? 'Real Claude responses and real Python tools. Sending a request uses your Claude API billing. Each request starts with a fresh training store.'
    : 'Scripted model replies. Real Tool Runner, Python tools, and database writes. No API key needed.';
  $('demo-mode-switch').hidden = !config?.live_available;
  $('demo-mode-switch').textContent = live ? 'Switch to free practice' : 'Use live Claude';
  $('demo-connect').textContent = config?.live_available ? 'Claude connection' : 'Use your Claude API key';
  $('demo-send').textContent = live ? 'Send to Claude →' : 'Send request →';
  if (staticLab) {
    $('demo-mode-label').textContent = 'Recorded demonstration';
    $('demo-mode-note').textContent = recordedNotice;
    $('demo-connect').hidden = true;
    $('demo-mode-switch').hidden = true;
    $('demo-send').textContent = 'Explore the example →';
    document.querySelector('.demo-intro > p:last-child').textContent = 'Explore a support request, then build the code behind it in Colab.';
    document.querySelector('.demo-code > p').textContent = 'This website replays recorded results. The Colab labs execute the Python shown here.';
    document.querySelector('.series-preview-note').textContent = 'The browser shows recorded examples. In Colab, the bot runs the functions you edit.';
  }
  syncDemoControls();
  requestAnimationFrame(resizeDemoPrompt);
}

function resizeDemoPrompt() {
  if (currentView !== 'demo') return;
  const prompt = $('demo-prompt');
  prompt.style.height = 'auto';
  prompt.style.height = `${Math.max(122, prompt.scrollHeight + 2)}px`;
}

function resetDemo() {
  demoResult = null;
  demoComparisonResult = null;
  ['demo-reply', 'demo-next', 'demo-breakdown', 'demo-prediction', 'demo-comparison', 'demo-restart'].forEach((id) => { $(id).hidden = true; });
  $('demo-form').hidden = false;
  $('demo-send').hidden = false;
  $('demo-comparison').querySelector('.demo-build-next').prepend($('demo-build'));
  $('demo-prompt').value = demoScenario()?.prompt || '';
  $('demo-status').textContent = '';
  $('demo-prediction-status').textContent = '';
  $('demo-prediction-form').reset();
  $('demo-prediction-form').hidden = false;
  syncDemoControls();
}

function setDemoMode(mode) {
  if (running || (mode === 'live' && !config?.live_available)) return;
  demoMode = mode;
  resetDemo();
  refreshDemoMode();
}

function demoOutcome(result) {
  // The proof comes from the stored state, never from a success-shaped reply.
  const returns = result.state?.returns;
  if (!Array.isArray(returns)) return 'The server did not return store records.';
  if (!returns.length) return staticLab ? 'Recorded outcome: no return was created.' : 'No return created in the training store.';
  return `${staticLab ? 'Recorded outcome: ' : ''}${returns.map((row) => `Return ${row.return_id} for ${row.order_id}`).join('; ')} saved in the training store.`;
}

function verifiedRun(result) {
  return result.completed === true && Array.isArray(result.checks) && result.checks.length > 0 && result.checks.every((check) => check.passed);
}

function describeDemoTool(event) {
  const data = event.data || {};
  const result = data.result || {};
  if (data.is_error) return ['The tool reported a problem', typeof result === 'string' ? result : JSON.stringify(result)];
  if (data.name === 'lookup_order') return ['Look up the order', `${result.order_id}: ${result.status}. Expected delivery: ${result.eta}.`];
  if (data.name === 'check_inventory') return ['Check replacement stock', `${result.product}: ${result.count} available.`];
  if (data.name === 'start_return') {
    const saved = event.data.state?.returns?.some((row) => row.return_id === result.return_id);
    return ['Start the requested return', saved ? `${result.return_id} was saved for order ${result.order_id}.` : 'The tool reported success, but no matching record was saved.'];
  }
  if (data.name === 'check_return_eligibility') return ['Check the return policy', `${result.order_id}: ${result.eligible ? 'eligible' : 'not eligible'} for a return. This check does not create one.`];
  return [niceName(data.name || 'Python tool'), serializableText(result)];
}

function explainDemo() {
  if (!demoResult || running) return;
  const calls = (demoResult.events || []).filter((event) => event.type === 'tool_result');
  $('demo-tool-story').replaceChildren();
  for (const event of calls) {
    const item = element('li');
    const [title, description] = describeDemoTool(event);
    const copy = element('div');
    copy.append(element('strong', '', title), element('p', '', description), element('code', '', event.data.name));
    item.append(copy);
    $('demo-tool-story').append(item);
  }
  if (!calls.length) $('demo-tool-story').append(element('li', '', 'This response did not execute a Python tool. Try asking about an order to see tool use.'));
  const turnCount = new Set(calls.map((event) => event.data.assistant_turn)).size;
  $('demo-runner-summary').textContent = calls.length
    ? `${calls.length} tool calls across ${turnCount} tool-use turn${turnCount === 1 ? '' : 's'}. Tool Runner passed the results back and continued the conversation. You could write that loop yourself; the SDK handles the repeated work. Your Python functions still own the return policy and database writes.${demoResult.mode === 'fixture' ? ' In practice, scripted requests stand in for Claude’s decisions.' : ''}`
    : 'Tool Runner also handles a response that needs no tools. It finishes when the model is done.';
  $('demo-next').hidden = true;
  $('demo-breakdown').hidden = false;
  const comparisonReady = demoResult.mode === 'fixture' && verifiedRun(demoResult);
  $('demo-prediction').hidden = !comparisonReady;
  // A custom live request has no predetermined correct next action.
  $('demo-build').hidden = false;
  if (!comparisonReady) {
    $('demo-breakdown').append($('demo-build'));
  }
  $('demo-breakdown').focus({ preventScroll: true });
  $('demo-breakdown').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function runDemo(event) {
  event.preventDefault();
  if (running || !demoScenario()) return;
  const prompt = $('demo-prompt').value.trim();
  if (demoMode === 'live' && !prompt) {
    $('demo-status').textContent = 'Enter a request for the support bot.';
    $('demo-prompt').focus();
    return;
  }
  const payload = { mode: demoMode, scenario: 'normal', inventory_count: 0, inventory_outage: false, simulate_missing_write: false, max_iterations: 6, error_threshold: 2 };
  if (demoMode === 'live') payload.prompt = prompt;
  demoResult = null;
  ['demo-reply', 'demo-next', 'demo-breakdown', 'demo-prediction', 'demo-comparison'].forEach((id) => { $(id).hidden = true; });
  toggleBusy(true);
  $('demo-status').textContent = staticLab ? 'Loading the recorded example…' : demoMode === 'live' ? 'Claude is working on your request…' : 'Running your request…';
  $('demo-send').textContent = 'Working…';
  try {
    demoResult = await streamRun(payload, (message) => {
      if (message.type === 'tool_call') {
        const labels = { lookup_order: 'Looking up your order…', check_inventory: 'Checking replacement stock…', start_return: 'Starting the return…', check_return_eligibility: 'Checking the return policy…' };
        $('demo-status').textContent = labels[message.data?.name] || 'Running the requested tool…';
      }
    });
    $('demo-answer').textContent = demoResult.answer || 'The run stopped without a final reply.';
    $('demo-outcome').textContent = demoOutcome(demoResult);
    $('demo-verification').hidden = verifiedRun(demoResult);
    $('demo-verification').textContent = 'This run did not pass every check. Review the actual tool results before trusting the reply.';
    $('demo-reply').hidden = false;
    $('demo-next').hidden = false;
    $('demo-restart').hidden = false;
    $('demo-send').hidden = true;
    $('demo-prompt').readOnly = true;
    $('demo-status').textContent = '';
    $('demo-reply').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    $('demo-status').textContent = `${error.message || 'The request could not finish.'} You can try again.`;
  } finally {
    toggleBusy(false);
    $('demo-send').textContent = staticLab ? 'Explore the example →' : demoMode === 'live' ? 'Send to Claude →' : 'Send request →';
  }
}

async function testDemoPrediction(event) {
  event.preventDefault();
  const choice = document.querySelector('input[name="demo-prediction"]:checked')?.value;
  if (running || !choice || demoResult?.mode !== 'fixture') return;
  toggleBusy(true);
  $('demo-prediction-status').textContent = staticLab ? 'Loading the recording with 3 replacements in stock…' : 'Running the same request with 3 replacements in stock…';
  try {
    demoComparisonResult = await streamRun({ mode: 'fixture', scenario: 'normal', inventory_count: 3, inventory_outage: false, simulate_missing_write: false, max_iterations: 6, error_threshold: 2 }, () => {});
    const asExpected = verifiedRun(demoComparisonResult) && demoComparisonResult.state.returns.length === 0;
    $('demo-before-result').textContent = demoOutcome(demoResult);
    $('demo-after-result').textContent = demoOutcome(demoComparisonResult);
    $('demo-prediction-feedback').textContent = asExpected
      ? `${choice === 'stock' ? 'Exactly.' : 'Look at the condition in the request: “If none is available.”'} With 3 replacements available, the bot reports stock and creates no return. The inventory result changed the next action; the Tool Runner code stayed the same.`
      : 'The observed result did not match the expected outcome. Inspect the run in the lessons before drawing a conclusion.';
    const reply = element('p', 'demo-comparison-reply', demoComparisonResult.answer || 'No final answer was returned.');
    $('demo-comparison').querySelector('.demo-comparison-reply')?.remove();
    $('demo-prediction-feedback').after(reply);
    $('demo-comparison').hidden = false;
    $('demo-prediction-form').hidden = true;
    $('demo-prediction-status').textContent = '';
    $('demo-comparison').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    $('demo-prediction-status').textContent = `${error.message || 'The comparison could not finish.'} Try again.`;
  } finally { toggleBusy(false); }
}

function openPracticeExercises() {
  if (running) return;
  if (curriculum) showStep('loop');
  else selectView('guided');
  if (demoResult) {
    const imported = demoComparisonResult || demoResult;
    selectScenario(demoScenario());
    $('run-mode').value = imported.mode;
    $('live-prompt').value = $('demo-prompt').value;
    $('stock-count').value = demoComparisonResult ? '3' : '0';
    $('stock-value').value = $('stock-count').value;
    $('missing-write').checked = false;
    setMode();
    resetEvidence();
    finishRun(imported);
    toggleBusy(false);
    $('step-run-status').textContent = demoComparisonResult
      ? 'Both opening runs are saved. The playground shows your latest comparison. Connect what you saw to the roles below, then check your understanding.'
      : 'Your opening run is saved. Connect what you saw to the roles below, then check your understanding.';
    if (demoResult.mode === 'fixture' && verifiedRun(demoResult)) {
      $('step-instructions').textContent = 'You’ve already seen this conversation in action. The model requested order and inventory information, then requested a return when stock was zero. Which part actually executed those Python functions? You can replay the example or answer below.';
      const replay = $('step-actions').querySelector('button');
      if (replay) replay.textContent = 'Replay with zero stock';
    }
  }
  $('guided-heading').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function focusSection(section, heading) {
  $(heading).setAttribute('tabindex', '-1');
  $(heading).focus({ preventScroll: true });
  $(section).scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function lastOpenedModule() {
  try { return localStorage.getItem('tool-runner-last-lab'); } catch { return null; }
}

function renderSeriesIndex() {
  $('series-index').hidden = false;
  $('series-detail').hidden = true;
  $('series-view').setAttribute('aria-labelledby', 'series-heading');
  seriesModuleId = null;
  const modules = tutorialSeries?.modules || [];
  $('series-list').replaceChildren(...modules.map((module, index) => {
    const row = element('article', 'series-row');
    row.append(element('span', 'series-number', String(index + 1).padStart(2, '0')));
    const copy = element('div', 'series-row-copy');
    copy.append(element('h2', '', module.title), element('p', '', module.outcome));
    const button = element('button', 'text-button', 'Open lab →');
    button.type = 'button';
    button.setAttribute('aria-label', `Open lab ${index + 1}: ${module.title}`);
    button.addEventListener('click', () => showSeriesModule(module.id));
    row.append(copy, button);
    return row;
  }));
  const last = modules.find((module) => module.id === lastOpenedModule());
  $('series-resume').hidden = !last;
  $('series-resume').textContent = last ? `Last opened: ${last.title} →` : '';
}

function showSeriesModule(id) {
  const module = tutorialSeries?.modules?.find((item) => item.id === id);
  if (!module || running || notebookBusy || seriesBusy) return;
  seriesModuleId = id;
  try { localStorage.setItem('tool-runner-last-lab', id); } catch { /* Saving this convenience hint is optional. */ }
  $('series-index').hidden = true;
  $('series-detail').hidden = false;
  $('series-view').setAttribute('aria-labelledby', 'series-detail-heading');
  const index = tutorialSeries.modules.indexOf(module);
  $('series-detail-kicker').textContent = `SELF-PACED · LAB ${index + 1} OF ${tutorialSeries.modules.length}`;
  $('series-detail-heading').textContent = module.title;
  $('series-detail-outcome').textContent = module.outcome;
  $('series-detail-task').textContent = `You’ll edit ${module.exercise}(), run it in the support bot, and check what changed.`;
  $('series-preview').textContent = id === 'make-it-yours' ? 'Explore related browser experiments →' : 'Try the guided preview here →';
  const opensInColab = configureNotebookLink('series-download', module.notebook, module.colab_url, 'Open in Colab ↗');
  configureNotebookLink('series-reference', module.reference, module.reference_colab_url, 'Open the complete reference in Colab ↗');
  $('series-open-help').textContent = opensInColab
    ? 'Opens this notebook in a new tab. Save a copy in Drive to keep your work, then run the setup cell.'
    : 'Download this notebook, open Google Colab, and choose File → Upload notebook.';
  $('series-file').hidden = !opensInColab;
  $('series-file').href = staticLab ? `.${module.notebook}` : module.notebook;
  $('series-file').download = module.notebook.split('/').pop();
  $('series-recovery').open = false;
  $('series-next').hidden = index === tutorialSeries.modules.length - 1;
  $('series-next').textContent = index < tutorialSeries.modules.length - 1 ? `Next lab: ${tutorialSeries.modules[index + 1].title} →` : '';
  focusSection('series-view', 'series-detail-heading');
}

async function openSeries(id = null) {
  if (running || notebookBusy || seriesBusy) return;
  selectView('series');
  renderSeriesIndex();
  focusSection('series-view', 'series-heading');
  if (!tutorialSeries) {
    seriesBusy = true;
    $('series-status').textContent = 'Loading the labs…';
    $('series-retry').hidden = true;
    try {
      const data = await jsonRequest('/api/tutorials');
      const safePath = (value) => typeof value === 'string' && /^\/tutorials\/[a-z0-9.-]+\.ipynb$/.test(value);
      if (!Array.isArray(data.modules) || !data.modules.length || data.modules.some((module) =>
        !modulePreviews[module.id] || typeof module.title !== 'string' || typeof module.outcome !== 'string' ||
        typeof module.exercise !== 'string' || !safePath(module.notebook) || !safePath(module.reference))) {
        throw new Error('The lab list could not be loaded.');
      }
      tutorialSeries = data;
    } catch (error) {
      $('series-status').textContent = error.message || 'The labs could not load. Try again.';
      $('series-retry').hidden = false;
      return;
    } finally { seriesBusy = false; }
  }
  if (currentView !== 'series') return;
  $('series-status').textContent = '';
  renderSeriesIndex();
  if (id) showSeriesModule(id);
}

async function openIntroNotebook() {
  if (running || notebookBusy || seriesBusy) return;
  notebookChapter = 'intro';
  notebookModuleId = null;
  notebookIndex = 0;
  await openNotebook();
}

async function openModulePreview() {
  if (!modulePreviews[seriesModuleId] || running || notebookBusy || seriesBusy) return;
  notebookModuleId = seriesModuleId;
  notebookChapter = 'module';
  notebookIndex = 0;
  await openNotebook();
}

function notebookCells() {
  if (notebookChapter === 'module') {
    const all = [...(notebook?.cells || []), ...(notebook?.deep_cells || [])];
    return (modulePreviews[notebookModuleId] || []).map((id) => all.find((cell) => cell.id === id));
  }
  return (notebookChapter === 'deep' ? notebook?.deep_cells : notebook?.cells) || [];
}

function notebookVariant(cell) {
  return cell?.variants?.find((item) => item.id === notebookVariants.get(cell.id)) || cell?.variants?.[0];
}

function notebookResultKey(cell, variant = notebookVariant(cell)) {
  return variant ? `${cell.id}:${variant.id}` : cell.id;
}

function notebookCellComplete(cell) {
  return cell && (cell.variants
    ? cell.variants.every((variant) => notebookOutputs.has(notebookResultKey(cell, variant)))
    : notebookOutputs.has(cell.id));
}

function syncNotebookControls() {
  const cell = notebookCells()[notebookIndex];
  $('notebook-run').disabled = running || notebookBusy || !cell;
  ['notebook-back', 'notebook-previous', 'notebook-next', 'notebook-repeat', 'notebook-practice', 'notebook-playground', 'notebook-variant', 'notebook-try-other', 'notebook-intro', 'notebook-labs'].forEach((id) => { $(id).disabled = running || notebookBusy; });
  $('notebook-deeper').disabled = running || notebookBusy || seriesBusy;
}

function renderNotebookCell() {
  const cells = notebookCells();
  const cell = cells[notebookIndex];
  $('notebook-cell').hidden = !cell;
  $('notebook-finish').hidden = Boolean(cell);
  $('notebook-view').setAttribute('aria-labelledby', cell ? 'notebook-heading' : 'notebook-finish-heading');
  if (!cell) {
    const deep = notebookChapter === 'deep';
    const preview = notebookChapter === 'module';
    $('notebook-finish-heading').textContent = preview ? 'Now make it your code.' : deep ? 'You can now test the loop.' : staticLab ? 'You’ve explored the whole loop.' : 'You’ve run the whole loop.';
    $('notebook-finish-copy').textContent = preview
      ? 'You have explored a working example. Open this lab in Colab, complete its small coding task, and run your own implementation in the bot.'
      : deep
      ? 'You replaced a manual loop with Tool Runner, traced the conversation, and tested its limits and failures. The SDK handles the repeated tool conversation; your code still owns the rules and the results.'
      : 'You have the context. Now choose a small Colab lab and build the code that powers the bot. Each lab has its own checks, hints, and reference.';
    $('notebook-deeper').hidden = false;
    $('notebook-deeper').textContent = preview ? 'Get this lab for Colab →' : 'Choose a lab →';
    $('notebook-repeat').textContent = preview ? 'Review this preview' : deep ? 'Review the experiments' : 'Review the introductory cells';
    $('notebook-repeat').className = 'text-button';
    $('notebook-further').hidden = staticLab || !deep;
    syncNotebookControls();
    return;
  }
  const variant = notebookVariant(cell);
  const saved = notebookOutputs.get(notebookResultKey(cell, variant));
  const complete = notebookCellComplete(cell);
  $('notebook-progress').textContent = notebookChapter === 'module'
    ? `GUIDED PREVIEW · ${notebookIndex + 1} OF ${cells.length}`
    : notebookChapter === 'deep'
    ? `GO DEEPER · EXPERIMENT ${notebookIndex + 1} OF ${cells.length}`
    : `CELL ${notebookIndex + 1} OF ${cells.length}`;
  $('notebook-heading').textContent = cell.title;
  $('notebook-instruction').textContent = staticLab ? recordedInstructions[cell.id] || 'Explore this recorded example, then open a Colab lab to execute and edit the code.' : cell.instruction;
  $('notebook-challenge').hidden = !cell.challenge;
  $('notebook-challenge').textContent = cell.challenge || '';
  $('notebook-control').hidden = !variant;
  if (variant) {
    $('notebook-variant-label').textContent = cell.control_label;
    $('notebook-variant').replaceChildren(...cell.variants.map((item) => {
      const option = element('option', '', item.label);
      option.value = item.id;
      return option;
    }));
    $('notebook-variant').value = variant.id;
  }
  $('notebook-code').textContent = variant?.code || cell.code;
  $('notebook-output-panel').hidden = !saved;
  $('notebook-output').textContent = saved?.output || '';
  $('notebook-takeaway').hidden = !saved;
  $('notebook-takeaway').textContent = staticLab ? (saved?.takeaway || '').replace(/^You wrote /, 'The manual example handles ') : saved?.takeaway || '';
  $('notebook-manual-choice').hidden = cell.id !== 'why' || !complete;
  if ($('notebook-manual-choice').hidden) $('notebook-manual-choice').open = false;
  $('notebook-next').hidden = !complete;
  $('notebook-next').textContent = notebookIndex === cells.length - 1 ? 'Finish →' : variant ? 'Next experiment →' : 'Next cell →';
  $('notebook-compare-prompt').hidden = !variant || !saved || complete;
  const remaining = cell.variants?.find((item) => !notebookOutputs.has(notebookResultKey(cell, item)));
  if (variant && saved && remaining) {
    $('notebook-compare-note').textContent = 'Now change the setting and run again to compare.';
    $('notebook-try-other').textContent = `Try: ${remaining.label} →`;
  }
  $('notebook-previous').hidden = notebookIndex === 0;
  $('notebook-run').textContent = staticLab ? saved ? 'Show recording again' : 'Show recorded output →' : saved ? 'Run again' : variant ? 'Run experiment →' : 'Run cell →';
  $('notebook-run').className = saved ? 'text-button notebook-rerun' : 'primary-button';
  $('notebook-status').textContent = '';
  $('notebook-mode-note').textContent = staticLab ? recordedNotice : notebook.notice || 'Scripted model replies. Real Python and Tool Runner.';
  if (staticLab) {
    $('notebook-control-note').textContent = 'Choose the recording to compare. Run the code yourself in Colab.';
    $('notebook-compare-note').textContent = 'Now choose the other setting and compare its recorded output.';
  }
  syncNotebookControls();
}

async function openNotebook() {
  if (running || notebookBusy) return;
  selectView('notebook');
  $('notebook-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (!notebook) {
    notebookBusy = true;
    $('notebook-heading').textContent = 'Your first code cell';
    $('notebook-status').textContent = 'Loading the notebook…';
    syncNotebookControls();
    try {
      const data = await jsonRequest('/api/notebook');
      if (!Array.isArray(data.cells) || !data.cells.length) throw new Error('The notebook has no cells yet.');
      notebook = data;
    } catch (error) {
      showNotebookUnavailable(error.message);
      return;
    } finally { notebookBusy = false; syncNotebookControls(); }
  }
  const cells = notebookCells();
  if (!cells.length || cells.some((cell) => !cell)) {
    notebook = null;
    showNotebookUnavailable('This example is missing some of its cells.');
    return;
  }
  renderNotebookCell();
  focusNotebook();
}

function showNotebookUnavailable(message) {
  $('notebook-cell').hidden = false;
  $('notebook-finish').hidden = true;
  $('notebook-view').setAttribute('aria-labelledby', 'notebook-heading');
  $('notebook-heading').textContent = 'The code view is unavailable.';
  $('notebook-progress').textContent = 'TRY AGAIN';
  $('notebook-instruction').textContent = 'Choose All labs to download the notebook, or open this example again to retry.';
  $('notebook-code').textContent = '# No code has run.';
  $('notebook-status').textContent = message || 'The notebook could not load.';
  ['notebook-challenge', 'notebook-control', 'notebook-output-panel', 'notebook-takeaway', 'notebook-manual-choice', 'notebook-compare-prompt', 'notebook-next', 'notebook-previous'].forEach((id) => { $(id).hidden = true; });
  syncNotebookControls();
  focusSection('notebook-view', 'notebook-heading');
}

function focusNotebook() {
  const target = notebookIndex >= (notebookCells().length || Infinity) ? $('notebook-finish') : $('notebook-heading');
  target.setAttribute('tabindex', '-1');
  target.focus({ preventScroll: true });
  $('notebook-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function runNotebookCell() {
  const cell = notebookCells()[notebookIndex];
  if (!cell || running || notebookBusy) return;
  const variant = notebookVariant(cell);
  const key = notebookResultKey(cell, variant);
  notebookBusy = true;
  toggleBusy(true);
  // A rerun replaces its own evidence; the last successful result is not
  // displayed as though it came from a request which failed or is still running.
  notebookOutputs.delete(key);
  $('notebook-output-panel').hidden = true;
  $('notebook-takeaway').hidden = true;
  $('notebook-next').hidden = true;
  $('notebook-compare-prompt').hidden = true;
  $('notebook-manual-choice').hidden = true;
  $('notebook-run').textContent = staticLab ? 'Loading…' : 'Running…';
  $('notebook-status').textContent = staticLab ? 'Loading recorded output…' : 'Executing Python…';
  try {
    const payload = variant ? { cell_id: cell.id, variant: variant.id } : { cell_id: cell.id };
    const result = await postJSON('/api/notebook-cell', payload);
    if (result.cell_id !== cell.id || (variant && result.variant !== variant.id) || result.passed !== true || typeof result.output !== 'string') throw new Error(result.error || 'This cell did not finish successfully.');
    notebookOutputs.set(key, result);
    renderNotebookCell();
    $('notebook-status').textContent = staticLab ? 'Recorded output shown' : variant ? 'Experiment complete' : 'Cell complete';
    $('notebook-output-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    $('notebook-status').textContent = error.message || 'This cell could not run. Try again.';
    $('notebook-run').textContent = 'Try again →';
    $('notebook-run').className = 'primary-button';
  } finally {
    notebookBusy = false;
    toggleBusy(false);
  }
}

function nextNotebookCell() {
  const cell = notebookCells()[notebookIndex];
  if (running || notebookBusy || !notebookCellComplete(cell)) return;
  notebookIndex += 1;
  renderNotebookCell();
  focusNotebook();
}

function startNotebookChapter(chapter) {
  if (running || notebookBusy) return;
  if (chapter === 'deep' && !notebook?.deep_cells?.length) return;
  notebookChapter = chapter;
  notebookModuleId = null;
  notebookIndex = 0;
  $('notebook-further').open = false;
  renderNotebookCell();
  focusNotebook();
}

function changeNotebookVariant(value) {
  const cell = notebookCells()[notebookIndex];
  if (running || notebookBusy || !cell?.variants?.some((variant) => variant.id === value)) return;
  notebookVariants.set(cell.id, value);
  renderNotebookCell();
}

async function initialize() {
  if (staticLab) {
    $('demo-connect').hidden = true;
    $('demo-mode-switch').hidden = true;
    $('demo-mode-label').textContent = 'Recorded demonstration';
    $('demo-mode-note').textContent = recordedNotice;
    await loadConfig();
    if (!config) $('demo-status').textContent = 'The recorded examples could not load. Refresh this page to try again.';
    selectView('demo');
    $('demo-test').textContent = 'Compare the recorded outcome →';
    document.querySelector('.demo-comparison-note').textContent = 'Each recording came from an isolated run with a fresh practice database.';
    return;
  }
  restoreLocation();
  $('checkpoint-button').disabled = true;
  const [configurationResult, curriculumResult] = await Promise.allSettled([loadConfig(), jsonRequest('/api/curriculum')]);
  if (configurationResult.status === 'rejected') showError('The lab configuration is unavailable. Reload after starting the server.');
  if (curriculumResult.status === 'fulfilled' && Array.isArray(curriculumResult.value.steps)) {
    curriculum = curriculumResult.value;
    if (!curriculum.steps.some((step) => step.id === currentStepId)) currentStepId = curriculum.steps[0]?.id || 'loop';
    visitedSteps.add(currentStepId);
    try { await syncReport(); } catch { $('progress-note').textContent = 'Learning progress will be checked in the current server session.'; }
    renderLesson();
  } else {
    $('step-objective').textContent = 'The guided curriculum could not be loaded. Start the lab server and reload this page.';
    $('step-list').replaceChildren(element('p', 'empty-copy', 'The learning path is unavailable.'));
    $('step-why').textContent = curriculumResult.status === 'rejected' ? curriculumResult.reason.message : 'The server returned no learning steps.';
  }
  selectView(currentView);
}

$('checkpoint-button').addEventListener('click', checkCheckpoint);
$('exercise-check-button').addEventListener('click', checkExercise);
$('reveal-solution-button').addEventListener('click', () => {
  if (!exerciseMetadata?.solution) return;
  $('solution-panel').hidden = false; $('solution-panel').open = true;
  $('exercise-solution').textContent = exerciseMetadata.solution;
});
$('copy-path-button').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('exercise-file').textContent); $('copy-path-button').textContent = 'Copied'; }
  catch { $('copy-path-button').textContent = 'Select the path below to copy'; }
});
$('previous-step').addEventListener('click', () => { const index = curriculum?.steps.findIndex((step) => step.id === currentStepId); if (index > 0) showStep(curriculum.steps[index - 1].id); });
$('next-step').addEventListener('click', () => { const index = curriculum?.steps.findIndex((step) => step.id === currentStepId); if (index >= 0 && index < curriculum.steps.length - 1) showStep(curriculum.steps[index + 1].id); });
$('back-to-lesson').addEventListener('click', () => { selectView('guided'); $('guided-heading').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
$('report-button').addEventListener('click', async () => {
  $('report-button').disabled = true;
  try { downloadJSON(await syncReport(), 'tool-runner-learning-report.json'); }
  catch (error) { $('progress-note').textContent = error.message || 'Unable to export the report.'; }
  finally { $('report-button').disabled = false; }
});
$('connect-form').addEventListener('submit', connectClaude);
$('demo-form').addEventListener('submit', runDemo);
$('demo-prompt').addEventListener('input', resizeDemoPrompt);
window.addEventListener('resize', resizeDemoPrompt);
$('demo-explain').addEventListener('click', explainDemo);
$('demo-prediction-form').addEventListener('submit', testDemoPrediction);
$('demo-prediction-form').addEventListener('change', syncDemoControls);
$('demo-build').addEventListener('click', openIntroNotebook);
$('open-lessons').addEventListener('click', () => openSeries());
$('series-back').addEventListener('click', () => { selectView('demo'); focusSection('demo-view', 'demo-heading'); });
$('series-intro').addEventListener('click', openIntroNotebook);
$('series-all').addEventListener('click', () => { renderSeriesIndex(); focusSection('series-view', 'series-heading'); });
$('series-resume').addEventListener('click', () => showSeriesModule(lastOpenedModule()));
$('series-retry').addEventListener('click', () => openSeries());
$('series-preview').addEventListener('click', openModulePreview);
$('series-next').addEventListener('click', () => {
  const index = tutorialSeries?.modules.findIndex((module) => module.id === seriesModuleId);
  if (index >= 0 && index < tutorialSeries.modules.length - 1) showSeriesModule(tutorialSeries.modules[index + 1].id);
});
$('notebook-run').addEventListener('click', runNotebookCell);
$('notebook-next').addEventListener('click', nextNotebookCell);
$('notebook-previous').addEventListener('click', () => {
  if (running || notebookBusy || notebookIndex === 0) return;
  notebookIndex -= 1; renderNotebookCell(); focusNotebook();
});
$('notebook-repeat').addEventListener('click', () => { notebookIndex = 0; renderNotebookCell(); focusNotebook(); });
$('notebook-deeper').addEventListener('click', () => openSeries(notebookModuleId));
$('notebook-labs').addEventListener('click', () => openSeries());
$('notebook-intro').addEventListener('click', () => startNotebookChapter('intro'));
$('notebook-variant').addEventListener('change', (event) => changeNotebookVariant(event.target.value));
$('notebook-try-other').addEventListener('click', () => {
  const cell = notebookCells()[notebookIndex];
  const remaining = cell?.variants?.find((variant) => !notebookOutputs.has(notebookResultKey(cell, variant)));
  if (remaining) { changeNotebookVariant(remaining.id); focusNotebook(); }
});
$('notebook-back').addEventListener('click', () => {
  selectView('demo');
  $('demo-heading').setAttribute('tabindex', '-1');
  $('demo-heading').focus({ preventScroll: true });
  $('demo-heading').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('notebook-practice').addEventListener('click', openPracticeExercises);
$('notebook-playground').addEventListener('click', () => {
  openPracticeExercises(); selectView('explore');
  $('explore-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('home-demo').addEventListener('click', () => { selectView('demo'); $('demo-heading').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
$('demo-restart').addEventListener('click', () => { resetDemo(); refreshDemoMode(); $('demo-heading').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
$('demo-mode-switch').addEventListener('click', () => setDemoMode(demoMode === 'live' ? 'fixture' : 'live'));
$('demo-connect').addEventListener('click', () => {
  demoLiveOpen = !demoLiveOpen;
  $('live-extension').open = demoLiveOpen;
  selectView('demo');
  if (demoLiveOpen) $('live-extension').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});
$('disconnect-button').addEventListener('click', disconnectClaude);
$('connected-model').addEventListener('change', changeLiveModel);
$('start-live-button').addEventListener('click', () => {
  if (running || !config?.live_available) return;
  if (currentView === 'demo') {
    setDemoMode('live');
    demoLiveOpen = false;
    selectView('demo');
    $('demo-prompt').focus();
    return;
  }
  $('run-mode').value = 'live'; setMode(); selectView('explore');
  $('explore-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
  $('live-prompt').focus({ preventScroll: true });
});
