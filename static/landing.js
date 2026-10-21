import { icon, mountIcons } from './common.js';
const panels = {
  monitor: `<div class="detail-top"><span>GROUP MONITORING</span><span class="live-badge">ON WATCH</span></div><h3>Your groups. Covered.</h3><p>One clear view of the communities you protect.</p><div class="detail-row"><span class="group-symbol">CG</span><div>Community group<small>Audio · Models · Decals</small></div><span class="mini-badge allowed">MONITORING</span></div><div class="detail-row"><span class="group-symbol">DG</span><div>Development group<small>Audio · Models · Meshes</small></div><span class="mini-badge allowed">MONITORING</span></div><div class="detail-row"><span class="group-symbol">SC</span><div>Studio collective<small>Audio · Video · Animation</small></div><span class="mini-badge allowed">MONITORING</span></div><div class="detail-footer">${icon('activity')} CHECKING EVERY 60 SECONDS <span style="margin-left:auto">ILLUSTRATIVE PREVIEW</span></div>`,
  rules: `<div class="detail-top"><span>MODERATION RULES</span><span class="mono subtle">YOUR WORKSPACE</span></div><h3>Only what you choose.</h3><p>Select asset types. Trust the right people. Fine-tune the rest.</p><div class="detail-chips"><span>${icon('check')} Audio</span><span>${icon('check')} Models</span><span>${icon('check')} Decals</span><span>${icon('check')} Meshes</span></div><div class="detail-row" style="margin-top:16px"><div>Trusted uploaders<small>Global and per-asset allowlists</small></div>${icon('shield')}</div><div class="detail-row"><div>Archive delay<small>A little time before Sentinel takes action</small></div><span class="mono">10 SECONDS</span></div><div class="detail-footer">${icon('sliders')} YOUR RULES, CONSISTENTLY ENFORCED.</div>`,
  history: `<div class="detail-top"><span>MODERATION HISTORY</span><span class="mono subtle">FULL VISIBILITY</span></div><h3>Every action. In view.</h3><p>Search your history and restore assets when you need to.</p><div class="detail-row">${icon('audio')}<div>Unauthorized audio<small>Community group · Just now</small></div><span class="mini-badge">ARCHIVED</span></div><div class="detail-row">${icon('box')}<div>Community model<small>Development group · 2 minutes ago</small></div><span class="mini-badge allowed">RESTORED</span></div><div class="detail-row">${icon('audio')}<div>New audio upload<small>Studio collective · 4 minutes ago</small></div><span class="mini-badge">ARCHIVED</span></div><div class="detail-footer">${icon('restore')} CHANGE YOUR MIND. KEEP YOUR CONTROL.</div>`,
};
const preview = document.getElementById('detail-preview');
function setPreview(name) {
  preview.innerHTML = panels[name];
  document.querySelectorAll('[data-preview]').forEach(button => {
    const active = button.dataset.preview === name;
    button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active));
  });
}
document.querySelectorAll('[data-preview]').forEach(button => {
  button.addEventListener('click', () => setPreview(button.dataset.preview));
  button.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft'].includes(event.key)) return;
    event.preventDefault(); const buttons = [...document.querySelectorAll('[data-preview]')];
    const next = buttons[(buttons.indexOf(button) + (['ArrowDown','ArrowRight'].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length];
    next.focus(); setPreview(next.dataset.preview);
  });
});
setPreview('monitor'); mountIcons();
document.getElementById('year').textContent = new Date().getFullYear();
const video = document.getElementById('hero-video');
const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
function updateMotion() { if (motion.matches) video.pause(); else video.play().catch(() => {}); }
motion.addEventListener('change', updateMotion); updateMotion();
document.addEventListener('visibilitychange', () => { if (document.hidden) video.pause(); else updateMotion(); });
