export const API = '/sentinel/api';
export const icons = {
  shield: '<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  sliders: '<path d="M4 21v-7m0-4V3m8 18V12m0-4V3m8 18v-3m0-4V3M1 14h6m2-6h6m2 10h6"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m7 14 5-5-5-5M21 12H9"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7"/><circle cx="12" cy="12" r="3"/>',
  archive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v13h14V8m-10 4h6"/>',
  search: '<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
  play: '<path d="m8 4 13 8-13 8z"/>',
  pause: '<path d="M7 4v16M17 4v16"/>',
  audio: '<path d="M4 10v4m4-7v10m4-13v16m4-13v10m4-7v4"/>',
  box: '<path d="m12 3 9 5v9l-9 5-9-5V8zM3 8l9 5 9-5m-9 5v9M7 5l9 5"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
  external: '<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',
  restore: '<path d="M3 11a9 9 0 1 1 2.5 7M3 4v7h7"/>',
};
export function icon(name, cls = '') {
  return `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.shield}</svg>`;
}
export function mountIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
}
export function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
export async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(API + path, {
      credentials: 'same-origin', ...options, signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-Sentinel-Request': '1', ...options.headers },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(e => e.msg).join('. ') : data.error || `Request failed (${response.status}). Please try again.`;
      const error = new Error(message); error.status = response.status; throw error;
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The server took too long to respond. Please try again.');
    if (error instanceof TypeError) throw new Error('Could not reach Sentinel. Check your connection and try again.');
    throw error;
  } finally { clearTimeout(timeout); }
}
export function post(path, body = {}) { return request(path, { method: 'POST', body: JSON.stringify(body) }); }
export function toast(message, type = 'success') {
  let container = document.getElementById('toasts');
  if (!container) { container = document.createElement('div'); container.id = 'toasts'; container.setAttribute('role', 'status'); document.body.append(container); }
  const item = document.createElement('div'); item.className = `toast ${type}`; item.textContent = message; container.append(item);
  setTimeout(() => item.remove(), 5000);
}
mountIcons();
