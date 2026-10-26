(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll(".nav a"));
  var sections = links.map(function (link) {
    return document.querySelector(link.getAttribute("href"));
  }).filter(Boolean);

  function updateNav() {
    var current = sections[0];
    sections.forEach(function (section) {
      if (section.getBoundingClientRect().top <= 140) current = section;
    });
    links.forEach(function (link) {
      link.classList.toggle("is-active", current && link.getAttribute("href") === "#" + current.id);
    });
  }

  updateNav();
  window.addEventListener("scroll", updateNav, { passive: true });
})();
