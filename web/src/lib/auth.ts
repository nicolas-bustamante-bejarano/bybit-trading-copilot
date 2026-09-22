export const SESSION_COOKIE = "copilot_session";
const SESSION_SECONDS = 60 * 60 * 12;

function encode(bytes: Uint8Array) {
  return Buffer.from(bytes).toString("base64url");
}

function decode(value: string) {
  return new Uint8Array(Buffer.from(value, "base64url"));
}

async function key(secret: string) {
  return crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false,
    ["sign", "verify"],
  );
}

function secrets() {
  return {
    password: process.env.COPILOT_ACCESS_PASSWORD,
    secret: process.env.COPILOT_SESSION_SECRET,
  };
}

export function authDisabled() {
  return process.env.NODE_ENV !== "production" && process.env.COPILOT_AUTH_DISABLED === "true";
}

export function authConfigured() {
  const values = secrets();
  return Boolean(values.password && values.secret && values.secret.length >= 32);
}

export async function verifyPassword(candidate: string) {
  const values = secrets();
  if (!values.password || !values.secret || values.secret.length < 32) return false;
  const signingKey = await key(values.secret);
  const expected = await crypto.subtle.sign(
    "HMAC", signingKey, new TextEncoder().encode(values.password),
  );
  return crypto.subtle.verify(
    "HMAC", signingKey, expected, new TextEncoder().encode(candidate),
  );
}

export async function createSessionToken(now = Date.now()) {
  const { secret } = secrets();
  if (!secret || secret.length < 32) throw new Error("COPILOT_SESSION_SECRET is required");
  const expires = Math.floor(now / 1000) + SESSION_SECONDS;
  const payload = `v1.${expires}`;
  const signature = await crypto.subtle.sign(
    "HMAC", await key(secret), new TextEncoder().encode(payload),
  );
  return `${payload}.${encode(new Uint8Array(signature))}`;
}

export async function verifySessionToken(token: string | undefined, now = Date.now()) {
  if (authDisabled()) return true;
  const { secret } = secrets();
  if (!token || !secret || secret.length < 32) return false;
  const [version, expiresRaw, signatureRaw, ...extra] = token.split(".");
  if (version !== "v1" || extra.length || !expiresRaw || !signatureRaw) return false;
  const expires = Number(expiresRaw);
  if (!Number.isInteger(expires) || expires <= Math.floor(now / 1000)) return false;
  try {
    return crypto.subtle.verify(
      "HMAC", await key(secret), decode(signatureRaw),
      new TextEncoder().encode(`${version}.${expiresRaw}`),
    );
  } catch {
    return false;
  }
}
