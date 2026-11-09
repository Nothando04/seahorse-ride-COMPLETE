/* Seahorse Ride — progressive enhancement. Every form also works server-side without JavaScript. */
(function () {
  "use strict";
  document.documentElement.classList.add("js");
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const money = (cents) => "R" + (cents / 100).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace(/,/g, " ");

  // ---- navigation toggles
  $$("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.toggle);
      const open = target.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });

  // ---- confirmation prompts on destructive actions
  $$("[data-confirm]").forEach((el) => {
    el.addEventListener("click", (e) => { if (!window.confirm(el.dataset.confirm)) e.preventDefault(); });
  });

  // ---- prevent double submission
  $$("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type=submit]:not([data-keep]), button:not([type]):not([data-keep])");
      if (btn && form.checkValidity()) {
        setTimeout(() => { btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = "Please wait…"; }, 0);
      }
    });
  });

  // ---- booking form
  const form = $("form[data-booking]");
  if (form) initBooking(form);

  function initBooking(form) {
    const exps = JSON.parse(form.dataset.exps || "[]");
    const addons = JSON.parse(form.dataset.addons || "[]");
    const ageGroups = JSON.parse(form.dataset.ageGroups || "[]");
    const levels = JSON.parse(form.dataset.levels || "[]");
    const steps = $$(".step", form);
    const markers = $$(".stepper li");
    let current = 0;

    // Start on the first step that has a server-side error.
    const errStep = steps.findIndex((s) => s.querySelector(".has-error, .error-text"));
    if (errStep > 0) current = errStep;

    function show(i) {
      current = Math.max(0, Math.min(i, steps.length - 1));
      steps.forEach((s, j) => s.classList.toggle("active", j === current));
      markers.forEach((m, j) => { m.classList.toggle("active", j === current); m.classList.toggle("done", j < current); });
      if (steps[current].dataset.step === "review") renderSummary();
      const top = form.getBoundingClientRect().top + window.scrollY - 90;
      if (window.scrollY > top) window.scrollTo({ top, behavior: "smooth" });
    }

    function validStep() {
      const fields = $$("input, select, textarea", steps[current]).filter((f) => !f.disabled && f.offsetParent !== null);
      for (const f of fields) { if (!f.checkValidity()) { f.reportValidity(); return false; } }
      if (steps[current].dataset.step === "date") {
        const picked = $("input[name=slot_id]:checked", form);
        const manual = $("[name=preferred_date]", form);
        if (!picked && manual && !manual.value) { alertMsg("Choose a session, or enter a preferred date."); return false; }
      }
      if (steps[current].dataset.step === "people") {
        const riders = +$("[name=riders]", form).value || 0;
        const people = (+$("[name=adults]", form).value || 0) + (+$("[name=children]", form).value || 0);
        if (riders > people) { alertMsg("Riders can't be more than the adults and children in your group."); return false; }
        const exp = selectedExp();
        if (exp && exp.max && riders > exp.max) { alertMsg(exp.name + " is for up to " + exp.max + " riders. Choose a group package or plan an event."); return false; }
      }
      return true;
    }
    function alertMsg(text) {
      let box = $(".step-alert", steps[current]);
      if (!box) { box = document.createElement("div"); box.className = "flash error step-alert"; box.setAttribute("role", "alert"); steps[current].prepend(box); }
      box.textContent = text;
    }

    if (steps.length) {
      $$("[data-next]", form).forEach((b) => b.addEventListener("click", () => { if (validStep()) show(current + 1); }));
      $$("[data-prev]", form).forEach((b) => b.addEventListener("click", () => show(current - 1)));
      show(current);
    }

    // experience
    const selectedExp = () => {
      const r = $("input[name=experience_id]:checked", form) || $("select[name=experience_id]", form);
      return r ? exps.find((e) => String(e.id) === r.value) : null;
    };

    // slots
    const slotBox = $("[data-slots]", form);
    function loadSlots() {
      if (!slotBox) return;
      const exp = selectedExp();
      const riders = +($("[name=riders]", form) || {}).value || 1;
      const chosen = slotBox.dataset.selected;
      slotBox.innerHTML = '<p class="muted">Loading available sessions…</p>';
      fetch(slotBox.dataset.url + "?days=28" + (exp ? "&experience=" + exp.id : ""))
        .then((r) => r.json())
        .then((slots) => {
          slotHorses = {};
          slots.forEach((s) => { slotHorses[String(s.id)] = s.horses; });
          if (!slots.length) { slotBox.innerHTML = '<p class="muted">No sessions are published for the next few weeks. Enter a preferred date below and we\'ll check for you.</p>'; return; }
          let html = "", lastDay = "";
          slots.forEach((s) => {
            const d = new Date(s.date + "T00:00:00");
            const day = d.toLocaleDateString("en-ZA", { weekday: "long", day: "numeric", month: "long" });
            if (day !== lastDay) { html += '<div class="day">' + day + "</div>"; lastDay = day; }
            const full = s.blocked || s.remaining < riders;
            const label = s.blocked ? "Closed" : (s.remaining < riders ? (s.remaining ? s.remaining + " left — too few" : "Full") : s.remaining + " places left");
            html += '<div class="option"><input type="radio" name="slot_id" id="slot' + s.id + '" value="' + s.id + '"' + (full ? " disabled" : "") + (String(s.id) === chosen ? " checked" : "") +
              '><label for="slot' + s.id + '"><strong>' + s.start + "–" + s.end + '</strong><span class="small muted">' + label + "</span></label></div>";
          });
          slotBox.innerHTML = html;
          const picked = $("input[name=slot_id]:checked", slotBox);
          if (picked) showHorseCounts(slotHorses[picked.value]);
          $$("input[name=slot_id]", slotBox).forEach((r) => r.addEventListener("change", () => {
            slotBox.dataset.selected = r.value;
            showHorseCounts(slotHorses[r.value]);
            const m = $("[name=preferred_date]", form); if (m) m.value = "";
          }));
        })
        .catch(() => { slotBox.innerHTML = '<p class="muted">Couldn\'t load sessions. Enter a preferred date below.</p>'; });
    }
    let slotHorses = {};
    function showHorseCounts(counts) {
      if (!counts) return;
      Object.keys(counts).forEach((k) => {
        const n = counts[k];
        $$('[data-horse-count="' + k + '"]', form).forEach((el) => (el.textContent = n));
        $$('[data-horse-note="' + k + '"]', form).forEach((el) => (el.textContent = n ? n + " free in your session" : "None free in this session"));
        const input = $("#hp-" + k, form);
        if (input) { input.disabled = !n; if (!n && input.checked) $("#hp-none", form).checked = true; }
      });
    }
    // "Suggest another place" text box
    const otherBox = $("[data-location-other]", form);
    const syncOther = () => {
      if (!otherBox) return;
      const on = ($("input[name=location_choice]:checked", form) || {}).value === "__other__";
      otherBox.style.display = on ? "" : "none";
      $("[name=location_other]", form).required = on;
    };
    form.addEventListener("change", (e) => { if (e.target.name === "location_choice") syncOther(); });
    syncOther();
    const manualDate = $("[name=preferred_date]", form);
    if (manualDate) manualDate.addEventListener("input", () => { $$("input[name=slot_id]", form).forEach((r) => (r.checked = false)); });

    // riders
    const rowsBox = $("[data-rider-rows]", form);
    function renderRiders() {
      if (!rowsBox) return;
      const n = Math.min(+($("[name=riders]", form).value) || 0, 30);
      const existing = $$(".rider-row", rowsBox).map((row) => [row.querySelector("select[data-age]").value, row.querySelector("select[data-exp]").value]);
      let html = "";
      for (let i = 0; i < n; i++) {
        const [a, x] = existing[i] || [rowsBox.dataset["age" + i] || "", rowsBox.dataset["exp" + i] || ""];
        html += '<div class="rider-row"><span class="small"><strong>Rider ' + (i + 1) + "</strong></span>" +
          '<select data-age name="rider_age_' + i + '" required aria-label="Rider ' + (i + 1) + ' age group"><option value="">Age group…</option>' +
          ageGroups.map((g) => '<option value="' + g[0] + '"' + (g[0] === a ? " selected" : "") + ">" + g[1] + "</option>").join("") + "</select>" +
          '<select data-exp name="rider_exp_' + i + '" required aria-label="Rider ' + (i + 1) + ' experience"><option value="">Experience…</option>' +
          levels.map((l) => '<option value="' + l[0] + '"' + (l[0] === x ? " selected" : "") + ">" + l[1] + "</option>").join("") + "</select></div>";
      }
      rowsBox.innerHTML = html || '<p class="muted small">No riders — non-riding guests only.</p>';
    }

    // estimate
    const estBox = $("[data-estimate]", form);
    function estimate() {
      if (!estBox) return null;
      const exp = selectedExp();
      const riders = +($("[name=riders]", form) || {}).value || 0;
      if (!exp || exp.price === null || exp.basis === "quote") { estBox.innerHTML = "<strong>Price confirmed on approval.</strong> This experience is quoted individually — you won't be charged until you accept our final price."; return null; }
      let total = exp.basis === "per_person" ? exp.price * riders : exp.price;
      for (const box of $$("input[name=addons]:checked", form)) {
        const a = addons.find((x) => String(x.id) === box.value);
        if (!a || a.price === null) { estBox.innerHTML = "<strong>Estimate: from " + money(total) + "</strong> plus add-ons priced on request. Final price confirmed on approval."; return total; }
        total += a.basis === "per_person" ? a.price * riders : a.price;
      }
      estBox.innerHTML = "<strong>Indicative estimate: " + money(total) + "</strong><br><span class='small'>Not a charge. Our team confirms the final price after checking availability.</span>";
      return total;
    }

    function renderSummary() {
      const box = $("[data-summary]", form);
      if (!box) return;
      const exp = selectedExp();
      const slot = $("input[name=slot_id]:checked", form);
      const when = slot ? slot.closest(".option").previousElementSibling && findDay(slot) + " " + slot.nextElementSibling.querySelector("strong").textContent
        : (($("[name=preferred_date]", form) || {}).value || "—") + " " + (($("[name=preferred_time]", form) || {}).value || "");
      const extras = $$("input[name=addons]:checked", form).map((b) => b.dataset.name).join(", ") || "None";
      const v = (n) => (($("[name=" + n + "]", form) || {}).value || "").trim();
      const rows = [["Experience", exp ? exp.name : v("event_type") || "—"], ["When", when], ["Adults / children", v("adults") + " / " + v("children")],
        ["Riders", v("riders")], ["Non-riding guests", v("spectators") || "0"], ["Where", placeText()], ["Horses", horseText()], ["Extras", extras], ["Name", v("contact_name")], ["Email", v("contact_email")], ["Mobile", v("contact_phone")]];
      box.innerHTML = "<dl>" + rows.map((r) => "<dt>" + r[0] + "</dt><dd>" + escapeHtml(r[1]) + "</dd>").join("") + "</dl>";
      estimate();
    }
    function placeText() {
      const r = $("input[name=location_choice]:checked", form);
      if (!r) return "—";
      return r.value === "__other__" ? "Your suggestion: " + (($("[name=location_other]", form) || {}).value || "") : r.value;
    }
    function horseText() {
      const r = $("input[name=horse_preference]:checked", form);
      return r && r.value ? r.nextElementSibling.querySelector("strong").textContent : "No preference";
    }
    function findDay(slot) {
      let el = slot.closest(".option").previousElementSibling;
      while (el && !el.classList.contains("day")) el = el.previousElementSibling;
      return el ? el.textContent : "";
    }
    function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

    form.addEventListener("change", (e) => {
      if (e.target.name === "experience_id") { loadSlots(); estimate(); }
      if (e.target.name === "riders") { renderRiders(); loadSlots(); }
      if (e.target.name === "addons" || e.target.name === "riders") estimate();
    });
    form.addEventListener("input", (e) => { if (e.target.name === "riders") { renderRiders(); estimate(); } });
    renderRiders(); loadSlots(); estimate();
  }

  // ---- payment return: poll until the gateway notification is verified server-side
  const poll = $("[data-poll-status]");
  if (poll) {
    let tries = 0;
    const tick = () => {
      fetch(poll.dataset.pollStatus).then((r) => r.json()).then((d) => {
        if (d.status && d.status.indexOf("Confirmed") === 0 || d.paid_cents > +poll.dataset.paidBefore) {
          window.location.href = poll.dataset.done;
        } else if (++tries < 40) setTimeout(tick, 3000);
        else poll.textContent = "Still waiting for the payment provider. You'll get an email as soon as it's verified.";
      }).catch(() => setTimeout(tick, 5000));
    };
    setTimeout(tick, 1500);
  }

  // ---- auto-submit (PayFast checkout handoff)
  const auto = $("form[data-autosubmit]");
  if (auto) setTimeout(() => auto.submit(), 600);

  // ---- admin approval: deposit helper
  const approveForm = $("form[data-approve]");
  if (approveForm) {
    const price = $("[name=final_price]", approveForm), dep = $("[name=deposit]", approveForm);
    const depRow = $("[data-deposit-row]", approveForm);
    const pct = +approveForm.dataset.depositPercent || 50;
    const sync = () => {
      const mode = ($("[name=payment_mode]:checked", approveForm) || {}).value;
      depRow.style.display = mode === "deposit" ? "" : "none";
      dep.required = mode === "deposit";
      if (mode === "deposit" && !dep.value && price.value) dep.value = (parseFloat(price.value) * pct / 100).toFixed(2);
    };
    approveForm.addEventListener("change", sync); sync();
  }

  // ---- "Meet our horses" type filter (also accepts #meet-our-horses links with ?type= via data-filter-link)
  const chips = $$(".horse-filters .chip");
  if (chips.length) {
    const apply = (type) => {
      chips.forEach((c) => c.classList.toggle("on", c.dataset.filter === type));
      $$(".horse-card").forEach((card) => { card.style.display = type === "all" || card.dataset.type === type ? "" : "none"; card.classList.add("in"); });
      try { sessionStorage.setItem("horseFilter", type); } catch (e) { /* storage unavailable */ }
      document.dispatchEvent(new Event("horsefilter"));
    };
    chips.forEach((c) => c.addEventListener("click", () => apply(c.dataset.filter)));
    let start = "all";
    try { start = sessionStorage.getItem("horseFilter") || "all"; sessionStorage.removeItem("horseFilter"); } catch (e) { /* ignore */ }
    if (chips.some((c) => c.dataset.filter === start)) apply(start);
  }
  $$("[data-filter-link]").forEach((a) => a.addEventListener("click", () => {
    try { sessionStorage.setItem("horseFilter", a.dataset.filterLink); } catch (e) { /* ignore */ }
  }));

  // ---- horse slider arrows
  $$("[data-slider]").forEach((slider) => {
    const track = $("[data-track]", slider);
    const btns = $$("[data-slide]", slider);
    const step = () => { const card = $(".horse-card:not([style*='none'])", track); return card ? (card.offsetWidth + 18) * 2 : 300; };
    const sync = () => {
      btns[0].disabled = track.scrollLeft < 8;
      btns[1].disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 8;
    };
    btns.forEach((b) => b.addEventListener("click", () => track.scrollBy({ left: step() * +b.dataset.slide })));
    track.addEventListener("scroll", sync, { passive: true });
    window.addEventListener("resize", sync);
    document.addEventListener("horsefilter", () => { track.scrollLeft = 0; setTimeout(sync, 50); });
    sync();
  });

  // ---- reveal-on-scroll
  const reveals = $$(".reveal");
  if ("IntersectionObserver" in window && reveals.length) {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: 0.12 });
    reveals.forEach((el) => io.observe(el));
  } else reveals.forEach((el) => el.classList.add("in"));

  // ---- share (business card)
  $$("[data-share]").forEach((b) => b.addEventListener("click", async () => {
    const data = { title: b.dataset.title, url: b.dataset.url };
    try {
      if (navigator.share) { await navigator.share(data); return; }
      await navigator.clipboard.writeText(data.url);
      b.textContent = "Link copied!";
      setTimeout(() => (b.textContent = "Share this card"), 2000);
    } catch (e) { /* user cancelled or clipboard blocked */ }
  }));

  // ---- print buttons
  $$("[data-print]").forEach((b) => b.addEventListener("click", () => window.print()));
})();
