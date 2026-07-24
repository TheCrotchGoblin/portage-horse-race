// Minimal vanilla JS helpers. Most interactivity is server-rendered via HTMX;
// this file only holds small conveniences that don't need a round-trip.

// Confirm ONLY when a form with data-confirm is actually submitted, or when a
// link with data-confirm is clicked. Never when clicking/focusing a field
// inside the form — that was firing the prompt on every input click.
document.addEventListener('submit', function (e) {
  const form = e.target.closest('form[data-confirm]');
  if (form && !window.confirm(form.getAttribute('data-confirm'))) {
    e.preventDefault();
  }
});

document.addEventListener('click', function (e) {
  // Links only. Buttons inside forms are handled by the submit listener above.
  const link = e.target.closest('a[data-confirm]');
  if (link && !window.confirm(link.getAttribute('data-confirm'))) {
    e.preventDefault();
    e.stopPropagation();
  }
});

// Auto-focus the first element marked data-autofocus on page load.
window.addEventListener('DOMContentLoaded', function () {
  const el = document.querySelector('[data-autofocus]');
  if (el) el.focus();
});
