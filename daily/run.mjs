import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, renameSync, rmdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { editorial, inSlot, istDate, select } from './policy.mjs';
import { publishers } from './meta.mjs';

const flags = new Set(process.argv.slice(2));
const live = flags.has('--live');
const fixture = flags.has('--fixture');
const scheduled = flags.has('--scheduled');
const collectOnly = flags.has('--collect-only');
const python = process.env.PYTHON ?? 'python3';
const now = fixture ? new Date('2026-11-24T05:30:00Z') : new Date();
const root = resolve(process.env.NELLORE_STATE ?? '.nellore');
const ledger = join(root, 'ledger.json');
const lock = join(root, 'lock');

function read(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function save(history) {
  const temporary = ledger + '.tmp';
  writeFileSync(temporary, JSON.stringify(history, null, 2), {mode: 0o600});
  renameSync(temporary, ledger);
}

async function main() {
  if (live && (!scheduled || fixture || collectOnly || process.env.NELLORE_LIVE !== 'true' || !process.env.NELLORE_STATE)) {
    throw new Error('Live publishing requires explicit launch settings, persistent state and a scheduled run');
  }
  if (scheduled && !inSlot(now)) return;
  const post = live ? publishers(process.env) : null;
  mkdirSync(root, {recursive: true, mode: 0o700});
  try {
    mkdirSync(lock);
  } catch (error) {
    if (error.code === 'EEXIST') {
      console.warn('An existing run holds the state lock');
      return;
    }
    throw error;
  }
  try {
    let history = [];
    try {
      history = read(ledger);
      if (!Array.isArray(history)) throw new Error('Invalid ledger');
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const source = fixture ? read('daily/fixture.json') : JSON.parse(execFileSync(
      python, ['daily/collect.py', istDate(now)], {encoding: 'utf8', timeout: 240000}));
    for (const error of source.errors ?? []) console.warn('Source unavailable:', error);
    if (source.errors?.length >= 4) throw new Error('All configured sources failed');
    const records = fixture ? [] : history;
    const item = select(source.news, source.jobs, records, now, fixture ? [] : read('posted_history.json'));
    if (!item) {
      console.log('No candidate cleared the publication checks');
      return;
    }
    if (collectOnly) {
      console.log('Candidate cleared checks:', item.key);
      return;
    }
    const day = istDate(now);
    if (live && records.filter(record => record.day === day).length >= 2) {
      console.log('Daily publishing limit reached');
      return;
    }
    const folder = join(root, fixture ? 'fixture' : item.key);
    mkdirSync(folder, {recursive: true});
    const data = editorial(item);
    writeFileSync(join(folder, 'editorial.json'), JSON.stringify(data, null, 2));
    writeFileSync(join(folder, 'caption.txt'), data.caption);
    execFileSync(python, ['daily/render.py', folder], {stdio: 'inherit', timeout: 360000});
    console.log('Preview artwork and reel:', folder);
    if (!post) return;

    // Write intent before calling Meta. Uncertain outcomes require manual reconciliation.
    const record = {key: item.key, title: item.title, links: item.links, day, channels: {}};
    history.push(record);
    save(history);
    const video = readFileSync(join(folder, 'reel.mp4'));
    let failed = false;
    for (const [channel, publish] of Object.entries(post)) {
      record.channels[channel] = {status: 'pending'};
      save(history);
      try {
        record.channels[channel] = {status: 'submitted', id: await publish(video, data.caption)};
      } catch (error) {
        failed = true;
        console.error(channel, error.message);
        record.channels[channel] = {status: 'review'};
      }
      save(history);
    }
    if (failed) throw new Error('Check Meta and reconcile channels marked review before any retry');
  } finally {
    rmdirSync(lock);
  }
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
