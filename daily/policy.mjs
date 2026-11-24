import { createHash } from 'node:crypto';

export const district = 'https://spsnellore.ap.gov.in';
const day = 86400000;
const offset = 330 * 60000;

export const istDate = (now) => new Date(+now + offset).toISOString().slice(0, 10);
export const inSlot = (now) => {
  const local = new Date(+now + offset);
  const minute = local.getUTCHours() * 60 + local.getUTCMinutes();
  return minute >= 300 && minute <= 1380 && minute % 30 === 0;
};

export function canonical(link) {
  try {
    const url = new URL(link);
    if (url.protocol !== 'https:' || url.username || url.password || url.port) return '';
    url.hash = '';
    for (const key of [...url.searchParams.keys()]) {
      if (/^(utm_|fbclid$|gclid$|oc$)/i.test(key)) url.searchParams.delete(key);
    }
    url.searchParams.sort();
    return url.href;
  } catch {
    return '';
  }
}

const words = (title) => new Set(title.normalize('NFKC').toLowerCase().match(/[\p{L}\p{M}\p{N}]+/gu) ?? []);
export function similar(first, second) {
  const a = words(first);
  const b = words(second);
  const overlap = [...a].filter(word => b.has(word)).length;
  return overlap >= 4 && overlap / new Set([...a, ...b]).size >= 0.8;
}

const isTelugu = /[\u0c05-\u0c39]/u;
const local = /నెల్లూరు|కావలి|ఆత్మకూరు|కందుకూరు|ఉదయగిరి|\bnellore\b/iu;
const jobStory = /ఉద్యోగ|నియామక|రిక్రూట్|job|recruitment|vacanc/iu;
const newsFresh = ({published}, now) => {
  const time = Date.parse(published);
  return time <= +now && time >= +now - day;
};
const headline = (title) => title.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, ' ').trim();
const publisher = (link) => new URL(link).hostname.replace(/^www\./, '');

export function select(news, jobs, history, now, legacy = []) {
  const links = new Set([...legacy.map(canonical), ...history.flatMap(item => item.links)]);
  const fresh = news.map(item => ({...item, link: canonical(item.link)}))
    .filter(item => item.link && newsFresh(item, now));
  const unique = (item) => !item.links.some(link => links.has(link)) &&
    !history.some(old => old.key === item.key || similar(old.title, item.title));
  const key = (value) => createHash('sha256').update(value).digest('hex').slice(0, 24);
  const verifiedJobs = jobs.filter(item =>
    canonical(item.link) === item.link &&
    [new URL(district).hostname, 'cdn.s3waas.gov.in'].includes(publisher(item.link)) &&
    /^\d{4}-\d{2}-\d{2}$/.test(item.deadline) &&
    item.opened <= istDate(now) && item.deadline > istDate(now))
    .map(item => ({...item, kind: 'job', links: [item.link], key: key(item.link)}));
  const verifiedNews = fresh.flatMap(item => {
    if (!isTelugu.test(item.title) || !local.test(item.title) || jobStory.test(item.title)) return [];
    const corroboration = fresh.find(other =>
      publisher(other.link) !== publisher(item.link) && headline(other.title) === headline(item.title));
    if (!corroboration) return [];
    const links = [item.link, corroboration.link].sort();
    return [{...item, kind: 'news', links, key: key(links.join('\n'))}];
  }).sort((a, b) => Date.parse(b.published) - Date.parse(a.published));
  return [...verifiedJobs, ...verifiedNews].find(unique);
}

export function editorial(item) {
  const label = item.kind === 'job' ? 'ఉద్యోగ సమాచారం' : 'నెల్లూరు వార్త';
  const segments = item.kind === 'job'
    ? ['నెల్లూరు జిల్లా ఉద్యోగ సమాచారం.', 'దరఖాస్తు వివరాలకు అధికారిక ప్రకటన చూడండి.',
       'దరఖాస్తుకు చివరి తేదీ ' + item.deadline.split('-').reverse().join('/') + '.']
    : ['నెల్లూరు నుంచి తాజా సమాచారం.', item.title];
  const details = item.kind === 'job' ? [
    'చివరి తేదీ • ' + item.deadline,
    'అర్హత • ' + (item.eligibility || 'అధికారిక ప్రకటనలో చూడండి'),
  ] : [];
  const caption = [label, item.title, ...details,
    item.kind === 'job' ? 'అధికారిక ప్రకటన' : 'వార్తా మూలాలు', ...item.links].join('\n');
  return {label, title: item.title, segments, details, caption};
}
