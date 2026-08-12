(function () {
  var root = document.documentElement;
  var themeButton = document.querySelector(".theme-toggle");
  var copyButtons = document.querySelectorAll("[data-copy-target]");

  function currentTheme() {
    var saved = root.getAttribute("data-theme");
    if (saved) {
      return saved;
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function setThemeLabel() {
    if (!themeButton) {
      return;
    }
    var text = themeButton.querySelector(".theme-toggle__text");
    if (text) {
      text.textContent = currentTheme() === "dark" ? "Light" : "Dark";
    }
  }

  function writeClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }

    var field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.left = "-9999px";
    document.body.appendChild(field);
    field.select();
    var copied = document.execCommand("copy");
    document.body.removeChild(field);
    return copied ? Promise.resolve() : Promise.reject(new Error("copy failed"));
  }

  if (themeButton) {
    setThemeLabel();
    themeButton.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      localStorage.setItem("aurora-theme", next);
      setThemeLabel();
    });
  }

  copyButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.getAttribute("data-copy-target"));
      if (!target) {
        return;
      }

      writeClipboard(target.textContent.trim())
        .then(function () {
          var original = button.textContent;
          button.textContent = "Copied";
          window.setTimeout(function () {
            button.textContent = original;
          }, 1400);
        })
        .catch(function () {
          button.textContent = "Select";
        });
    });
  });
})();
