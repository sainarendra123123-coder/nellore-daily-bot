const trustedUpload = (value) => {
  const url = new URL(value);
  if (url.origin !== 'https://rupload.facebook.com' || url.username || url.password) {
    throw new Error('Unexpected Meta upload host');
  }
  return url;
};

export function publishers(env, fetchImpl = fetch, pause = (ms) => new Promise(resolve => setTimeout(resolve, ms))) {
  const {META_GRAPH_VERSION: version, IG_USER_ID: instagram, FB_PAGE_ID: facebook,
    META_IG_TOKEN: igToken, META_PAGE_TOKEN: pageToken} = env;
  if (!/^v\d+\.\d+$/.test(version ?? '') ||
      ![instagram, facebook].every(id => /^\d+$/.test(id ?? '')) || !igToken || !pageToken) {
    throw new Error('Set the Graph version, both numeric account IDs and both Meta tokens');
  }
  const graph = `https://graph.facebook.com/${version}`;

  async function request(url, token, fields, binary) {
    const headers = binary
      ? {Authorization: `OAuth ${token}`, offset: '0', file_size: String(binary.length), 'Content-Type': 'application/octet-stream'}
      : {Authorization: `Bearer ${token}`};
    const response = await fetchImpl(url, {
      method: fields || binary ? 'POST' : 'GET', headers,
      body: binary ?? (fields ? new URLSearchParams(fields) : undefined),
      signal: AbortSignal.timeout(binary ? 90000 : 30000), redirect: 'error',
    });
    const result = await response.json();
    if (!response.ok || result.error) {
      throw new Error(`Meta rejected request (HTTP ${response.status}, code ${result.error?.code ?? 'unknown'})`);
    }
    return result;
  }

  return {
    async instagram(video, caption) {
      const {id, uri} = await request(`${graph}/${instagram}/media`, igToken,
        {media_type: 'REELS', upload_type: 'resumable', caption});
      if (!id || !uri) throw new Error('Instagram container missing');
      const upload = await request(trustedUpload(uri), igToken, null, video);
      if (!upload.success) throw new Error('Instagram upload failed');
      for (let attempt = 0; attempt < 18; attempt++) {
        const {status_code: status} = await request(`${graph}/${id}?fields=status_code`, igToken);
        if (status === 'FINISHED') {
          const published = await request(`${graph}/${instagram}/media_publish`, igToken, {creation_id: id});
          if (!published.id) throw new Error('Instagram publish ID missing');
          return published.id;
        }
        if (status === 'ERROR' || status === 'EXPIRED') throw new Error('Instagram container failed');
        await pause(10000);
      }
      throw new Error('Instagram container timed out');
    },
    async facebook(video, caption) {
      const endpoint = `${graph}/${facebook}/video_reels`;
      const {video_id: id, upload_url: uri} = await request(endpoint, pageToken, {upload_phase: 'start'});
      if (!id || !uri) throw new Error('Facebook upload session missing');
      const upload = await request(trustedUpload(uri), pageToken, null, video);
      if (!upload.success) throw new Error('Facebook upload failed');
      const result = await request(endpoint, pageToken, {
        upload_phase: 'finish', video_id: id, video_state: 'PUBLISHED', description: caption,
      });
      if (!result.success) throw new Error('Facebook publish failed');
      return id;
    },
  };
}
