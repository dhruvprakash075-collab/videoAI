export const API_BASE = '';

export async function apiGet(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export async function apiSend(path, body, method = 'POST') {
  const res = await fetch(`${API_BASE}${path}`, { method, body });
  return res;
}
