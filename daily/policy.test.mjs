import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { canonical, editorial, inSlot, select } from './policy.mjs';
import { publishers } from './meta.mjs';

const {news} = JSON.parse(readFileSync(new URL('./fixture.json', import.meta.url)));
const now = new Date('2026-11-24T05:30:00Z');
const job = {title: 'Nellore district recruitment notice', opened: '2026-11-23',
  deadline: '2026-11-25', eligibility: 'B.Sc.', link: 'https://cdn.s3waas.gov.in/notice.pdf'};

test('37 IST slots, including 05:00 and 23:00', () => {
  const midnight = Date.parse('2026-11-23T18:30:00Z');
  const slots = Array.from({length: 48}, (_, index) => index)
    .filter(index => inSlot(new Date(midnight + index * 1800000)));
  assert.equal(slots.length, 37);
  assert.equal(slots[0], 10);
  assert.equal(slots.at(-1), 46);
});

test('news needs local Telugu headline, fresh timestamps and two distinct publishers', () => {
  assert.equal(select(news, [], [], now).kind, 'news');
  assert.equal(select(news.slice(0, 1), [], [], now), undefined);
  assert.equal(select([news[0], {...news[1], link: news[0].link + '2'}], [], [], now), undefined);
  for (const published of ['', '2026-11-23T04:00:00Z', '2026-11-25T04:00:00Z']) {
    assert.equal(select(news.map(item => ({...item, published})), [], [], now), undefined);
  }
  for (const title of ['విజయవాడ నగరంలో ఆదివారం పుస్తక ప్రదర్శన ప్రారంభం',
    'నెల్లూరు నగరంలో ఉద్యోగ నియామక ప్రకటన విడుదల']) {
    assert.equal(select(news.map(item => ({...item, title})), [], [], now), undefined);
  }
});

test('history suppresses previous links and cross-publisher title duplicates', () => {
  const first = select(news, [], [], now);
  assert.equal(select(news, [], [first], now), undefined);
  assert.equal(select(news, [], [], now, [news[0].link + '?utm_source=test#part']), undefined);
  assert.equal(select(news.map(item => ({...item, link: item.link + '/updated'})), [], [first], now), undefined);
  assert.equal(canonical('https://example.com/?utm_source=test&job=2#top'), 'https://example.com/?job=2');
});

test('jobs must be officially hosted, open and unexpired; captions include application details', () => {
  const item = select(news, [job], [], now);
  assert.equal(item.kind, 'job');
  assert.match(editorial(item).caption, /2026-11-25/);
  assert.match(editorial(item).caption, /B\.Sc\./);
  assert.ok(editorial(item).caption.includes(job.link));
  for (const change of [{deadline: '2026-11-23'}, {deadline: '2026-11-24'},
    {opened: '2026-11-25'}, {link: 'https://jobs.example/recruitment'}]) {
    assert.equal(select([], [{...job, ...change}], [], now), undefined);
  }
});

const env = {META_GRAPH_VERSION: 'v25.0', IG_USER_ID: '12', FB_PAGE_ID: '34',
  META_IG_TOKEN: 'ig', META_PAGE_TOKEN: 'fb'};
const uri = 'https://rupload.facebook.com/reels/123';
const ok = result => ({ok: true, json: async () => result});

test('mocked Meta uploads publish only after the expected steps', async () => {
  const calls = [];
  const results = [{id: '56', uri}, {success: true}, {status_code: 'FINISHED'}, {id: '78'},
    {video_id: '910', upload_url: uri}, {success: true}, {success: true}];
  const post = publishers(env, async (url, options) => {
    calls.push([String(url), options]);
    return ok(results.shift());
  });
  assert.equal(await post.instagram(Buffer.from('video'), 'caption'), '78');
  assert.equal(await post.facebook(Buffer.from('video'), 'caption'), '910');
  assert.equal(calls[2][1].method, 'GET');
  assert.equal(calls[3][1].body.get('creation_id'), '56');
  assert.equal(calls[6][1].body.get('upload_phase'), 'finish');
  assert.equal(calls[5][1].headers.Authorization, 'OAuth fb');
});

test('Meta upload credentials never go to an arbitrary returned host', async () => {
  let requests = 0;
  const post = publishers(env, async () => {
    requests++;
    return ok({id: '56', uri: 'https://example.com/reels/123'});
  });
  await assert.rejects(post.instagram(Buffer.from('video'), 'caption'), /Unexpected Meta upload host/);
  assert.equal(requests, 1);
});
