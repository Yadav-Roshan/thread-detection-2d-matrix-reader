// UI smoke test for the static export (frontend/out) with a fully mocked API (docs/API.md).
// Run `npm run build` first, then `npm run test:e2e`. No backend required.
// Optional: SCREENSHOT_DIR=<dir> saves screenshots at key steps.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { extname, join, resolve } from 'node:path';

const frontend = resolve(import.meta.dirname, '..');
const outDir = join(frontend, 'out');
const repo = resolve(frontend, '..');
if (!existsSync(join(outDir, 'index.html'))) throw new Error('frontend/out missing — run `npm run build` first');

const pick = (...candidates) => candidates.find((p) => existsSync(p));
const logoPath = join(frontend, 'public', 'logo.jpeg');
const codeImage = pick(join(repo, 'data/2D-Code/Snapshot000003.jpg'), logoPath);
const threadImage = pick(join(repo, 'data/Thread/Snapshot000115.jpg'), logoPath);
const dataUrl = (p) => `data:image/jpeg;base64,${readFileSync(p).toString('base64')}`;

// ------------------------------------------------------------------ static server
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.jpeg': 'image/jpeg', '.txt': 'text/plain', '.json': 'application/json', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };
const server = createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  let file = join(outDir, path);
  if (!file.startsWith(outDir)) { res.writeHead(403).end(); return; }
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, 'index.html');
  if (!existsSync(file) && existsSync(`${file}.html`)) file = `${file}.html`;
  if (!existsSync(file)) { res.writeHead(404).end('not found'); return; }
  res.writeHead(200, { 'Content-Type': MIME[extname(file)] || 'application/octet-stream' });
  res.end(readFileSync(file));
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}`;

// ------------------------------------------------------------------ mock API
const DECODED = '005715051079230AP666711026002315';
const params = { min_ridge_area: 75000, min_ridge_band_px: 180, min_purity: 0.4, min_classifier_margin: 0.5, max_novelty: 2.5 };
const reportId = (verdict, part = DECODED) => `2026/11/01/${verdict}/${part}_130405.pdf`;
const reportUrl = (id) => `/api/reports/file/${id}`;

const codeOk = { passed: true, text: DECODED, format: 'DataMatrix', message: 'Decoded' };
const threadPresent = { passed: true, state: 'PRESENT', confidence: 0.97, message: 'Thread ridges detected', evidence: {} };
const threadNotVisible = { passed: false, state: 'NOT_VISIBLE', confidence: 0.2, message: 'Ridge band too small', evidence: {} };
const result = (mode, overrides = {}) => {
  const r = {
    timestamp: '2026-11-01T13:04:05Z',
    operator: 'Ravi',
    mode,
    overall_result: 'GO',
    decoded_number: DECODED,
    code_result: codeOk,
    thread_result: threadPresent,
    code_image: dataUrl(codeImage),
    thread_image: dataUrl(threadImage),
    ...overrides,
  };
  r.report_id = reportId(r.overall_result);
  r.report_url = reportUrl(r.report_id);
  return r;
};
const noGo = (mode) => result(mode, { overall_result: 'NO_GO', thread_result: threadNotVisible });

const reports = [
  { report_id: reportId('NO_GO', 'UNREAD'), timestamp: '2026-11-01T13:10:00Z', decoded_number: null, overall_result: 'NO_GO', report_url: reportUrl(reportId('NO_GO', 'UNREAD')) },
  { report_id: reportId('GO'), timestamp: '2026-11-01T13:04:05Z', decoded_number: DECODED, overall_result: 'GO', report_url: reportUrl(reportId('GO')) },
];

const herculesConfig = {
  enabled: true, bind_host: '0.0.0.0', port: 9000,
  ready_expect: 'isready', ready_reply: 'readyyes', not_ready_reply: 'readyno',
  scan_expect: 'scanjobno', scan_fail_reply: 'noread',
  thread_expect: 'threadpresence', thread_ok_reply: 'threadok', thread_nok_reply: 'threadnok',
  error_reply: 'error',
};

const state = {
  active: { code: 0, thread: null },
  rois: { code: { x: 100, y: 80, width: 400, height: 300 }, thread: null },
  onlineCalls: 0,
  offlineCalls: 0,
  posted: {},
  reportDir: 'D:\\Reports\\CamShaft',
  hercules: {
    enabled: true, listening: true, bind_host: '0.0.0.0', port: 9000,
    client_connected: false, client_addr: null, last_error: null,
    // Pre-existing event: must be ignored on page load.
    last_event: { seq: 3, kind: 'thread', message: 'threadpresence', reply: 'threadok', timestamp: '2026-11-01T12:00:00Z', result: result('hercules') },
  },
};

async function handleApi(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const method = req.method();
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  const body = () => { try { return req.postDataJSON(); } catch { return null; } };

  if (path === '/api/camera/devices') return json({ devices: [{ index: 0, label: 'USB Camera 0', width: 1280, height: 720 }, { index: 1, label: 'USB Camera 1', width: 1280, height: 720 }], active: state.active });
  if (path === '/api/camera/select') { const b = body(); state.active = { ...state.active, [b.role]: b.index }; return json({ success: true, message: 'ok', active: state.active }); }
  if (path === '/api/camera/release') { const b = body(); state.active = { ...state.active, [b.role]: null }; return json({ success: true, active: state.active }); }
  if (/^\/api\/camera\/(code|thread)\/stream$/.test(path)) return route.fulfill({ status: 200, contentType: 'image/jpeg', body: readFileSync(logoPath) });
  if (/^\/api\/camera\/(code|thread)\/save-frame$/.test(path)) return json({ success: true, path: 'C:/frames/x.png', filename: 'x.png' });
  if (path === '/api/config/rois') { if (method === 'POST') { state.rois = body(); state.posted.rois = state.rois; } return json(state.rois); }
  if (path === '/api/config/detection') { if (method === 'POST') state.posted.detection = body(); return json(method === 'POST' ? body() : params); }
  if (path === '/api/config/detection-defaults') return json(params);
  if (path === '/api/config/hercules') { if (method === 'POST') { state.posted.hercules = body(); return json(body()); } return json(herculesConfig); }
  if (path === '/api/hercules/status') return json(state.hercules);
  if (path === '/api/auth/login') return json(body().password === 'admin123' ? { success: true, message: 'ok' } : { success: false, message: 'Invalid password' });
  if (path === '/api/auth/change-password') return json({ success: true, message: 'ok' });
  if (path === '/api/inspect') {
    state.onlineCalls += 1;
    if (state.onlineCalls === 1) return json({ detail: 'Thread camera is not connected' }, 503);
    return json(result('online'));
  }
  if (path === '/api/inspect/offline') {
    state.offlineCalls += 1;
    return json(state.offlineCalls === 1 ? result('offline') : noGo('offline'));
  }
  if (path === '/api/report/directory') return json({ directory: state.reportDir, default: 'C:\\CamShaft\\reports\\pdf', structure: '<dir>/YYYY/MM/DD/GO|NO_GO/<part>_HHMMSS.pdf' });
  if (path === '/api/report/browse-directory') return json({ directory: 'E:\\Picked\\Folder' });
  if (path === '/api/report/set-directory') {
    const b = body();
    state.posted.reportDirs = [...(state.posted.reportDirs || []), b.directory];
    if (b.directory && !/^[A-Za-z]:\\/.test(b.directory)) return json({ detail: 'Report folder must be an absolute path' }, 400);
    state.reportDir = b.directory ?? 'C:\\CamShaft\\reports\\pdf';
    return json({ success: true, directory: state.reportDir });
  }
  if (path === '/api/reports') return json(reports);
  if (path.startsWith('/api/reports/by-part/')) return json(reports.filter((r) => r.decoded_number === decodeURIComponent(path.split('/').pop())));
  if (path.startsWith('/api/reports/file/')) return route.fulfill({ status: 200, contentType: 'application/pdf', body: '%PDF-1.4\n%%EOF' });
  if (path === '/api/health') return json({ status: 'ok' });
  return json({ detail: `unmocked ${method} ${path}` }, 404);
}

// ------------------------------------------------------------------ test
const check = (cond, msg) => { if (!cond) throw new Error(msg); };
const shot = async (page, name) => { if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, `${name}.png`) }); };
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => pageErrors.push(e.message));
  await page.addInitScript(() => {
    window.__dialogs = [];
    window.alert = (m) => window.__dialogs.push(`alert:${m}`);
    window.confirm = (m) => { window.__dialogs.push(`confirm:${m}`); return true; };
  });
  await page.route('**/api/**', handleApi);

  // ---- Load / header
  await page.goto(base, { waitUntil: 'networkidle' });
  check((await page.title()) === 'CamShaft 2D Matrix & Thread Inspection', `bad title: ${await page.title()}`);
  await page.getByRole('heading', { name: 'CAMSHAFT 2D MATRIX & THREAD INSPECTION' }).waitFor();
  const logo = page.getByAltText('Perfect Gauging Solutions');
  await logo.waitFor();
  check(await logo.evaluate((img) => img.naturalWidth > 0), 'logo did not load');
  await page.getByTestId('live-badge').getByText('1/2 CAMERAS LIVE').waitFor();

  // ---- INSPECTION is live-only
  check((await page.locator('input[type=file]').count()) === 0, 'INSPECTION must not have upload inputs');
  check((await page.getByRole('button', { name: 'OFFLINE' }).count()) === 0, 'INSPECTION must not have an OFFLINE switch');
  await page.getByTestId('rule-code').getByText('ROI: set (400×300 px)').waitFor();
  await page.getByTestId('rule-thread').getByText('ROI: full frame').waitFor();
  check((await page.getByTestId('feed-code').getByLabel('2D Code cam').inputValue()) === '0', 'code select should be device 0');
  check((await page.getByTestId('feed-thread').getByLabel('Thread cam').inputValue()) === '-1', 'thread select should be off');
  await page.getByTestId('feed-code').locator('img[src*="/api/camera/code/stream"]').waitFor();
  await page.getByTestId('feed-thread').getByText('Camera not connected').waitFor();
  await page.getByTestId('hercules-line').getByText('Hercules: listening on :9000').waitFor();
  await page.waitForTimeout(1300); // at least one more Hercules poll
  check((await page.getByTestId('last-part').innerText()).includes('No part checked yet'), 'pre-load Hercules event must be ignored');
  await shot(page, '0-initial');

  await page.getByLabel('Thread cam').selectOption('1');
  await page.getByTestId('feed-thread').locator('img[src*="/api/camera/thread/stream"]').waitFor();
  await page.getByTestId('live-badge').getByText('2/2 CAMERAS LIVE').waitFor();

  // ---- Manual CHECK: 503 detail, then GO with PDF link
  const checkBtn = page.getByRole('button', { name: 'CHECK', exact: true });
  await checkBtn.click();
  await page.getByTestId('results').getByText('Thread camera is not connected').waitFor();
  await page.locator('h1').click();
  await page.keyboard.press('Enter'); // keyboard CHECK
  const overlay = page.getByTestId('feed-overlay');
  await overlay.getByText('GO', { exact: true }).waitFor();
  await page.getByTestId('last-part').getByText(DECODED).waitFor();
  await page.getByTestId('results').getByText(/PRESENT · 97%/).waitFor();
  const pdfLink = page.getByTestId('results').getByRole('link', { name: 'Open inspection report (PDF)' });
  check((await pdfLink.getAttribute('href')) === reportUrl(reportId('GO')), 'results PDF link href mismatch');
  check((await pdfLink.getAttribute('target')) === '_blank', 'PDF link must open a new tab');
  await page.getByTestId('feed-thread').locator('img[src^="data:image/jpeg"]').waitFor();
  await shot(page, '1-go');
  await page.getByTestId('feed-thread').getByRole('button', { name: 'Back to live' }).click();
  await page.getByTestId('feed-thread').locator('img[src*="/api/camera/thread/stream"]').waitFor();

  // ---- Hercules events update the INSPECTION screen
  state.hercules = {
    ...state.hercules, client_connected: true, client_addr: '192.168.1.5:50122',
    last_event: { seq: 4, kind: 'scan', message: 'scanjobno', reply: 'XYZ999', timestamp: '2026-11-01T13:20:00Z',
      result: { decoded_number: 'XYZ999', code_result: { ...codeOk, text: 'XYZ999' }, code_image: dataUrl(codeImage) } },
  };
  await page.getByTestId('hercules-line').getByText('Hercules: connected (192.168.1.5:50122)').waitFor();
  await page.getByTestId('results').getByText('XYZ999 (DataMatrix)').waitFor();
  await page.getByTestId('results').getByText('waiting for thread check…').waitFor();
  await page.getByTestId('last-part').getByText('XYZ999').waitFor();
  await page.getByTestId('feed-code').locator('img[src^="data:image/jpeg"]').waitFor();
  check((await overlay.count()) === 0, 'scan event must not show a GO/NO-GO verdict');
  state.hercules = {
    ...state.hercules,
    last_event: { seq: 5, kind: 'thread', message: 'threadpresence', reply: 'threadnok', timestamp: '2026-11-01T13:20:05Z', result: noGo('hercules') },
  };
  await overlay.getByText('NO-GO', { exact: true }).waitFor();
  check((await overlay.getByRole('link', { name: 'Open report PDF' }).getAttribute('href')) === reportUrl(reportId('NO_GO')), 'overlay PDF link mismatch');
  await page.getByTestId('results').getByText(/NOT VISIBLE · 20%/).waitFor();
  await page.getByText('Thread not visible — move the camera closer / remove obstruction').waitFor();
  await shot(page, '2-hercules-nogo');

  // ---- SETUP password gate
  await page.getByRole('button', { name: 'SETUP' }).click();
  await page.getByRole('heading', { name: 'SETUP Authentication' }).waitFor();
  await page.getByPlaceholder('Enter password').fill('wrong');
  await page.getByRole('button', { name: 'Login' }).click();
  await page.getByText('Invalid password').waitFor();
  await page.getByPlaceholder('Enter password').fill('admin123');
  await page.getByRole('button', { name: 'Login' }).click();
  await page.getByRole('button', { name: 'Change password' }).waitFor();
  await page.waitForFunction(() => document.querySelector('#param-min_ridge_band_px')?.value === '180');

  // ---- Offline test (SETUP)
  await page.getByRole('button', { name: 'Offline test' }).click();
  const runOffline = page.getByRole('button', { name: 'Run offline CHECK' });
  check(await runOffline.isDisabled(), 'offline CHECK must be disabled without uploads');
  await page.getByTestId('upload-code').setInputFiles(codeImage);
  await page.getByTestId('upload-thread').setInputFiles(threadImage);
  await page.getByTestId('feed-code').locator('img[src^="blob:"]').waitFor();
  await page.getByTestId('feed-thread').locator('img[src^="blob:"]').waitFor();
  check((await page.getByLabel('2D Code cam').count()) === 0, 'camera select hidden while uploaded image shown');
  await runOffline.click();
  const offResults = page.getByTestId('offline-results');
  await offResults.getByText(/PRESENT · 97%/).waitFor();
  await overlay.getByText('GO', { exact: true }).waitFor();
  check((await offResults.getByRole('link', { name: 'Open inspection report (PDF)' }).getAttribute('href')) === reportUrl(reportId('GO')), 'offline PDF link mismatch');
  await page.getByTestId('feed-thread').locator('img[src^="data:image/jpeg"]').waitFor();
  await shot(page, '3-offline-go');
  await page.getByTestId('feed-thread').getByRole('button', { name: 'Show original' }).click();
  await page.getByTestId('feed-thread').locator('img[src^="blob:"]').waitFor();
  await runOffline.click();
  await overlay.getByText('NO-GO', { exact: true }).waitFor();
  await offResults.getByText(/NOT VISIBLE · 20%/).waitFor();
  await offResults.getByText('Thread not visible — move the camera closer / remove obstruction').waitFor();

  // ---- ROIs: draw on the uploaded thread image, save, clear → null
  await page.getByRole('button', { name: 'ROIs', exact: true }).click();
  await page.getByRole('button', { name: 'Thread ROI' }).click();
  await page.getByTestId('feed-thread').locator('img[src^="blob:"]').waitFor();
  const canvas = page.getByTestId('feed-thread').locator('canvas');
  await canvas.waitFor();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.25);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.7, { steps: 5 });
  await page.mouse.up();
  const w = Number(await page.locator('#roi-thread-width').inputValue());
  check(w > 10, `ROI draw did not populate width (got ${w})`);
  await page.getByRole('button', { name: 'Save ROIs' }).click();
  await page.getByRole('heading', { name: 'ROIs saved' }).waitFor();
  check(state.posted.rois?.thread?.width === w && state.posted.rois?.code?.x === 100, 'saved ROI body mismatch');
  await page.getByRole('button', { name: 'OK' }).click();
  await page.getByRole('button', { name: 'Clear (use full frame)' }).first().click();
  await page.getByRole('button', { name: 'Save ROIs' }).click();
  await page.getByRole('heading', { name: 'ROIs saved' }).waitFor();
  check(state.posted.rois?.code === null, 'cleared code ROI should be saved as null');
  await page.getByRole('button', { name: 'OK' }).click();

  // ---- Detection
  await page.getByRole('button', { name: 'Save detection params' }).click();
  await page.getByRole('heading', { name: 'Detection parameters saved' }).waitFor();
  check(state.posted.detection?.min_purity === 0.4, 'detection body mismatch');
  await page.getByRole('button', { name: 'OK' }).click();

  // ---- Hercules settings
  await page.getByRole('button', { name: 'Hercules', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#hercules-scan_expect')?.value === 'scanjobno');
  await page.getByText('Reply when 2D code not read').waitFor();
  await page.getByText('On success the reply is the decoded 2D number itself.').waitFor();
  await page.getByTestId('hercules-status').getByText('Waiting for Hercules on 0.0.0.0:9000').waitFor();
  await page.locator('#hercules-thread_ok_reply').fill('THREAD-OK');
  await page.locator('#hercules-port').fill('9100');
  await page.getByRole('button', { name: 'Save Hercules settings' }).click();
  await page.locator('aside').last().getByRole('button', { name: 'Saved' }).waitFor();
  check(state.posted.hercules?.thread_ok_reply === 'THREAD-OK' && state.posted.hercules?.port === 9100, 'hercules body mismatch');
  check(state.posted.hercules?.scan_fail_reply === 'noread' && state.posted.hercules?.enabled === true, 'hercules body incomplete');
  await shot(page, '4-hercules');

  // ---- Reports: folder + PDF list
  await page.getByRole('button', { name: 'Reports', exact: true }).click();
  const dirInput = page.locator('#report-dir');
  await page.waitForFunction(() => document.querySelector('#report-dir')?.value === 'D:\\Reports\\CamShaft');
  await page.getByText('<folder>\\YYYY\\MM\\DD\\GO|NO_GO\\<part>_<HHMMSS>.pdf', { exact: false }).waitFor();
  await page.getByRole('button', { name: 'Browse…' }).click();
  await page.waitForFunction(() => document.querySelector('#report-dir')?.value === 'E:\\Picked\\Folder');
  const folder = page.getByTestId('report-folder');
  await folder.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('heading', { name: 'Report folder saved' }).waitFor();
  check(state.posted.reportDirs.at(-1) === 'E:\\Picked\\Folder', 'report folder save body mismatch');
  await page.getByRole('button', { name: 'OK' }).click();
  await dirInput.fill('relative\\path');
  await folder.getByRole('button', { name: 'Save', exact: true }).click();
  await folder.getByText('Report folder must be an absolute path').waitFor();
  await folder.getByRole('button', { name: 'Use default' }).click();
  await page.getByRole('heading', { name: 'Report folder saved' }).waitFor();
  check(state.posted.reportDirs.at(-1) === null, 'Use default must save null');
  await page.getByRole('button', { name: 'OK' }).click();

  const recent = page.getByTestId('recent-reports');
  await recent.getByText('UNREAD', { exact: true }).waitFor();
  const recentLinks = recent.getByRole('link', { name: 'Open PDF report' });
  check((await recentLinks.count()) === 2, 'each report card needs an Open PDF report link');
  check((await recentLinks.first().getAttribute('href')) === reportUrl(reportId('NO_GO', 'UNREAD')), 'report card href mismatch');
  check((await recentLinks.first().getAttribute('target')) === '_blank', 'report link must open a new tab');
  await page.getByPlaceholder('Enter decoded number').fill(DECODED);
  await page.getByRole('button', { name: 'Find reports' }).click();
  await page.getByTestId('lookup-results').getByText('1 result').waitFor();
  await shot(page, '5-reports');

  // ---- Change password dialog
  await page.getByRole('button', { name: 'Change password' }).click();
  await page.getByRole('heading', { name: 'Change SETUP password' }).waitFor();
  await page.getByRole('button', { name: 'Cancel' }).click();

  const dialogs = await page.evaluate(() => window.__dialogs);
  check(dialogs.length === 0, `alert/confirm used: ${dialogs.join(', ')}`);
  // The deliberate 503 / 400 responses log "Failed to load resource" console errors; ignore only those.
  const unexpected = consoleErrors.filter((t) => !/status of (503|400)/.test(t));
  check(unexpected.length === 0, `console errors: ${unexpected.join(' | ')}`);
  check(pageErrors.length === 0, `page errors: ${pageErrors.join(' | ')}`);
  console.log('UI smoke test passed');
} finally {
  await browser?.close();
  server.close();
}
