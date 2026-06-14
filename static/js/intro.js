/* Intro splash — shows the CAU mark on a soft gradient on first entry,
   then fades out. Once per session so repeat visits aren't slowed. */
(function () {
  try { if (sessionStorage.getItem('tsl_intro')) return; } catch (e) {}
  var logo = document.documentElement.getAttribute('data-intrologo') || '/static/images/cau.jpg';

  var o = document.createElement('div');
  o.className = 'intro-overlay';
  o.setAttribute('aria-hidden', 'true');
  o.innerHTML =
    '<div class="intro-mark">' +
      '<img src="' + logo + '" alt="">' +
      '<span>Trauma Stress Lab</span>' +
    '</div>';

  function mount() {
    document.body.appendChild(o);
    document.body.style.overflow = 'hidden';
    setTimeout(finish, 1500);
  }
  function finish() {
    o.classList.add('hide');
    document.body.style.overflow = '';
    try { sessionStorage.setItem('tsl_intro', '1'); } catch (e) {}
    setTimeout(function () { if (o.parentNode) o.parentNode.removeChild(o); }, 700);
  }

  if (document.body) mount();
  else document.addEventListener('DOMContentLoaded', mount);
})();
