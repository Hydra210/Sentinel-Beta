import { post, request, mountIcons } from './common.js';
const $ = id => document.getElementById(id);
let mode = new URLSearchParams(location.search).get('mode') === 'create' ? 'create' : 'login';
let challenge = null;
const form = $('account-form');
function setMode(next) {
  mode = next; challenge = next === '2fa' ? challenge : null;
  $('form-error').textContent = ''; $('form-success').textContent = '';
  $('account-tabs').classList.toggle('hidden', !['login','create'].includes(mode));
  $('verify-email-screen').classList.add('hidden'); form.classList.remove('hidden');
  $('name-field').classList.toggle('hidden', mode !== 'create');
  $('email-field').classList.toggle('hidden', mode === '2fa');
  $('password-group').classList.toggle('hidden', ['forgot','2fa'].includes(mode));
  $('password-hint').classList.toggle('hidden', mode !== 'create');
  $('code-field').classList.toggle('hidden', mode !== '2fa');
  $('forgot-password').classList.toggle('hidden', mode !== 'login');
  $('back-to-login').classList.toggle('hidden', ['login','create'].includes(mode));
  $('display-name').required = mode === 'create'; $('display-name').disabled = mode !== 'create';
  $('email').required = mode !== '2fa'; $('email').disabled = mode === '2fa';
  $('password').required = ['login','create'].includes(mode); $('password').disabled = !['login','create'].includes(mode);
  $('password').minLength = mode === 'create' ? 8 : 1;
  $('password').autocomplete = mode === 'create' ? 'new-password' : 'current-password';
  $('verification-code').required = mode === '2fa'; $('verification-code').disabled = mode !== '2fa';
  $('remember-device-row').classList.toggle('hidden', !['login','2fa'].includes(mode));
  $('login-tab').classList.toggle('active', mode === 'login'); $('login-tab').setAttribute('aria-selected', String(mode === 'login'));
  $('create-tab').classList.toggle('active', mode === 'create'); $('create-tab').setAttribute('aria-selected', String(mode === 'create'));
  const copy = {
    login: ['Good to see you.', 'Sign in and put Sentinel on watch.', 'Log in to Sentinel'],
    create: ['Make it your own.', 'Your community. Your fresh workspace.', 'Create your account'],
    forgot: ['Back in control.', "We'll send you a link to reset your password.", 'Send reset link'],
    '2fa': ['One more check.', 'A small step to keep your account secure.', 'Verify and log in'],
  }[mode];
  $('form-title').textContent = copy[0]; $('form-subtitle').textContent = copy[1]; $('submit-label').textContent = copy[2];
}
$('login-tab').addEventListener('click', () => setMode('login'));
$('create-tab').addEventListener('click', () => setMode('create'));
$('forgot-password').addEventListener('click', () => setMode('forgot'));
$('back-to-login').addEventListener('click', () => setMode('login'));
$('verified-button').addEventListener('click', () => setMode('login'));
$('password-toggle').addEventListener('click', () => {
  const visible = $('password').type === 'password'; $('password').type = visible ? 'text' : 'password';
  $('password-toggle').setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
});
$('verification-code').addEventListener('input', event => { event.target.value = event.target.value.replace(/\D/g, ''); });
form.addEventListener('submit', async event => {
  event.preventDefault(); if (!form.reportValidity()) return;
  $('form-error').textContent = ''; $('form-success').textContent = '';
  const previousLabel = $('submit-label').textContent;
  $('account-submit').disabled = true; $('submit-label').textContent = 'Just a moment…';
  try {
    const email = $('email').value.trim();
    if (mode === 'create') {
      await post('/auth/register', { email, password: $('password').value, display_name: $('display-name').value.trim(), app: 'sentinel-web' });
      $('verify-email-address').textContent = email; form.classList.add('hidden'); $('account-tabs').classList.add('hidden');
      $('verify-email-screen').classList.remove('hidden'); $('back-to-login').classList.remove('hidden');
      $('form-title').textContent = 'Check your inbox.'; $('form-subtitle').textContent = 'Your workspace is almost ready.';
    } else if (mode === 'forgot') {
      await post('/auth/forgot-password', { email });
      $('form-success').textContent = 'If an account exists for this email, a reset link is on its way. Check your inbox and spam folder.';
    } else {
      const result = mode === '2fa'
        ? await post('/auth/verify-2fa', { challenge_token: challenge, code: $('verification-code').value.trim(), remember_me: $('remember-device').checked })
        : await post('/auth/login', { email, password: $('password').value, app: 'sentinel-web', remember_me: $('remember-device').checked });
      if (result.requires_2fa) {
        challenge = result.challenge_token; setMode('2fa'); $('verification-code').focus();
      } else {
        // EXE access and refresh tokens remain server-side; only a validated session can open the dashboard.
        await request('/auth/me'); location.assign('/sentinel/Dashboard');
      }
    }
  } catch (error) { $('form-error').textContent = error.message; }
  finally { $('account-submit').disabled = false; if ($('submit-label').textContent === 'Just a moment…') $('submit-label').textContent = previousLabel; }
});
// Retire obsolete device-local profile flags; no legacy account state is imported.
try { ['sentinel_exe_migrated','sentinel_exe_refresh_token','sentinel_profile_id','sentinel_profile_pin'].forEach(key => localStorage.removeItem(key)); } catch (_) {}
setMode(mode); mountIcons();
const returnTarget = new URLSearchParams(location.search).get('next');
function leaveIfSignedIn() {
  request('/auth/me').then(() => {
    const destination = returnTarget && returnTarget.startsWith('/sentinel/') && returnTarget !== '/sentinel/login' ? returnTarget : '/sentinel';
    location.replace(destination);
  }).catch(() => {});
}
leaveIfSignedIn();
window.addEventListener('pageshow', event => { if (event.persisted) leaveIfSignedIn(); });
const video = document.querySelector('.account-background video');
if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) video.pause();
