// ALYDAS-18, S3. The plugin's one door to the desk: which desk, and a POST to
// it with a timeout (and, since ALYDAS-379, S1, one GET for its metadata).
// Plain Node ESM with no dependencies, since the plugin has no build step.
// `connect.mjs` and `hook.mjs` both send through it.
//
// Nothing here logs. A header, a body and a token never reach the console or
// the terminal from this module, and an error it throws says only what went
// wrong with the connection, never what was sent.

import http from 'node:http';

// Design §2 and the plan: every call to the desk gives up after 3 s. A hook
// that waited longer would hold up the person's session. The connect poller,
// which holds up nobody, passes its own (connect.mjs POLL_TIMEOUT_MS).
export const DESK_TIMEOUT_MS = 3000;

// Gate 8, SEC-3. pipeline.py's url_refusal rule: https, or plain http only to
// the laptop itself, spelled exactly (`localhost.` is another name).
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

// ALYDAS-381, Gate 8. A local desk is served at http://<name>.localhost:<port>,
// and RFC 6761 6.3 makes every name under localhost this machine, so the name
// alone decides. It is never handed to a resolver, here or when the request
// connects (`sendToThisMachine`): S3 asked one and then let the request ask
// again, and a resolver that said loopback the first time and elsewhere the
// second was handed the bearer. `a.localhost.` and `localhost.example.com` do
// not end in `.localhost`, so neither is this machine.
//
// Ask ec2c79f7 (ALYDAS-392's ruling): a name with an empty label under it
// (`..localhost`, `a..localhost`) is no name a desk is served at, so it is
// not this machine either, and is refused like any other plain http address.
function underLocalhost(hostname) {
  if (!hostname.endsWith('.localhost')) return false;
  const labels = hostname.slice(0, -'.localhost'.length);
  return labels !== '' && labels.split('.').every((label) => label !== '');
}

// ALYDAS-391, and S3's smoke (ALYDAS-392). Whether `url` is plain http to
// this laptop: localhost, 127.0.0.1, [::1] or a name under localhost, the
// addresses deskRefusal lets plain http reach. GitHub's runners cannot reach
// one (setup.mjs), and requestThisMachine can. Not a URL is not local.
export function isLocalDesk(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  return parsed.protocol === 'http:' && (LOCAL_HOSTS.has(parsed.hostname) || underLocalhost(parsed.hostname));
}

// Null when a device code or a token may be sent to `url`, and otherwise the
// sentence that says why not. It names the scheme and host only, so a user,
// password or path in the address is never repeated. Async since ALYDAS-381,
// S3; Gate 8 took its lookup away and kept the shape, so the callers' awaits
// stand.
export async function deskRefusal(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return 'the desk address is not a URL, so nothing was sent to it.';
  }
  if (parsed.protocol === 'https:' && parsed.hostname !== '') return null;
  if (isLocalDesk(url)) return null;
  return `the desk at ${parsed.protocol}//${parsed.host} is not https, and plain http is only for localhost, *.localhost, 127.0.0.1 or [::1], each with no trailing dot, so nothing was sent to it.`;
}

// `ALYDASH_URL` with any query or fragment and then its trailing slashes cut,
// or null when none is set. ALYDAS-388: a query is never part of the desk, so
// a key pasted with the address is never printed by connect, setup or status,
// nor filed with the credential. Cut as text, not rebuilt through `new URL`,
// which would lowercase the host, so a stored entry still matches by
// `entry.desk ===`, and a value that is not a URL reaches deskRefusal as it
// was given. It holds no secret: the one shell variable the plugin reads.
// The plugin has no desk of its own (ALYDAS-283): it was one person's
// production desk, so a copy of the repository connected its laptops there.
// With null, connect uses the desk this laptop is already connected to or
// asks, and a hook uses the desk each stored credential names.
export function deskUrl(env = process.env) {
  const given = (env.ALYDASH_URL ?? '').trim().replace(/[?#][\s\S]*$/, '').replace(/\/+$/, '');
  return given === '' ? null : given;
}

// One request, a POST unless ALYDAS-379's GET says otherwise. The answer is
// `{ status, json }`, with `json` null when the body is not JSON, so a caller
// reads a refusal the same way as a success. A desk that cannot be reached,
// answers too late or redirects is a thrown Error whose message is safe to
// print. So is an address deskRefusal turns away, which is refused before
// anything is sent.
async function send(url, method, headers, body, timeoutMs) {
  const refusal = await deskRefusal(url);
  if (refusal !== null) throw new Error(refusal);
  const parsed = new URL(url);
  const deadline = `the desk did not answer within ${Math.ceil(timeoutMs / 1000)} s`;
  const unreachable = `could not reach the desk at ${parsed.origin}`;
  const redirected = 'the desk answered with a redirect, which is not followed';
  let status;
  let text;
  if (parsed.protocol === 'http:' && underLocalhost(parsed.hostname)) {
    ({ status, text } = await sendToThisMachine(parsed, method, headers, body, timeoutMs));
    if (status >= 300 && status < 400) throw new Error(redirected);
  } else {
    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body,
        // A redirect is not followed: the form or the bearer would go wherever
        // the desk pointed, and a GET is held to the same rule as the POSTs.
        redirect: 'manual',
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      const name = error instanceof Error ? error.name : '';
      if (name === 'TimeoutError' || name === 'AbortError') throw new Error(deadline);
      throw new Error(unreachable);
    }
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      throw new Error(redirected);
    }
    status = response.status;
    text = await response.text();
  }
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = null;
  }
  return { status, json };
}

// ALYDAS-381, Gate 8. The request to a *.localhost desk, made with node:http
// rather than fetch, because Node's fetch drops a Host header it is handed and
// sends the address it connected to instead. It connects to 127.0.0.1 on the
// URL's port and sends the URL's host as the Host, so the desk sees exactly
// what it saw when the name was resolved. The host is an address, so no
// resolver is asked; `lookup` refuses all the same, so a change that handed
// node:http a name would fail here rather than ask one. `agent: false` keeps a
// proxy the environment names out of it. node:http follows no redirect, and
// the caller refuses one. The timeout covers the connect and the whole body,
// as fetch's signal does.
//
// The answer is `{ status, headers, text }`, the headers by lowercase name,
// each one string (a repeated one, `set-cookie` above all, joined with ", "
// as fetch's Headers join it), so requestThisMachine hands smoke what
// `fetch` did. `[::1]` connects to ::1, every other name to 127.0.0.1.
function sendToThisMachine(parsed, method, headers, body, timeoutMs) {
  const deadline = `the desk did not answer within ${Math.ceil(timeoutMs / 1000)} s`;
  const unreachable = `could not reach the desk at ${parsed.origin}`;
  const signal = AbortSignal.timeout(timeoutMs);
  return new Promise((resolve, reject) => {
    const fail = () => reject(new Error(signal.aborted ? deadline : unreachable));
    const request = http.request(
      {
        host: parsed.hostname === '[::1]' ? '::1' : '127.0.0.1',
        port: parsed.port === '' ? 80 : Number(parsed.port),
        path: `${parsed.pathname}${parsed.search}`,
        method,
        headers: { ...headers, host: parsed.host },
        agent: false,
        lookup: (_name, _options, callback) => callback(new Error('a *.localhost desk is never looked up')),
        signal,
      },
      (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('error', fail);
        response.on('aborted', fail);
        response.on('end', () => {
          if (!response.complete) return fail();
          const answered = {};
          for (const [name, value] of Object.entries(response.headers)) {
            if (value !== undefined) answered[name.toLowerCase()] = Array.isArray(value) ? value.join(', ') : String(value);
          }
          resolve({ status: response.statusCode ?? 0, headers: answered, text: Buffer.concat(chunks).toString('utf8') });
        });
      },
    );
    request.on('error', fail);
    request.end(body);
  });
}

// For ALYDAS-392 (S3). One request to a desk on this laptop, made as the
// plugin's own are (no resolver asked, no proxy, the URL's host as the Host),
// for smoke, whose checks read status codes, headers and redirects the
// plugin's wrappers do not hand back. Any method, headers and body, and the
// caller's timeout. A redirect is returned, not followed and not thrown:
// what to do with one is the caller's to decide. An address that is not a
// local desk (isLocalDesk) is turned away before anything is sent, in
// deskRefusal's words when it refuses it and otherwise in this sentence.
export async function requestThisMachine(url, { method = 'GET', headers = {}, body, timeoutMs = DESK_TIMEOUT_MS } = {}) {
  const refusal = await deskRefusal(url);
  if (refusal !== null) throw new Error(refusal);
  const parsed = new URL(url);
  if (!isLocalDesk(url)) {
    throw new Error(`the desk at ${parsed.protocol}//${parsed.host} is not on this laptop, so nothing was sent to it.`);
  }
  return sendToThisMachine(parsed, method, headers, body, timeoutMs);
}

// A form, as RFC 8628 has the device and token endpoints read.
export function postForm(url, fields, { timeoutMs = DESK_TIMEOUT_MS } = {}) {
  return send(
    url,
    'POST',
    { 'content-type': 'application/x-www-form-urlencoded', accept: 'application/json' },
    new URLSearchParams(fields).toString(),
    timeoutMs,
  );
}

// JSON, with the session token as a bearer when one is given.
export function postJson(url, body, { token, timeoutMs = DESK_TIMEOUT_MS } = {}) {
  const headers = { 'content-type': 'application/json', accept: 'application/json' };
  if (token) headers.authorization = `Bearer ${token}`;
  return send(url, 'POST', headers, JSON.stringify(body), timeoutMs);
}

// ALYDAS-379, S1. A GET with no body and no token, for the desk's
// authorization server metadata (RFC 8414), which connect reads before it asks
// for scopes. It answers as the POSTs do.
export function getJson(url, { timeoutMs = DESK_TIMEOUT_MS } = {}) {
  return send(url, 'GET', { accept: 'application/json' }, undefined, timeoutMs);
}

// ALYDAS-450. Whether the desk still takes `token`: one GET to its MCP
// endpoint with the bearer and no query string (the endpoint answers any
// query 400 before it reads the token). It is there on every desk with agent
// access, which every plugin token needs, and answers 405 to a token it takes
// (it has no stream), 403 to one whose app has agent access switched off, and
// 401 to one that is revoked, expired or unknown (agent-access routes/mcp.ts).
// It counts against the token's rate limit, so the hook asks once per session
// start and status once per run. It answers as the POSTs do: a desk that
// cannot be reached is a thrown Error whose message is safe to print.
export function probeConnection(desk, token, { timeoutMs = DESK_TIMEOUT_MS } = {}) {
  const url = `${String(desk).replace(/\/+$/, '')}/api/mcp`;
  return send(url, 'GET', { accept: 'application/json', authorization: `Bearer ${token}` }, undefined, timeoutMs);
}

// What a probe's answer says of the connection, by its status code alone (the
// 401's `invalid_token` is in a header, which is not read): 'revoked' for a
// 401; 'switched-off' for a 403, which the endpoint answers a token it took
// whose app has agent access switched off (Gate 8, W4: every call is refused
// until it is switched back on, so it is not live); 'live' for any other
// answer the desk gives only a token it took (2xx, 405); and 'unknown' for
// anything else (a 429, a 5xx, a desk with no such route), which says nothing
// either way.
export function connectionOf(answer) {
  const status = answer?.status;
  if (status === 401) return 'revoked';
  if (status === 403) return 'switched-off';
  if (status === 405 || (status >= 200 && status < 300)) return 'live';
  return 'unknown';
}
