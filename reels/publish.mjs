import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { setTimeout as pause } from 'node:timers/promises';

const queue = JSON.parse(readFileSync('reels/queue.json'));
const ledgerPath = 'reels/ledger.json';
const ledger = JSON.parse(readFileSync(ledgerPath));
const now = Date.now();
const rows = new Set();
for (const item of queue) {
  if (!/^[a-z0-9-]{3,80}$/.test(item.id ?? '') || rows.has(item.id)) throw Error('Invalid or duplicate queue ID');
  rows.add(item.id);
  if (item.approved !== true) continue;
  const url = new URL(item.videoUrl);
  if (url.protocol !== 'https:' || url.username || url.password || url.port || url.search || url.hash ||
      !['d2ol7oe51mr4n9.cloudfront.net', 'videogen.vidiq.com'].includes(url.hostname) ||
      typeof item.caption !== 'string' || !/[\u0c05-\u0c39]/u.test(item.caption) || item.caption.length > 2200 ||
      !Number.isFinite(Date.parse(item.publishAfter)) || !Number.isFinite(Date.parse(item.expiresAt)) ||
      Date.parse(item.expiresAt) <= Date.parse(item.publishAfter)) {
    throw Error('Invalid approved entry: ' + item.id);
  }
}
const ready = queue.filter(item => item.approved === true && !ledger[item.id] &&
  Date.parse(item.publishAfter) <= now && now < Date.parse(item.expiresAt))
  .sort((a, b) => Date.parse(a.publishAfter) - Date.parse(b.publishAfter));

if (process.argv.includes('--validate')) {
  console.log('Queue valid. Ready entries:', ready.length);
  process.exit(0);
}
const oneTimeReel = 'handmade-first-order-telugu-english-20261125';
const immediatePublish = process.env.GITHUB_EVENT_NAME === 'push' &&
  process.env.GITHUB_RUN_ATTEMPT === '1' &&
  JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH)).head_commit?.message === 'Publish approved Telugu English reel once' &&
  ready[0]?.id === oneTimeReel;
const live = process.env.LIVE === 'true' || immediatePublish;
if (live && ready.length === 0) {
  console.log('No approved, unexpired reel is ready.');
  process.exit(0);
}
const account = process.env.IG_USER_ID;
const token = process.env.META_TOKEN;
if (!/^\d+$/.test(account ?? '') || !token) throw Error('Missing Meta publishing credentials');
const graph = 'https://graph.facebook.com/v25.0/';

async function request(path, params, post = false) {
  const query = new URLSearchParams(params);
  const res = await fetch(graph + path + (post ? '' : '?' + query), {
    headers: {Authorization: 'Bearer ' + token},
    method: post ? 'POST' : 'GET',
    body: post ? query : undefined,
    redirect: 'error',
    signal: AbortSignal.timeout(30000),
  });
  const value = await res.json();
  if (!res.ok || value.error) throw Error(path.split('/').at(-1) + ' rejected: HTTP ' + res.status + ', Meta code ' + (value.error?.code ?? 'unknown'));
  return value;
}

const profile = await request(account, {fields: 'username'});
if (profile.username?.toLowerCase() !== 'nelloredaily') throw Error('Configured account is not @nelloredaily');
if (!live) {
  console.log('Dry run verified @nelloredaily access. Ready entries:', ready.length);
  process.exit(0);
}

function save(item, state) {
  ledger[item.id] = state;
  writeFileSync(ledgerPath, JSON.stringify(ledger, null, 2) + '\n');
  const git = (...args) => execFileSync('git', args, {stdio: 'inherit'});
  git('add', ledgerPath);
  git('-c', 'user.name=Nellore Daily', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
    'commit', '-m', 'Record Instagram publication state [skip ci]');
  git('push', 'origin', 'HEAD:main');
}
const [item] = ready;
let pending = false;
const {id: container} = await request(account + '/media',
  {media_type: 'REELS', video_url: item.videoUrl, caption: item.caption}, true);
if (!/^\d+$/.test(container ?? '')) throw Error('Missing Instagram media container');
for (let i = 0; i < 35; i++) {
  const {status_code: status} = await request(container, {fields: 'status_code'});
  if (status === 'FINISHED') {
    if (Date.now() >= Date.parse(item.expiresAt)) throw Error('Reel expired during processing');
    // Persist the intent before the irreversible call. Uncertain results are never retried blindly.
    save(item, {status: 'pending-review', container, at: new Date().toISOString()});
    pending = true;
    break;
  }
  if (status === 'ERROR' || status === 'EXPIRED') throw Error('Instagram media processing failed: ' + status);
  await pause(5000);
}
if (!pending) throw Error('Media is not ready; the next run can try again');
const {id} = await request(account + '/media_publish', {creation_id: container}, true);
if (!/^\d+$/.test(id ?? '')) throw Error('Unconfirmed publication; inspect Instagram before retrying');
let permalink;
try {
  ({permalink} = await request(id, {fields: 'permalink'}));
} catch {
  console.warn('Instagram returned a media ID but the permalink lookup failed.');
}
save(item, {status: 'published', id, permalink, at: new Date().toISOString()});
console.log(permalink || 'Published Instagram media ' + id);
