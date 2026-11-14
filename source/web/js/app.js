(function () {
  "use strict";

  var state = { meta: null, scenario: {}, suggestions: [], touched: {}, edited: {}, validation: null, result: null,
    llm: true, currency: "USD", samples: [], busy: false };
  var main = document.getElementById("main");
  var live = document.getElementById("live");

  // ---- helpers -------------------------------------------------------------
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else el.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) c.forEach(function (x) { add(el, x); });
    else el.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
  }
  function say(msg) { live.textContent = ""; setTimeout(function () { live.textContent = msg; }, 30); }
  function toast(msg) {
    var t = h("div", { class: "toast", role: "status", text: msg });
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 4200);
  }
  function api(path, body, isForm) {
    var opts = { method: body === undefined ? "GET" : "POST" };
    if (body !== undefined) {
      if (isForm) opts.body = body;
      else { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    }
    return fetch(path, opts).then(function (r) {
      if (!r.ok) return r.json().catch(function () { return {}; }).then(function (j) {
        throw new Error(j.detail || ("Request failed (" + r.status + ")"));
      });
      var ct = r.headers.get("content-type") || "";
      return ct.indexOf("json") >= 0 ? r.json() : r.blob();
    });
  }
  function store(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage unavailable */ } }
  function load(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } }

  var NAMES = { pci_dss: "PCI DSS", rbi_localisation: "RBI localisation", dpdp: "DPDP", gdpr: "GDPR", hipaa: "HIPAA", soc2: "SOC 2",
    iso27001: "ISO 27001", csharp: "C#", cpp: "C++", api_only: "API only", llm_rag: "LLM / RAG", phone_otp: "Phone OTP",
    email_password: "Email and password", enterprise_sso: "Enterprise SSO", ios: "iOS", aws: "AWS", gcp: "GCP", azure: "Azure",
    none: "None", any: "Any", saas_b2b: "SaaS (B2B)", ai_ml_product: "AI / ML product", p95: "p95", microsoft_365: "Microsoft 365",
    aws_credits: "AWS credits", gcp_credits: "GCP credits", azure_credits: "Azure credits", iot: "IoT", javascript: "JavaScript",
    typescript: "TypeScript", sql: "SQL", php: "PHP", go: "Go", inference_api: "Inference API", on_premise: "On premise",
    self_hosted_cloud_vm: "Self-hosted cloud VM", managed_cloud: "Managed cloud", air_gapped: "Air-gapped", android: "Android",
    web: "Web", desktop: "Desktop", java: "Java", python: "Python", kotlin: "Kotlin", swift: "Swift", ruby: "Ruby", rust: "Rust", dart: "Dart" };
  function nice(v) {
    v = String(v);
    if (NAMES[v]) return NAMES[v];
    var s = v.replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  }
  function getPath(o, p) { return p.split(".").reduce(function (a, k) { return a && typeof a === "object" ? a[k] : undefined; }, o); }
  function setPath(o, p, v) {
    var parts = p.split("."), cur = o;
    for (var i = 0; i < parts.length - 1; i++) { if (typeof cur[parts[i]] !== "object" || cur[parts[i]] === null) cur[parts[i]] = {}; cur = cur[parts[i]]; }
    if (v === undefined) delete cur[parts[parts.length - 1]]; else cur[parts[parts.length - 1]] = v;
  }
  function fmt(n) {
    if (typeof n !== "number") return String(n);
    return n.toLocaleString("en-US", { maximumFractionDigits: 3 });
  }
  function usd(n) { return "$" + Math.round(n).toLocaleString("en-US"); }

  // ---- form definition -----------------------------------------------------
  var GROUPS = [
    { id: "project", title: "Project", fields: [
      { p: "project.name", l: "Name", t: "text" },
      { p: "project.type", l: "Project type", t: "enum", req: 1 },
      { p: "project.platforms", l: "Platforms", t: "multi", req: 1, wide: 1 },
      { p: "project.description", l: "Description", t: "textarea", wide: 1 }] },
    { id: "scale", title: "Scale", fields: [
      { p: "scale.users_at_launch", l: "Users at launch", t: "int", req: 1 },
      { p: "scale.users_12_months", l: "Users after 12 months", t: "int", req: 1 },
      { p: "scale.peak_concurrent_users", l: "Peak concurrent users", t: "int", help: "Leave empty if unknown." },
      { p: "scale.data_volume_gb_12_months", l: "Data after 12 months (GB)", t: "num" },
      { p: "scale.traffic_pattern", l: "Traffic pattern", t: "enum" },
      { p: "scale.workload", l: "Workload", t: "enum" }] },
    { id: "team", title: "Team", fields: [
      { p: "team.size", l: "Engineers", t: "int", req: 1 },
      { p: "team.experience", l: "Experience", t: "enum", req: 1 },
      { p: "team.languages", l: "Languages the team knows", t: "tags", wide: 1, req: 1, list: "languages", help: "Press Enter or comma to add a language." },
      { p: "team.has_devops", l: "Someone can operate infrastructure", t: "bool", wide: 1 }] },
    { id: "budget", title: "Budget and timeline", fields: [
      { p: "budget.monthly_infra_usd", l: "Monthly infrastructure budget (USD)", t: "num", req: 1, help: "Include third-party services. Zero means free tiers only." },
      { p: "timeline.mvp_weeks", l: "Weeks to first release", t: "int", req: 1 }] },
    { id: "infra", title: "Infrastructure", fields: [
      { p: "infrastructure.cloud_preference", l: "Cloud preference", t: "enum" },
      { p: "infrastructure.deployment_model", l: "Deployment model", t: "enum" },
      { p: "infrastructure.data_residency", l: "Data residency", t: "enum" },
      { p: "infrastructure.existing_systems", l: "Existing systems to reuse", t: "tags", wide: 1, help: "For example SQL Server or Active Directory." }] },
    { id: "req", title: "Requirements", fields: [
      { p: "requirements.realtime", l: "Live updates, chat or collaboration", t: "bool" },
      { p: "requirements.offline_support", l: "Works without connectivity", t: "bool" },
      { p: "requirements.full_text_search", l: "Full-text search", t: "bool" },
      { p: "requirements.file_media_storage", l: "User file or media uploads", t: "bool" },
      { p: "requirements.payments", l: "Takes payments", t: "bool" },
      { p: "requirements.multi_tenant", l: "Multiple customer organisations", t: "bool" },
      { p: "requirements.seo_critical", l: "Public pages must rank in search", t: "bool" },
      { p: "requirements.auth", l: "Sign-in method", t: "enum" },
      { p: "requirements.ml_features", l: "Machine learning", t: "enum" },
      { p: "requirements.availability", l: "Availability target (%)", t: "enum" },
      { p: "requirements.p95_latency_ms", l: "p95 latency target (ms)", t: "int" }] },
    { id: "comp", title: "Compliance", fields: [
      { p: "compliance", l: "Standards and regulations", t: "multi", wide: 1 }] },
    { id: "prio", title: "Priorities", desc: "1 = minor, 5 = critical. Default: 3.", fields: [
      { p: "priorities.cost", l: "Cost", t: "range" }, { p: "priorities.time_to_market", l: "Time to market", t: "range" },
      { p: "priorities.scalability", l: "Scalability", t: "range" }, { p: "priorities.performance", l: "Performance", t: "range" },
      { p: "priorities.maintainability", l: "Maintainability", t: "range" }, { p: "priorities.security", l: "Security", t: "range" }] }
  ];
  var EXTRA = { id: "extra", title: "Extra inputs", fields: [
    { p: "extra.learning_appetite", l: "Learning appetite", t: "enum", help: "High allows unfamiliar tools; low favours known languages." },
    { p: "extra.lock_in_tolerance", l: "Vendor lock-in tolerance", t: "enum" },
    { p: "extra.user_geography", l: "User geography", t: "enum" },
    { p: "extra.data_sensitivity", l: "Data sensitivity", t: "enum" },
    { p: "extra.budget_strictness", l: "Budget strictness", t: "enum", help: "Hard excludes stacks above the budget." },
    { p: "extra.hiring_plan", l: "Hiring plan", t: "enum" },
    { p: "extra.mobile_preference", l: "Mobile preference", t: "enum" },
    { p: "extra.existing_licenses", l: "Existing licences and credits", t: "multi", wide: 1 },
    { p: "extra.expected_rps", l: "Expected requests per second", t: "num" },
    { p: "extra.cloud_credits_usd", l: "Cloud credits (USD, total)", t: "num", help: "Select the credit provider above. Enter the total credit, not a monthly amount." }] };
  var REQUIRED = {};
  GROUPS.concat([EXTRA]).forEach(function (g) { g.fields.forEach(function (f) { if (f.req) REQUIRED[f.p] = 1; }); });
  var LIST_HINTS = { languages: ["python", "javascript", "typescript", "java", "csharp", "php", "go", "kotlin", "swift", "ruby", "rust", "dart", "sql", "cpp"] };

  // ---- suggestions ---------------------------------------------------------
  function sugFor(path) { return state.suggestions.filter(function (s) { return s.path === path; }); }
  function sameVal(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function pct(c) { return Math.round(c * 100) + "%"; }
  function level(c) { return c >= 0.75 ? "high" : c >= 0.55 ? "medium" : "low"; }

  function evidenceBlock(path) {
    var list = sugFor(path);
    if (!list.length) return null;
    var best = Math.max.apply(null, list.map(function (s) { return s.confidence; }));
    return h("details", { class: "ev" },
      h("summary", { text: "Found in your text, " + level(best) + " confidence (" + pct(best) + ")" }),
      h("ul", null, list.map(function (s) {
        return h("li", null, h("b", { text: nice(Array.isArray(s.value) ? s.value.join(", ") : String(s.value)) }),
          " " + pct(s.confidence) + (s.source === "gemini" ? ", model suggestion" : ""), h("br"),
          s.evidence ? h("q", { text: s.evidence }) : null);
      })));
  }
  function alsoFound(f, apply) {
    if (f.t !== "enum") return null;
    var cur = getPath(state.scenario, f.p);
    var others = sugFor(f.p).filter(function (s) { return !sameVal(s.value, cur) && !Array.isArray(s.value); });
    if (!others.length) return null;
    return h("div", { class: "also" }, "Also found:", others.map(function (s) {
      return h("button", { type: "button", title: s.evidence || "", onclick: function () { apply(s.value); } }, nice(s.value));
    }));
  }
  function badge(f) {
    if (state.edited[f.p]) return h("span", { class: "badge edited", text: "edited" });
    if (sugFor(f.p).length && getPath(state.scenario, f.p) !== undefined) return h("span", { class: "badge", text: "suggested" });
    return null;
  }

  // ---- controls ------------------------------------------------------------
  function changed(path, value, rerender) {
    setPath(state.scenario, path, value);
    state.edited[path] = true;
    store("scenario", state.scenario);
    scheduleValidate();
    if (rerender) renderReview(true);
  }
  function optionsFor(path) { return (state.meta && state.meta.enums && state.meta.enums[path]) || []; }

  function control(f) {
    var id = "f-" + f.p.replace(/\./g, "-");
    var val = getPath(state.scenario, f.p);
    var el, lab = true;
    if (f.t === "text" || f.t === "int" || f.t === "num") {
      el = h("input", { id: id, type: f.t === "text" ? "text" : "number", inputmode: f.t === "int" ? "numeric" : f.t === "num" ? "decimal" : null,
        min: f.t === "text" ? null : "0", step: f.t === "int" ? "1" : f.t === "num" ? "any" : null,
        value: val === undefined ? "" : val, "data-path": f.p, autocomplete: "off",
        oninput: function (e) {
          var v = e.target.value;
          if (v === "") changed(f.p, undefined);
          else changed(f.p, f.t === "text" ? v : (isNaN(Number(v)) ? v : Number(v)));
          refreshMarks(f.p);
        } });
    } else if (f.t === "textarea") {
      el = h("textarea", { id: id, "data-path": f.p, oninput: function (e) { changed(f.p, e.target.value || undefined); } });
      el.value = val || "";
    } else if (f.t === "enum") {
      var opts = optionsFor(f.p);
      el = h("select", { id: id, "data-path": f.p, onchange: function (e) { changed(f.p, e.target.value || undefined, true); } },
        h("option", { value: "", text: f.req ? "Choose" : "Not set (default)" }),
        opts.map(function (o) { return h("option", { value: o, text: nice(o) + (f.p === "requirements.availability" ? "" : "") }); }));
      el.value = val === undefined ? "" : String(val);
    } else if (f.t === "bool") {
      lab = false;
      el = h("label", { class: "switch" }, h("input", { id: id, type: "checkbox", "data-path": f.p,
        onchange: function (e) { changed(f.p, e.target.checked, true); } }), h("span", { text: f.l }), badge(f));
      el.querySelector("input").checked = val === true;
    } else if (f.t === "multi") {
      lab = false;
      var cur = Array.isArray(val) ? val : [];
      el = h("fieldset", { class: "tick", style: "border:0;padding:0;margin:0", "data-path": f.p },
        h("legend", { class: "sr", text: f.l }),
        optionsFor(f.p).map(function (o) {
          var cb = h("input", { type: "checkbox", value: o, onchange: function () {
            var set = (getPath(state.scenario, f.p) || []).slice();
            var i = set.indexOf(o);
            if (cb.checked && i < 0) set.push(o); else if (!cb.checked && i >= 0) set.splice(i, 1);
            changed(f.p, set.length || f.p === "project.platforms" ? set : undefined, true);
          } });
          cb.checked = cur.indexOf(o) >= 0;
          return h("label", null, cb, nice(o));
        }));
    } else if (f.t === "tags") {
      el = tagsControl(f, id, val);
    } else if (f.t === "range") {
      var set = val !== undefined;
      var out = h("output", { for: id, text: set ? String(val) : "3" }, set ? null : h("small", { text: "default" }));
      var r = h("input", { id: id, type: "range", min: "1", max: "5", step: "1", "data-path": f.p, "aria-valuetext": (set ? val : 3) + " of 5",
        value: set ? val : 3,
        oninput: function (e) { var v = Number(e.target.value); out.textContent = String(v); r.setAttribute("aria-valuetext", v + " of 5"); changed(f.p, v); } });
      el = h("div", { class: "range" }, r, out);
    }
    var wrap = h("div", { class: "field" + (f.wide ? " wide" : "") + (f.t === "textarea" ? " wide" : ""), "data-field": f.p });
    if (lab) wrap.appendChild(h("label", { for: f.t === "multi" ? null : id }, f.l, f.req ? h("span", { class: "req", text: "required" }) : null, badge(f)));
    else if (f.t === "multi") wrap.appendChild(h("div", { class: "lab" }, f.l, f.req ? h("span", { class: "req", text: "required" }) : null, badge(f)));
    wrap.appendChild(el);
    if (f.help) wrap.appendChild(h("p", { class: "help", text: f.help }));
    wrap.appendChild(h("div", { class: "marks", "data-marks": f.p }));
    fillMarks(wrap.querySelector(".marks"), f);
    return wrap;
  }

  function fillMarks(box, f) {
    box.textContent = "";
    var errs = errorsFor(f.p);
    errs.forEach(function (e) { box.appendChild(h("p", { class: "err", id: "err-" + f.p.replace(/\./g, "-"), text: errorMessage(e) })); });
    var inp = box.parentNode.querySelector("input:not([type=checkbox]):not([type=range]),select");
    if (inp) { if (errs.length) inp.setAttribute("aria-invalid", "true"); else inp.removeAttribute("aria-invalid"); }
    var ev = evidenceBlock(f.p);
    if (ev) box.appendChild(ev);
    var al = alsoFound(f, function (v) { changed(f.p, v, true); });
    if (al) box.appendChild(al);
  }
  function refreshMarks(path) {
    var box = main.querySelector('[data-marks="' + path + '"]');
    if (!box) return;
    var f = findField(path);
    if (f) fillMarks(box, f);
  }
  function findField(path) {
    var found = null;
    GROUPS.concat([EXTRA]).forEach(function (g) { g.fields.forEach(function (f) { if (f.p === path) found = f; }); });
    return found;
  }
  function errorMessage(e) {
    var f = findField(String(e.field).replace(/\[\d+\]/g, ""));
    if (/^required field is missing/.test(e.message)) return "Required field is missing.";
    if (f && (f.t === "enum" || f.t === "multi") && /allowed values:|one of:|must be one of/.test(e.message)) {
      return f.t === "multi" ? "Select from the available options." : "Choose an available option.";
    }
    return e.message;
  }
  function checkLabel(path) {
    var field = findField(String(path).replace(/\[\d+\]/g, ""));
    if (!field) return nice(path);
    var section = GROUPS.concat([EXTRA]).find(function (g) { return g.fields.indexOf(field) >= 0; });
    return section.title + " · " + field.l;
  }
  function checkTarget(path) {
    path = String(path).replace(/\[\d+\]/g, "");
    var field = findField(path);
    if (field) return main.querySelector('[data-field="' + field.p + '"]');
    var group = GROUPS.concat([EXTRA]).find(function (g) {
      return g.fields.some(function (f) { return f.p.indexOf(path + ".") === 0; });
    });
    return group ? document.getElementById("g-" + group.id) : null;
  }
  function focusCheck(path) {
    var target = checkTarget(path);
    if (!target) return;
    var details = target.closest("details"); if (details) details.open = true;
    target.scrollIntoView({ behavior: "auto", block: "center" });
    var input = target.querySelector("input,select,textarea");
    if (input) input.focus({ preventScroll: true });
    else { target.setAttribute("tabindex", "-1"); target.focus({ preventScroll: true }); }
  }
  function errorsFor(path) {
    var v = state.validation;
    if (!v || !v.validation_errors) return [];
    return v.validation_errors.filter(function (e) { return String(e.field).replace(/\[\d+\]/g, "") === path; });
  }

  function tagsControl(f, id, val) {
    var list = Array.isArray(val) ? val.slice() : [];
    var box = h("div", { class: "tags", "data-path": f.p });
    var input = h("input", { id: id, type: "text", autocomplete: "off", list: f.list ? id + "-list" : null, "aria-label": f.l });
    function commit(t) {
      t = t.trim().replace(/,$/, "").trim();
      if (!t) return;
      if (f.list) t = t.toLowerCase();
      if (list.indexOf(t) < 0) list.push(t);
      input.value = "";
      changed(f.p, list.slice());
      draw();
    }
    function draw() {
      Array.prototype.slice.call(box.querySelectorAll(".tag")).forEach(function (n) { n.remove(); });
      list.forEach(function (t, i) {
        box.insertBefore(h("span", { class: "tag" }, t, h("button", { type: "button", "aria-label": "Remove " + t, text: "x", onclick: function () {
          list.splice(i, 1); changed(f.p, list.length || f.p === "team.languages" ? list.slice() : undefined); draw(); refreshMarks(f.p); input.focus();
        } })), input);
      });
    }
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(input.value); refreshMarks(f.p); }
      else if (e.key === "Backspace" && !input.value && list.length) { list.pop(); changed(f.p, list.slice()); draw(); }
    });
    input.addEventListener("blur", function () { commit(input.value); refreshMarks(f.p); });
    box.appendChild(input);
    draw();
    var frag = h("div", null, box);
    if (f.list) frag.appendChild(h("datalist", { id: id + "-list" }, LIST_HINTS[f.list].map(function (o) { return h("option", { value: o }); })));
    return frag;
  }

  // ---- validation ----------------------------------------------------------
  var vTimer = null, vSeq = 0;
  function scheduleValidate() { clearTimeout(vTimer); vTimer = setTimeout(runValidate, 450); }
  function runValidate() {
    var seq = ++vSeq;
    return api("/api/validate", state.scenario).then(function (v) {
      if (seq !== vSeq) return v;
      state.validation = v;
      if (currentRoute() === "review") { paintChecks(); GROUPS.concat([EXTRA]).forEach(function (g) { g.fields.forEach(function (f) { refreshMarks(f.p); }); }); }
      return v;
    }).catch(function (e) {
      state.validation = { validation_errors: [], warnings: [], error: e.message };
      if (currentRoute() === "review") paintChecks();
      return null;
    });
  }

  function sevBlock(w) {
    return h("div", { class: "sev " + (w.severity || "info") }, h("b", { text: w.severity || "info" }), w.message || "",
      w.fields && w.fields.length ? h("div", { class: "f", text: w.fields.join(", ") }) : null);
  }
  function paintChecks() {
    var box = document.getElementById("checks-body");
    if (!box) return;
    box.textContent = "";
    var v = state.validation;
    if (!v) { box.appendChild(h("p", { class: "note", text: "Checks run as you edit." })); return; }
    if (v.error) box.appendChild(h("p", { class: "err", text: v.error }));
    var errs = v.validation_errors || [];
    var evalBtn = document.getElementById("evaluate");
    if (evalBtn) evalBtn.disabled = state.busy;
    if (errs.length) {
      var fieldErrors = errs.filter(function (e) {
        return !/^section '[^']+' is missing;/.test(e.message) || !errs.some(function (other) { return other.field.indexOf(e.field + ".") === 0; });
      });
      box.appendChild(h("p", { class: "note", text: fieldErrors.length + (fieldErrors.length === 1 ? " item needs" : " items need") + " attention." }));
      box.appendChild(h("ul", { class: "check-list" }, fieldErrors.map(function (e) {
        var message = errorMessage(e);
        return h("li", null,
          h(checkTarget(e.field) ? "a" : "div", checkTarget(e.field) ? { href: "#/review", onclick: function (event) { event.preventDefault(); focusCheck(e.field); } } : null,
            h("span", { class: "check-field", text: checkLabel(e.field) }), h("span", { class: "check-message", text: message })),
          message !== e.message && !/^required field is missing/.test(e.message) ?
            h("details", null, h("summary", { text: "Details" }), e.message) : null);
      })));
    } else if (!v.error) box.appendChild(h("p", { class: "note", text: "Ready to evaluate." }));
    var ws = v.warnings || [];
    if (ws.length) box.appendChild(h("h3", { text: "Conflicts", style: "margin-top:18px" }));
    ws.forEach(function (w) { box.appendChild(sevBlock(w)); });
    var ass = v.assumptions || [];
    if (ass.length) box.appendChild(h("details", { class: "ev" }, h("summary", { text: "Defaults applied (" + ass.length + ")" }),
      h("ul", null, ass.map(function (a) { return h("li", { text: typeof a === "string" ? a : JSON.stringify(a) }); }))));
  }

  // ---- views ---------------------------------------------------------------
  function currentRoute() { return (location.hash.replace(/^#\/?/, "") || "start").split("?")[0]; }

  function renderStart() {
    var fileIn = h("input", { type: "file", accept: ".pdf,.docx,.txt", class: "sr", id: "file-up", tabindex: "-1" });
    var jsonIn = h("input", { type: "file", accept: ".json,application/json", class: "sr", id: "file-json", tabindex: "-1" });
    var describe = h("div", { class: "panel describe", id: "describe", hidden: true },
      h("label", { for: "desc-text", class: "lab", text: "Describe the project in plain words" }),
      h("textarea", { id: "desc-text", placeholder: "Customers book electricians in two cities. About 30k users in a year, peak 800 at once. Team of 3 who know python and typescript, no devops. Budget $150 a month, MVP in 8 weeks. Data must stay in India." }),
      h("div", { class: "row" },
        h("button", { class: "btn-primary", type: "button", id: "analyse", onclick: function () {
          var t = document.getElementById("desc-text").value.trim();
          if (!t) { toast("Write a few sentences first."); return; }
          runExtract({ text: t });
        } }, "Find requirements"),
        h("span", { class: "note", text: "Review the extracted values before evaluating." })));
    function method(n, title, desc, fn) {
      return h("button", { type: "button", class: "method", onclick: fn }, h("span", { class: "num", text: n }), h("span", { class: "t", text: title }), desc ? h("span", { class: "d", text: desc }) : null);
    }
    var methods = h("div", { class: "methods" },
      method("i.", "Upload a brief", "PDF, Word (.docx) or text (.txt)", function () { describe.hidden = true; fileIn.click(); }),
      method("ii.", "Describe it", "", function () { describe.hidden = !describe.hidden; if (!describe.hidden) document.getElementById("desc-text").focus(); }),
      method("iii.", "Enter manually", "", function () { newScenario(); location.hash = "#/review"; }),
      method("iv.", "Import JSON", "", function () { describe.hidden = true; jsonIn.click(); }));
    fileIn.addEventListener("change", function () { if (fileIn.files[0]) uploadFile(fileIn.files[0]); fileIn.value = ""; });
    function importJson(f) {
      describe.hidden = true;
      if (!/\.json$/i.test(f.name)) { toast("Choose a JSON (.json) file."); return; }
      f.text().then(function (txt) {
        var obj;
        try { obj = JSON.parse(txt); } catch (e) { toast("That file is not valid JSON."); return; }
        if (!obj || typeof obj !== "object" || Array.isArray(obj)) { toast("The JSON must be an object."); return; }
        useScenario(obj, []); location.hash = "#/review";
      }).catch(function () { toast("That file could not be read."); });
    }
    jsonIn.addEventListener("change", function () { if (jsonIn.files[0]) importJson(jsonIn.files[0]); jsonIn.value = ""; });
    function uploadDrop(button, read) {
      ["dragover", "dragenter"].forEach(function (ev) { button.addEventListener(ev, function (e) { e.preventDefault(); button.classList.add("drag"); }); });
      ["dragleave", "drop"].forEach(function (ev) { button.addEventListener(ev, function () { button.classList.remove("drag"); }); });
      button.addEventListener("drop", function (e) { e.preventDefault(); describe.hidden = true; if (e.dataTransfer.files[0]) read(e.dataTransfer.files[0]); });
    }
    uploadDrop(methods.children[0], uploadFile);
    uploadDrop(methods.children[3], importJson);

    var samples = h("section", { class: "samples", "aria-labelledby": "samples-h" },
      h("p", { class: "eyebrow", id: "samples-h", text: "Or start from a sample" }),
      h("div", { class: "chips" }, state.samples.length ? state.samples.map(function (s) {
        return h("button", { type: "button", class: "chip", onclick: function () { useScenario(JSON.parse(JSON.stringify(s.scenario)), []); location.hash = "#/review"; } },
          s.id, h("small", { text: s.name }));
      }) : h("span", { class: "note", text: "No sample scenarios found." })));

    main.textContent = "";
    main.appendChild(h("div", { class: "hero" },
      h("p", { class: "eyebrow", text: "Technology stack recommendations" }),
      h("h1", { id: "page-h" }, "Begin with a ", h("em", { text: "project." }))));
    main.appendChild(methods); main.appendChild(describe); main.appendChild(samples); main.appendChild(fileIn); main.appendChild(jsonIn);
  }

  function newScenario() { state.scenario = {}; state.suggestions = []; state.edited = {}; state.validation = null; state.result = null; store("scenario", state.scenario); }
  function useScenario(obj, suggestions) {
    state.scenario = obj; state.suggestions = suggestions || []; state.edited = {}; state.validation = null; state.result = null;
    store("scenario", obj); store("suggestions", state.suggestions);
  }

  function uploadFile(file) {
    if (!/\.(pdf|docx|txt)$/i.test(file.name)) { toast("Choose a PDF, Word (.docx) or text (.txt) file."); return; }
    var fd = new FormData(); fd.append("file", file);
    if (!state.llm) fd.append("llm", "false");
    busy("Reading " + file.name + "...");
    api("/api/extract", fd, true).then(handleExtract, function (e) { busy(null); toast(e.message); });
  }
  function runExtract(body) {
    body.llm = state.llm ? undefined : false;
    busy("Reading the description...");
    api("/api/extract", body).then(function (r) { r.__text = body.text; handleExtract(r); }, function (e) { busy(null); toast(e.message); });
  }
  function handleExtract(r) {
    busy(null);
    if (r.scenario) { useScenario(r.scenario, []); toast("Found a scenario in the file."); location.hash = "#/review"; return; }
    var fields = r.fields || {};
    if (r.__text || r.text) { fields.project = fields.project || {}; fields.project.description = (r.__text || r.text || "").slice(0, 3000); }
    useScenario(fields, r.suggestions || []);
    state.extractNote = (r.suggestions || []).length + " suggestions found" + (r.used_llm ? ", including model suggestions" : "") + ". Check each one.";
    location.hash = "#/review";
  }
  function busy(msg) {
    state.busy = !!msg;
    var b = document.getElementById("analyse"); if (b) b.disabled = !!msg;
    if (msg) say(msg);
  }

  function renderReview(keepScroll) {
    var y = window.scrollY;
    var sections = GROUPS.map(function (g, i) {
      return h("section", { class: "group", "aria-labelledby": "g-" + g.id },
        h("header", null, h("span", { class: "gnum", text: String(i + 1).padStart(2, "0") }), h("h2", { id: "g-" + g.id, text: g.title })),
        g.desc ? h("p", { class: "gdesc", text: g.desc }) : null,
        h("div", { class: "fields" }, g.fields.map(control)));
    });
    var extra = h("details", { class: "more", open: Object.keys(state.scenario.extra || {}).length ? true : null },
      h("summary", { id: "g-extra" }, EXTRA.title, h("span", { class: "note", text: "optional" })),
      h("div", { class: "fields" }, EXTRA.fields.map(control)));
    var checks = h("aside", { class: "checks", "aria-label": "Checks" },
      h("div", { class: "panel" }, h("h3", { text: "Checks" }), h("div", { id: "checks-body", style: "margin-top:12px" })),
      h("div", { class: "row" },
        h("button", { class: "btn-primary", id: "evaluate", type: "button", onclick: evaluate }, "Evaluate"),
        h("button", { type: "button", onclick: function () { if (confirm("Clear the form?")) { newScenario(); renderReview(); } } }, "Clear")));
    main.textContent = "";
    main.appendChild(h("p", { class: "eyebrow", text: "Step two" }));
    main.appendChild(h("h1", { id: "page-h" }, "Review the ", h("em", { text: "details." })));
    if (state.extractNote) main.appendChild(h("p", { class: "note", text: state.extractNote }));
    main.appendChild(h("div", { class: "review" }, h("div", null, sections, extra), checks));
    paintChecks();
    if (keepScroll) window.scrollTo(0, y);
    runValidate();
  }

  function evaluate() {
    var btn = document.getElementById("evaluate");
    btn.disabled = true; btn.textContent = "Checking...";
    runValidate().then(function (v) {
      if (!v || (v.validation_errors || []).length) {
        btn.disabled = false; btn.textContent = "Evaluate";
        say("Fix the highlighted fields first.");
        var first = main.querySelector('[aria-invalid="true"]');
        if (first) first.focus();
        return;
      }
      btn.textContent = "Evaluating...";
      var body = { scenario: state.scenario, options: state.llm ? {} : { llm: false } };
      return api("/api/recommend", body).then(function (res) {
        state.result = res; store("result", res);
        btn.disabled = false; btn.textContent = "Evaluate";
        location.hash = "#/result";
      });
    }).catch(function (e) { btn.disabled = false; btn.textContent = "Evaluate"; toast(e.message); });
  }

  // ---- result ----------------------------------------------------------------
  var STATUS = { ok: "All constraints met", ok_with_warnings: "Recommended, with warnings", infeasible: "Not feasible as stated", invalid_input: "Input needs fixing" };
  var CORE = ["frontend", "backend", "database", "hosting"];

  var NOTICE_TITLES = {
    TIMELINE_VS_PLATFORMS: "Build effort exceeds the timeline",
    BUDGET_TOO_LOW_FOR_SCALE: "Budget is tight for this scale",
    BUDGET_EXCEEDED: "Estimated cost exceeds the budget",
    BUDGET_AT_RISK: "Budget may not cover expected usage",
    BUDGET_PEAK_HEADROOM: "Allow budget for peak usage",
    SMS_COST: "SMS costs take a large share of the budget",
    SMS_DLT_REQUIRED: "Register SMS messaging before launch",
    PCI_SCOPE: "Keep card data with a certified payment provider",
    PCI_TOKENISATION: "Use hosted or tokenised checkout",
    DPDP_NOTES: "Personal data obligations",
    GDPR_NOTES: "Personal data and transfer obligations",
    OVERENGINEERING_AVOIDED: "A heavier setup adds complexity",
    FREE_TIER_LIMITS: "Check free plan limits",
    HIPAA_BAA_NEEDED: "Health data requires vendor agreements"
  };
  function showWarnings() {
    var box = document.getElementById("result-warnings");
    if (!box) return;
    box.open = true;
    box.scrollIntoView({ block: "start", behavior: "auto" });
    box.querySelector("summary").focus({ preventScroll: true });
  }
  function renderWarnings(warnings) {
    var groups = [
      { severity: "critical", title: "Critical" },
      { severity: "warning", title: "Warnings" },
      { severity: "info", title: "Information" },
      { severity: "other", title: "Other notices" }
    ];
    return h("details", { class: "sec warnings", id: "result-warnings", open: warnings.some(function (w) { return w.severity === "critical"; }) ? true : null },
      h("summary", null, h("h2", { text: "Warnings & notes" }), h("span", { class: "notice-count", text: String(warnings.length) })),
      warnings.length ? groups.map(function (group) {
        var items = warnings.filter(function (w) {
          return group.severity === "other" ? ["critical", "warning", "info"].indexOf(w.severity) < 0 : w.severity === group.severity;
        });
        if (!items.length) return null;
        return h("section", { class: "notice-group " + group.severity },
          h("h3", { text: group.title + " (" + items.length + ")" }),
          items.map(function (w) {
            return h("details", { class: "notice", open: group.severity === "critical" ? true : null },
              h("summary", { text: NOTICE_TITLES[w.code] || (w.code ? nice(w.code.toLowerCase()) : w.message || "Notice") }),
              h("div", { class: "notice-body" }, h("p", { text: w.message || "" }),
                w.fields && w.fields.length ? h("p", { class: "note", text: "Related inputs: " + w.fields.map(checkLabel).join(", ") }) : null));
          }));
      }) : h("p", { class: "note", text: "No warnings or notes." }));
  }

  function renderResult() {
    var r = state.result;
    if (!r) { location.hash = "#/start"; return; }
    main.textContent = "";
    main.appendChild(h("p", { class: "eyebrow", text: "Step three" }));
    main.appendChild(h("h1", { id: "page-h" }, "The ", h("em", { text: "recommendation." })));
    main.appendChild(h("div", { class: "banner " + r.status, role: "status" }, h("span", { class: "st", text: STATUS[r.status] || r.status }),
      (r.warnings || []).length ? h("button", { type: "button", class: "notice-jump", onclick: showWarnings, text: "View warnings & notes (" + r.warnings.length + ")" }) : null));
    var stack = r.stack;
    if (!stack) {
      var blockers = (r.warnings || []).filter(function (w) { return w.severity === "critical"; });
      main.appendChild(h("section", { class: "sec" }, h("h2", { text: "What is blocking this" }),
        (r.validation_errors || []).map(function (e) { return h("div", { class: "sev critical" }, h("b", { text: "invalid" }), e.message, h("div", { class: "f", text: e.field })); }),
        (r.repairs || []).length ? h("div", null, h("h4", { class: "sh", style: "margin-top:16px", text: "Single changes that make a stack possible" }),
          tbl(["Change", "Resulting stack", "Core + required services per month"], r.repairs.map(function (x) {
            var st = x.stack || {};
            return [x.change.charAt(0).toUpperCase() + x.change.slice(1), [st.frontend, st.backend, st.database, st.hosting].filter(function (v) { return v && v !== "None"; }).join(", "),
              usd(x.cost_usd[0]) + " to " + usd(x.cost_usd[1])];
          }))) : null,
        !blockers.length && !(r.validation_errors || []).length ? h("p", { class: "empty", text: "No stack could be produced for this input." }) : null));
      main.appendChild(renderWarnings(r.warnings || []));
      main.appendChild(h("div", { class: "actions" },
        h("a", { class: "btn btn-primary", href: "#/review", text: "Request revisions: back to the form" }),
        h("button", { type: "button", onclick: downloadJson }, "Download JSON")));
      return;
    }
    main.appendChild(h("p", { class: "summary", text: r.summary || "" }));
    if (r.architecture_style) main.appendChild(h("span", { class: "badge style", text: "Architecture: " + nice(r.architecture_style) }));
    if (state.llm && r.stack) {
      var slot = h("div", { class: "note", style: "margin-top:16px", text: "Writing a plain-language summary..." });
      main.appendChild(slot);
      api("/api/narrate", { result: r }).then(function (ai) {
        if (ai && ai.text) {
          slot.className = "ai"; slot.removeAttribute("style"); slot.textContent = "";
          slot.appendChild(h("p", { class: "eyebrow", text: "Plain-language rewording (AI)" }));
          slot.appendChild(h("p", { text: ai.text }));
          slot.appendChild(h("p", { class: "note", text: "Written by a model from the engine's own result. It does not change the stack." }));
        } else { slot.textContent = (ai && ai.notice) || ""; }
      }).catch(function () { slot.textContent = ""; });
    }

    var index = h("div", { class: "index" }, CORE.map(function (k) { return indexLine(k, stack[k]); }),
      (stack.supporting || []).map(function (s) { return indexLine(s.layer, s); }));
    main.appendChild(index);

    main.appendChild(h("section", { class: "sec" }, h("h2", { text: "Why each layer" }),
      h("div", { class: "cards" }, CORE.map(function (k, i) { return card(k, stack[k], i === 0); }),
        r.architecture ? card("architecture", { choice: nice(r.architecture.style), justification: r.architecture.justification,
          driven_by: r.architecture.driven_by, alternatives: r.architecture.alternatives, tradeoffs: [] }, false) : null)));
    if ((stack.supporting || []).length) main.appendChild(h("section", { class: "sec" }, h("h2", { text: "Supporting services" }),
      h("div", { class: "cards" }, stack.supporting.map(function (s) { return card(s.layer, s, false); }))));

    var cost = r.estimated_monthly_cost_usd;
    if (cost) {
      var rate = (state.meta && state.meta.inr_per_usd) || 88;
      var costBig = h("span", { class: "big" });
      var costRows = h("div", { style: "margin-top:16px" });
      var shown = (cost.breakdown || []).filter(function (b) { return b.max > 0; });
      var seg = h("div", { class: "seg", role: "group", "aria-label": "Currency" });
      function paint() {
        var inr = state.currency === "INR", k = inr ? rate : 1, sym = inr ? "INR " : "$";
        var f = function (n) { return sym + Math.round(n * k).toLocaleString(inr ? "en-IN" : "en-US"); };
        costBig.textContent = f(cost.min) + " to " + f(cost.max);
        costRows.textContent = "";
        if (shown.length) costRows.appendChild(tbl(["Layer", "Item", "Per month"], shown.map(function (b) {
          return [nice(b.layer), b.item, b.min === b.max ? f(b.min) : f(b.min) + " to " + f(b.max)]; })));
        Array.prototype.forEach.call(seg.children, function (b) { b.setAttribute("aria-pressed", String(b.dataset.c === state.currency)); });
      }
      ["USD", "INR"].forEach(function (c) { seg.appendChild(h("button", { type: "button", "data-c": c, text: c, onclick: function () { state.currency = c; paint(); } })); });
      main.appendChild(h("section", { class: "sec" }, h("h2", { text: "Monthly cost" }),
        h("div", { class: "cost" }, costBig, h("span", { class: "muted", text: "per month" }), seg),
        cost.credits ? h("p", { style: "margin-top:12px;max-width:80ch", text: cost.credits.provider + " credits cover about $" + cost.credits.covered_usd[0].toLocaleString("en-US") + " to $" + cost.credits.covered_usd[1].toLocaleString("en-US") + " of this per month" +
          (cost.credits.first_year_average_usd ? "; first-year average after credits: $" + cost.credits.first_year_average_usd[0].toLocaleString("en-US") + " to $" + cost.credits.first_year_average_usd[1].toLocaleString("en-US") + " per month." : ".") }) : null,
        shown.length ? costRows : null,
        h("ul", { class: "muted", style: "margin-top:16px;max-width:80ch" }, (cost.assumptions || []).filter(function (a) { return !(cost.breakdown || []).length || !/^[A-Z][A-Za-z\/ ]* \(.*\): about /.test(a); }).map(function (a) { return h("li", { text: a }); })),
        h("p", { class: "note", text: "Order of magnitude, not a quote. INR uses a fixed rate of " + rate + " per USD." })));
      paint();
    }

    var rb = r.robustness;
    if (rb && rb.checks) {
      var crows = rb.checks.map(function (c) {
        var to = c.same_stack ? "Same stack" : c.result ? c.result : (c.swaps || []).map(function (s) { return nice(s.layer) + ": " + s.to; }).join(", ");
        return [c.change.charAt(0).toUpperCase() + c.change.slice(1), to];
      });
      main.appendChild(h("section", { class: "sec" }, h("h2", { text: "How firm the pick is" }),
        h("p", { class: "firm" }, h("span", { class: "badge verdict " + rb.verdict.replace(/ /g, "-"), text: rb.verdict }),
          h("span", { text: " " + rb.checks_held + " of " + rb.checks_run + " checks keep the same stack" + (typeof rb.margin_points === "number" && rb.margin_points >= 0 ? "; it leads the next different stack by " + rb.margin_points.toFixed(2) + " points." : ".") })),
        h("p", { style: "max-width:80ch", text: rb.summary }),
        sub("Every check", tbl(["If", "Then the pick is"], crows))));
    }
    var opts = r.trade_off_options || [];
    if (opts.length) {
      main.appendChild(h("section", { class: "sec" }, h("h2", { text: "If you want something different" }),
        h("p", { class: "note", style: "max-width:80ch", text: "The best-scoring stack that delivers each change, and the points it gives up. Costs cover the four core layers plus the cheapest required supporting services." }),
        h("div", { class: "opts" }, opts.map(function (o) {
          if (!o.available) return h("div", { class: "opt off" }, h("h4", { text: o.title }), h("p", { class: "muted", text: o.note }));
          return h("div", { class: "opt" }, h("h4", { text: o.title }),
            h("p", { class: "swaps", text: (o.swaps || []).map(function (s) { return nice(s.layer) + ": " + s.from + " to " + s.to; }).join("; ") }),
            h("p", { text: o.gains.charAt(0).toUpperCase() + o.gains.slice(1) + "." }),
            h("p", { class: "muted", text: (o.points_behind >= 0 ? o.points_behind.toFixed(1) + " points behind" : (-o.points_behind).toFixed(1) + " points ahead") +
              (o.gives_up && o.gives_up.length ? "; gives up " + o.gives_up.join(", ") : "") + ". Core layers " + usd(o.cost_usd[0]) + " to " + usd(o.cost_usd[1]) + " per month." }));
        }))));
    }

    var ws = r.warnings || [];
    main.appendChild(renderWarnings(ws));
    if ((r.assumptions || []).length) main.appendChild(h("section", { class: "sec" }, h("h2", { text: "Assumptions" }),
      h("ul", { class: "muted" }, r.assumptions.map(function (a) { return h("li", { text: a }); }))));


    main.appendChild(h("div", { class: "actions" },
      h("button", { class: "btn-primary", type: "button", onclick: downloadJson }, "Download JSON"),
      h("button", { type: "button", onclick: downloadPdf }, "Download PDF"),
      h("a", { class: "btn", href: "#/review", text: "Adjust inputs" })));
  }

  function renderCalculations() {
    var r = state.result;
    main.textContent = "";
    main.appendChild(h("a", { class: "btn btn-quiet", href: r ? "#/result" : "#/start", text: r ? "← Back to result" : "← Start a project" }));
    main.appendChild(h("h1", { id: "page-h", text: "The calculations.", style: "margin-top:24px" }));
    if (!r) {
      main.appendChild(h("p", { class: "empty", text: "Evaluate a project to see its calculations." }));
      return;
    }
    main.appendChild(inspect(r));
    main.appendChild(h("div", { class: "actions" }, h("a", { class: "btn", href: "#/result", text: "Back to result" })));
  }

  function indexLine(k, c) {
    return h("div", { class: "line" }, h("span", { class: "k", text: nice(k) }), h("span", { class: "lead", "aria-hidden": "true" }),
      h("span", { class: "v", text: (c && c.choice) || "None" }));
  }
  function card(layer, c, open) {
    if (!c) return null;
    var sents = (c.justification || "").split(/(?<=[.!?])\s+/), first = sents[0] || "", rest = sents.slice(1).join(" ");
    var body = h("div", { class: "body" },
      rest ? h("div", null, h("h4", { text: "More detail" }), h("p", { style: "margin:0", text: rest })) : null,
      c.driven_by && c.driven_by.length ? h("div", null, h("h4", { text: "Driven by" }), c.driven_by.map(function (d) { return h("span", { class: "pill", text: d }); })) : null,
      c.confidence || c.region || c.engine ? h("p", { class: "muted", style: "margin:0" }, [
        c.confidence ? "Confidence: " + c.confidence + (typeof c.margin_points !== "number" ? "" : c.margin_points >= 0 ? " (" + c.margin_points.toFixed(2) + " points ahead of the next option for this layer)" :
          " (the next option scores " + (-c.margin_points).toFixed(2) + " points higher; this one wins the near-tie rule on cost, then complexity and operations load)") : null,
        c.engine && c.offering ? "Engine: " + c.engine + ", run as " + c.offering : null,
        c.region ? "Region: " + c.region : null].filter(Boolean).join(". ") + ".") : null,
      h("div", { class: "two" },
        h("div", null, h("h4", { text: "Alternatives and why not" }), (c.alternatives || []).length ?
          h("ul", null, c.alternatives.map(function (a) { return h("li", { class: "alt" }, h("b", { text: a.choice }), ": " + (a.why_not || "")); })) :
          h("p", { class: "muted", style: "margin:0", text: "No alternative applies." })),
        h("div", null, h("h4", { text: "Trade-offs" }), (c.tradeoffs || []).length ?
          h("ul", null, c.tradeoffs.map(function (t) { return h("li", { text: t }); })) : h("p", { class: "muted", style: "margin:0", text: "None noted." }))),
      (c.sources || []).length ? h("div", null, h("h4", { text: "Sources" }), h("ul", null, c.sources.map(function (x) {
        return h("li", null, h("a", { href: x.url, target: "_blank", rel: "noopener noreferrer", text: x.title }));
      }))) : null);
    var d = h("details", { class: "card" },
      h("summary", null, h("span", { class: "layer", text: nice(layer) }), h("span", { class: "choice", text: c.choice || "None" }), h("span", { class: "why1", text: first })), body);
    return d;
  }

  // Generic renderer for the engine's decision trace so any shape stays readable.
  function isScalar(v) { return v === null || ["string", "number", "boolean"].indexOf(typeof v) >= 0; }
  function cellText(v) { return v === null || v === undefined ? "" : isScalar(v) ? fmt(v) : Array.isArray(v) ? v.map(function (x) { return isScalar(x) ? fmt(x) : (x && (x.name || x.id || x.choice)) || JSON.stringify(x); }).join(", ") : Object.keys(v).map(function (k) { return k + ": " + cellText(v[k]); }).join("; "); }
  function dataTable(rows) {
    var cols = [];
    rows.forEach(function (r) { Object.keys(r).forEach(function (k) { if (cols.indexOf(k) < 0) cols.push(k); }); });
    return h("div", { class: "scroll" }, h("table", null,
      h("thead", null, h("tr", null, cols.map(function (c) { return h("th", { scope: "col", text: c.replace(/_/g, " ") }); }))),
      h("tbody", null, rows.map(function (r) { return h("tr", null, cols.map(function (c) { return h("td", { class: typeof r[c] === "number" ? "num" : null, text: cellText(r[c]) }); })); }))));
  }
  function renderAny(v, depth) {
    if (isScalar(v)) return h("span", { text: cellText(v) });
    if (Array.isArray(v)) {
      if (!v.length) return h("p", { class: "muted", style: "margin:0", text: "Nothing recorded." });
      if (v.every(function (x) { return x && typeof x === "object" && !Array.isArray(x); })) return dataTable(v);
      return h("p", { style: "margin:0", text: cellText(v) });
    }
    var keys = Object.keys(v), scalars = keys.filter(function (k) { return isScalar(v[k]); }), others = keys.filter(function (k) { return !isScalar(v[k]); });
    var out = [];
    if (scalars.length) out.push(dataTable([scalars.reduce(function (o, k) { o[k] = v[k]; return o; }, {})]));
    others.forEach(function (k) {
      out.push(depth >= 2 ? h("p", { class: "mono muted", text: k + ": " + cellText(v[k]) }) :
        h("details", null, h("summary", { text: nice(k) }), h("div", { class: "in" }, renderAny(v[k], depth + 1))));
    });
    return h("div", { class: "in", style: "padding:0" }, out);
  }
  function tbl(heads, rows, numFrom) {
    return h("div", { class: "scroll" }, h("table", null,
      h("thead", null, h("tr", null, heads.map(function (c, i) { return h("th", { scope: "col", class: i >= (numFrom === undefined ? 99 : numFrom) ? "num" : null, text: c }); }))),
      h("tbody", null, rows.map(function (r) { return h("tr", null, r.map(function (c, i) { return h("td", { class: i >= (numFrom === undefined ? 99 : numFrom) ? "num" : null, text: c }); })); }))));
  }
  function kv(obj) {
    var keys = Object.keys(obj).filter(function (k) { return obj[k] !== null && obj[k] !== undefined; });
    return h("dl", { class: "kv" }, keys.map(function (k) {
      return h("div", null, h("dt", { text: nice(k) }), h("dd", { text: cellText(obj[k]) }));
    }));
  }
  function pairs(obj, digits) {
    return Object.keys(obj).map(function (k) { return [nice(k), typeof obj[k] === "number" ? obj[k].toFixed(digits === undefined ? 3 : digits) : cellText(obj[k])]; });
  }
  function sub(title, node, open) { return h("details", { open: open ? true : null }, h("summary", { text: title }), h("div", { class: "in" }, node)); }
  function nameOf(v) { return typeof v === "string" ? v : (v && (v.name || v.id)) || ""; }

  function inspect(r) {
    var parts = [], t = r.decision_trace || {}, sc = r.score;
    if (r.profile) {
      var prof = {};
      Object.keys(r.profile).forEach(function (k) { prof[k] = r.profile[k]; });
      parts.push(sub("Derived profile of the project", kv(prof)));
    }
    if (t.weights || t.layer_weights) {
      var wnode = h("div", { class: "two" },
        t.weights ? h("div", null, h("h4", { class: "sh", text: "Criterion weights (after context adjustment)" }), tbl(["Criterion", "Weight"], pairs(t.weights, 3), 1)) : null,
        t.layer_weights ? h("div", null, h("h4", { class: "sh", text: "Layer weights in the stack score" }), tbl(["Layer", "Weight"], pairs(t.layer_weights, 2), 1)) : null);
      parts.push(sub("Weights", wnode));
    }
    if (sc && typeof sc === "object") {
      var inner = [h("p", { class: "mono muted", style: "margin:0", text: t.score_formula || "" }),
        tbl(["Part", "Value"], [["Total", fmt(sc.total)], ["Quality (Q)", fmt(sc.quality)], ["Fit (F)", fmt(sc.fit)], ["Penalties", fmt(sc.penalty)]], 1)];
      inner.push(h("div", { class: "two" },
        sc.criteria ? h("div", null, h("h4", { class: "sh", text: "Quality criteria (0-10)" }), tbl(["Criterion", "Score"], pairs(sc.criteria, 2), 1)) : null,
        sc.fit_terms ? h("div", null, h("h4", { class: "sh", text: "Fit terms (0-1)" }), tbl(["Term", "Value"], pairs(sc.fit_terms, 3), 1)) : null));
      if (sc.penalties) {
        var pen = Object.keys(sc.penalties).filter(function (k) { return Math.abs(sc.penalties[k]) > 0; });
        inner.push(h("div", null, h("h4", { class: "sh", text: "Penalties applied" }),
          pen.length ? tbl(["Penalty", "Points"], pen.map(function (k) { return [nice(k), sc.penalties[k].toFixed(2)]; }), 1) : h("p", { class: "muted", style: "margin:0", text: "No penalties applied." })));
      }
      parts.push(sub("Score breakdown of the chosen stack", h("div", { class: "stackcol" }, inner)));
    }
    if (Array.isArray(r.ranking) && r.ranking.length) {
      var tops = Array.isArray(t.top_stacks) ? t.top_stacks : [];
      var rows = r.ranking.map(function (x, i) {
        var st = x.stack || {};
        var pn = (tops[i] && tops[i].penalties) || {};
        var pt = Object.keys(pn).filter(function (k) { return Math.abs(pn[k]) > 0; }).map(function (k) { return nice(k) + " " + Number(pn[k]).toFixed(1); }).join(", ");
        return [String(x.rank || i + 1), st.frontend || "", st.backend || "", st.database || "", st.hosting || "", fmt(x.score),
          x.cost_usd ? usd(x.cost_usd[0]) + " to " + usd(x.cost_usd[1]) : "", pt || "none"];
      });
      var gap = null;
      if (r.ranking.length > 1 && typeof r.ranking[0].score === "number" && typeof r.ranking[1].score === "number") {
        var a = r.ranking[0], b = r.ranking[1], d = a.score - b.score, cd = "";
        if (a.cost_usd && b.cost_usd) {
          var lo = b.cost_usd[0] - a.cost_usd[0], hi = b.cost_usd[1] - a.cost_usd[1];
          cd = lo === 0 && hi === 0 ? " Their comparable cost is the same." : " The runner-up costs " + (lo >= 0 ? "+" : "-") + usd(Math.abs(lo)) + " to " + (hi >= 0 ? "+" : "-") + usd(Math.abs(hi)) + " per month against the winner (core layers plus required supporting services).";
        }
        gap = h("p", { style: "margin:0 0 12px", text: "The winner leads the runner-up by " + d.toFixed(2) + " points" + (d < 2 ? ", a near tie." : ".") + cd });
      }
      parts.push(sub("Ranking of the top stacks", h("div", null, gap, tbl(["#", "Frontend", "Backend", "Database", "Hosting", "Score", "Core + required services per month", "Penalties"], rows),
        h("p", { class: "note", style: "margin:10px 0 0", text: "Cost covers the four core layers plus the cheapest required supporting services. The monthly cost band on the result page prices the services actually chosen." })), true));
    }
    if (t.layers) {
      CORE.forEach(function (k) {
        var L = t.layers[k]; if (!L) return;
        var el = Array.isArray(L.eliminated) ? L.eliminated : [];
        var chosen = Array.isArray(L.chosen) ? L.chosen.join(" + ") : L.chosen;
        var node = h("div", { class: "stackcol" },
          h("p", { style: "margin:0", text: (L.candidates || 0) + " candidates, " + el.length + " eliminated, " + (L.eligible_after_stack_checks || 0) + " left after compatibility checks. Chosen: " + (chosen || "none") + "." }),
          el.length ? tbl(["Eliminated", "Reason"], el.map(function (e) { return [nameOf(e), (e.reasons || [e.reason || ""]).join("; ")]; })) :
            h("p", { class: "muted", style: "margin:0", text: "Nothing was eliminated by hard constraints." }));
        parts.push(sub("Layer: " + nice(k) + " (" + el.length + " eliminated)", node));
      });
    }
    if (t.stack_search) {
      var ss = t.stack_search, pr = ss.pruned_by_reason || {};
      parts.push(sub("Stack search", h("div", { class: "stackcol" },
        kv({ considered: ss.considered, scored: ss.scored, removed_by_hard_budget: ss.removed_by_hard_budget }),
        Object.keys(pr).length ? tbl(["Combinations pruned because", "Count"], Object.keys(pr).map(function (k) { return [k, fmt(pr[k])]; }), 1) : null)));
    }
    if (Array.isArray(t.relaxed_constraints) && t.relaxed_constraints.length) parts.push(sub("Relaxed constraints", h("ul", null, t.relaxed_constraints.map(function (x) { return h("li", { text: cellText(x) }); })), true));
    if (Array.isArray(t.pipeline)) parts.push(sub("Pipeline", h("ol", { style: "margin:0;padding-left:20px" }, t.pipeline.map(function (x) { return h("li", { text: String(x) }); }))));
    return h("section", { class: "sec trace", "aria-label": "Calculation details" }, parts);
  }

  function saveBlob(blob, name) {
    var a = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }
  function downloadJson() {
    var r = Object.assign({}, state.result); delete r.ai_narrative;
    saveBlob(new Blob([JSON.stringify(r, null, 2)], { type: "application/json" }), (r.scenario_id || "archlens") + "_output.json");
  }
  function downloadPdf() {
    api("/api/report.pdf", state.result).then(function (b) { saveBlob(b, (state.result.scenario_id || "archlens") + "_report.pdf"); }, function (e) { toast(e.message); });
  }

  // ---- shell -----------------------------------------------------------------
  function route() {
    var r = currentRoute();
    if (["start", "review", "result", "calculations"].indexOf(r) < 0) r = "start";
    document.querySelectorAll(".nav a").forEach(function (a) { if (a.dataset.route === r) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
    if (r === "start") renderStart(); else if (r === "review") renderReview(); else if (r === "calculations") renderCalculations(); else renderResult();
    var t = document.getElementById("page-h");
    document.title = (t ? t.textContent + " - " : "") + "ArchLens";
    window.scrollTo(0, 0);
    main.focus({ preventScroll: true });
  }

  function paintLlmToggle() {
    var b = document.getElementById("llm-toggle"), m = state.meta || {};
    var usable = m.llm_available && !m.llm_env_off;
    b.setAttribute("aria-checked", String(state.llm && usable));
    b.disabled = !usable;
    b.title = !m.llm_available ? "No API key set. The engine runs fully without one." : m.llm_env_off ? "Switched off by STACKSENSE_LLM=off." : "Optional model help for parsing and wording. Never picks the stack.";
    document.getElementById("llm-state").textContent = usable && state.llm ? "on" : "off";
  }
  document.getElementById("llm-toggle").addEventListener("click", function () {
    state.llm = !state.llm; paintLlmToggle();
  });

  window.addEventListener("hashchange", route);
  Promise.all([api("/api/meta").catch(function () { return {}; }), api("/api/scenarios").catch(function () { return []; })]).then(function (res) {
    state.meta = res[0]; state.samples = res[1] || [];
    var usable = state.meta.llm_available && !state.meta.llm_env_off;
    state.llm = !!usable;
    document.getElementById("engine-version").textContent = state.meta.engine_version || "";
    paintLlmToggle();
    state.scenario = load("scenario") || {}; state.suggestions = load("suggestions") || []; state.result = load("result");
    route();
  });
})();
