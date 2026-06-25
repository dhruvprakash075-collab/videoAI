// ponytail: API_BASE was always ''; kept as re-export for components that import it
export const API_BASE = '';

export async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export async function apiSend(path, body, method = 'POST') {
  const res = await fetch(path, { method, body });
  return res;
}
