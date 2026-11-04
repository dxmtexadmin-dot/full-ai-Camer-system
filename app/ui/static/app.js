/* Kichik yordamchi skriptlar. Bu yerda hech qanday ish mantig'i yo'q:
   faqat forma/ko'rinish bilan bog'liq yengilliklar. */

(function () {
  "use strict";

  // --- mobil yon panel ---------------------------------------------------
  var toggle = document.querySelector("[data-menu-toggle]");
  var sidebar = document.querySelector(".sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      sidebar.classList.toggle("open");
    });
  }

  // --- soat --------------------------------------------------------------
  var clock = document.querySelector("[data-clock]");
  if (clock) {
    var tick = function () {
      var now = new Date();
      clock.textContent = now.toLocaleTimeString("uz-UZ", { hour12: false });
    };
    tick();
    window.setInterval(tick, 1000);
  }

  // --- tasdiqlash oynasi -------------------------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  });

  // --- sekin ishlaydigan formalarni ikki marta yubormaslik ----------------
  // Rasm yuklash bir necha sekund davom etadi: tugma o'chiriladi va holat
  // yoziladi, aks holda operator tugmani qayta-qayta bosadi.
  document.querySelectorAll('form[method="post"], form:not([method])').forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (event.defaultPrevented) return;
      var button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;
      var original = button.textContent;
      button.disabled = true;
      button.textContent = "Yuborilmoqda...";
      // Sahifa almashmasa (tarmoq xatosi, bekor qilish) tugma qayta yoqiladi.
      window.setTimeout(function () {
        button.disabled = false;
        button.textContent = original;
      }, 20000);
    });
  });

  // --- kamera manbasi maydoni -------------------------------------------
  var sourceType = document.querySelector("[data-source-type]");
  var sourceInput = document.querySelector("[data-source-input]");
  if (sourceType && sourceInput) {
    var syncSource = function () {
      var value = sourceType.value;
      var row = sourceInput.closest(".field");
      if (row) {
        row.style.display = value === "simulation" ? "none" : "";
      }
      sourceInput.placeholder =
        value === "usb"
          ? "0 (birinchi USB kamera)"
          : value === "file"
            ? "C:\\video\\kirish.mp4"
            : "rtsp://user:***@192.168.1.64:554/Streaming/Channels/101";
    };
    sourceType.addEventListener("change", syncSource);
    syncSource();
  }

  // --- inventar amali: kerakli maydonlarni ko'rsatish --------------------
  var opSelect = document.querySelector("[data-inventory-operation]");
  if (opSelect) {
    var deltaField = document.querySelector("[data-field-delta]");
    var qtyField = document.querySelector("[data-field-quantity]");
    var locationField = document.querySelector("[data-field-location]");
    var syncOperation = function () {
      var value = opSelect.value;
      if (deltaField) deltaField.style.display = value === "correct" ? "" : "none";
      if (qtyField) qtyField.style.display = value === "correct" ? "none" : "";
      if (locationField) locationField.style.display = value === "transfer" || value === "correct" ? "" : "";
    };
    opSelect.addEventListener("change", syncOperation);
    syncOperation();
  }

  // --- jonli oqimni qayta ulash -----------------------------------------
  document.querySelectorAll("[data-stream]").forEach(function (img) {
    img.addEventListener("error", function () {
      var src = img.getAttribute("data-stream");
      window.setTimeout(function () {
        img.src = src + (src.indexOf("?") === -1 ? "?" : "&") + "t=" + Date.now();
      }, 3000);
    });
  });

  // --- kadr lavhasi ("kameradan rasm olish" bloki) -----------------------
  var snapshots = document.querySelectorAll("img[data-snapshot]");
  if (snapshots.length) {
    var refreshSnapshots = function () {
      snapshots.forEach(function (img) {
        var base = img.getAttribute("data-snapshot");
        img.src = base + (base.indexOf("?") === -1 ? "?" : "&") + "t=" + Date.now();
      });
    };
    refreshSnapshots();
    window.setInterval(refreshSnapshots, 4000);
  }

  // --- holat kartochkalarini yangilash ----------------------------------
  var statusNodes = document.querySelectorAll("[data-camera-status]");
  if (statusNodes.length) {
    var refresh = function () {
      statusNodes.forEach(function (node) {
        var code = node.getAttribute("data-camera-status");
        fetch("/api/cameras/" + encodeURIComponent(code) + "/status", {
          headers: { Accept: "application/json" },
        })
          .then(function (response) {
            return response.ok ? response.json() : null;
          })
          .then(function (data) {
            if (!data) return;
            var badge = node.querySelector("[data-status-badge]");
            if (badge) {
              badge.textContent = data.status_label || data.status || "—";
              badge.className = "badge " + (data.running ? "ok" : "bad");
            }
            var frames = node.querySelector("[data-frames]");
            if (frames) frames.textContent = data.frames_captured || 0;
            var dropped = node.querySelector("[data-dropped]");
            if (dropped) dropped.textContent = data.frames_dropped || 0;
            var detected = node.querySelector("[data-detected]");
            if (detected) detected.textContent = data.tracks || 0;
          })
          .catch(function () {
            /* tarmoq uzilishi - keyingi urinishda qayta tekshiriladi */
          });
      });
    };
    window.setInterval(refresh, 4000);
  }

  // --- bitta formada turli amal tugmalari --------------------------------
  function reasonField(form) {
    return form.querySelector("[data-reason-input]");
  }

  function reasonError(form) {
    return form.querySelector("[data-reason-error]");
  }

  function markReasonInvalid(form, invalid) {
    var reason = reasonField(form);
    var error = reasonError(form);
    if (reason) {
      if (invalid) reason.setAttribute("aria-invalid", "true");
      else reason.removeAttribute("aria-invalid");
    }
    if (error) error.hidden = !invalid;
  }

  document.querySelectorAll("[data-reason-input]").forEach(function (reason) {
    var form = reason.closest("form");
    if (!form) return;
    reason.addEventListener("input", function () {
      if (reason.value.trim()) markReasonInvalid(form, false);
    });
  });

  document.querySelectorAll("[data-decision]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      var form = button.closest("form");
      if (!form) return;
      var decision = button.getAttribute("data-decision");
      var input = form.querySelector("[data-decision-input]");
      if (input) input.value = decision;
      // Rad etish sababsiz qabul qilinmaydi: server ham tekshiradi, lekin
      // foydalanuvchi sahifa yangilanishini kutmasdan xabarni ko'rishi kerak.
      if (decision === "REJECTED") {
        var reason = reasonField(form);
        if (reason && !reason.value.trim()) {
          event.preventDefault();
          markReasonInvalid(form, true);
          reason.focus();
          return;
        }
      }
      markReasonInvalid(form, false);
    });
  });

  // --- xavfli amallar uchun tasdiqlash -----------------------------------
  document.querySelectorAll("[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) {
        event.preventDefault();
      }
    });
  });
})();
