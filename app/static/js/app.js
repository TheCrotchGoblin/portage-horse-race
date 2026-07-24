// Minimal vanilla JS helpers. Most interactivity is server-rendered via HTMX;
// this file only holds small conveniences that don't need a round-trip.

// Confirm before any element with data-confirm is actioned.
document.addEventListener('click', function (e) {
  const el = e.target.closest('[data-confirm]');
  if (!el) return;
  if (!window.confirm(el.getAttribute('data-confirm'))) {
    e.preventDefault();
    e.stopPropagation();
  }
});

// Auto-focus the first element marked data-autofocus on page load.
window.addEventListener('DOMContentLoaded', function () {
  const el = document.querySelector('[data-autofocus]');
  if (el) el.focus();
});
