// ats/lever.js — Lever-specific autofill
(function () {
  function fireEvent(el, type) {
    try {
      el.dispatchEvent(new Event(type, { bubbles: true }));
    } catch (e) {
      try {
        const evt = document.createEvent("Event");
        evt.initEvent(type, true, true);
        el.dispatchEvent(evt);
      } catch (e2) {}
    }
  }

  function getText(n) {
    return (n.innerText || n.textContent || "").trim();
  }

  function detectLabel(el) {
    if (el.id) {
      const safe = (window.CSS && CSS.escape) ? CSS.escape(el.id) : el.id;
      const lab = document.querySelector(`label[for="${safe}"]`);
      if (lab) return getText(lab);
    }
    const parentLabel = el.closest("label");
    if (parentLabel) return getText(parentLabel);
    let prev = el.previousElementSibling;
    while (prev && prev.tagName !== "LABEL") prev = prev.previousElementSibling;
    if (prev && prev.tagName === "LABEL") return getText(prev);
    return el.getAttribute("placeholder") || el.getAttribute("data-qa") || el.getAttribute("aria-label") || "";
  }

  const Lever = {
    name: "lever",

    isApplyPage() {
      return !!document.querySelector('form[data-qa="apply-form"], form.application-form, .application-form');
    },

    isCaptchaPage() {
      return !!document.querySelector('iframe[src*="hcaptcha"][src*="challenge"]');
    },

    fields() {
      const form = document.querySelector('form[data-qa="apply-form"], .application-form') || document;
      const inputs = Array.from(form.querySelectorAll('input, textarea, select'));
      return inputs
        .filter((el) => {
          if (el.type === 'hidden') return false;
          if (el.type === 'checkbox' && el.name && (el.name.startsWith('eeo') || el.name === 'pronouns')) return false;
          try {
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
          } catch (e) {}
          return true;
        })
        .map((el) => ({
          el,
          name: el.getAttribute("name") || "",
          type: el.getAttribute("type") || el.tagName.toLowerCase(),
          label: detectLabel(el),
        }));
    },

    fill(profile) {
      const fields = this.fields();
      const filled = [];
      const skipped = [];

      for (const f of fields) {
        try {
          // Handle urls[LinkedIn] etc. by name pattern (no label needed)
          if (f.name && f.name.startsWith("urls[")) {
            const key = f.name.match(/urls\[(\w+)\]/)?.[1]?.toLowerCase();
            const value = key === "linkedin" ? profile.linkedin_url
                       : key === "github" ? profile.github_url
                       : key === "other" ? (profile.portfolio_url || profile.linkedin_url)
                       : "";
            if (value) {
              f.el.value = value;
              fireEvent(f.el, "input");
              fireEvent(f.el, "change");
              filled.push({ name: f.name, label: f.label, value, source: `urls.${key}` });
            }
            continue;
          }

          const matched = matchLeverField(f, profile);
          if (!matched) {
            skipped.push({ name: f.name, label: f.label });
            continue;
          }

          if (f.type === "select" || f.el.tagName === "SELECT") {
            pickSelectOption(f.el, matched.value);
          } else if (f.type === "radio") {
            pickRadio(f.el, matched.value);
          } else if (f.type === "checkbox") {
            f.el.checked = Boolean(matched.value);
          } else if (f.type === "file") {
            continue;
          } else {
            f.el.value = matched.value;
            fireEvent(f.el, "input");
            fireEvent(f.el, "change");
          }
          filled.push({ name: f.name, label: f.label, value: matched.value, source: matched.source });
        } catch (e) {
          console.warn("[job-agent] lever fill error", f.name, e);
        }
      }
      return { filled, skipped };
    },

    async attachResume(resumeDataUrl, resumeName) {
      const input = document.querySelector('input[name="resume"]');
      if (!input) return { ok: false, error: "no resume input" };
      try {
        const blob = await (await fetch(resumeDataUrl)).blob();
        const dt = new DataTransfer();
        dt.items.add(new File([blob], resumeName, { type: "application/pdf" }));
        input.files = dt.files;
        fireEvent(input, "change");
        return { ok: true, attached: resumeName };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },

    hasSubmitButton() {
      return Array.from(document.querySelectorAll('button')).find((b) =>
        (b.innerText || "").toLowerCase().includes("submit application")
      );
    },

    clickSubmit() {
      const btn = this.hasSubmitButton();
      if (btn) { btn.click(); return true; }
      return false;
    },

    hasNextButton() { return null; },
    clickNext() { return false; },
  };

  function matchLeverField(field, profile) {
    const label = (field.label || "").toLowerCase();
    const name = field.name.toLowerCase();
    const blob = `${label} ${name}`;

    if (blob === "name" || (blob.includes("name") && !blob.includes("first") && !blob.includes("last"))) {
      return { value: profile.full_name || "", source: "full_name" };
    }
    if (blob.includes("email")) return { value: profile.email || "", source: "email" };
    if (blob.includes("phone")) return { value: profile.phone || "", source: "phone" };
    if (blob.includes("location")) return { value: profile.location || "", source: "location" };
    if (blob === "org" || blob.includes("current company") || blob.includes("current employer")) {
      const lastJob = (profile.work_history || [])[0];
      return { value: lastJob?.company || "", source: "current_company" };
    }
    if (blob.includes("authorized") || blob.includes("authorisation") || blob.includes("right to work")) {
      return { value: profile.work_authorization?.authorized_us ? "yes" : "no", source: "work_authorization" };
    }
    if (blob.includes("sponsor")) {
      return { value: profile.work_authorization?.needs_sponsorship ? "yes" : "no", source: "sponsorship" };
    }
    if (profile.question_bank) {
      for (const [q, a] of Object.entries(profile.question_bank)) {
        if (blob.includes(q.toLowerCase())) return { value: a, source: `question_bank:${q}` };
      }
    }
    return null;
  }

  function pickSelectOption(selectEl, value) {
    const v = String(value).toLowerCase();
    const opts = Array.from(selectEl.options);
    let best = opts.find((o) => o.value.toLowerCase() === v);
    if (!best) best = opts.find((o) => o.text.toLowerCase() === v);
    if (!best) best = opts.find((o) => o.text.toLowerCase().includes(v));
    if (!best) best = opts.find((o) => o.value.toLowerCase().includes(v));
    if (best) {
      selectEl.value = best.value;
      fireEvent(selectEl, "change");
    }
  }

  function pickRadio(radio, value) {
    const v = String(value).toLowerCase();
    if (radio.value.toLowerCase() === v) {
      radio.checked = true;
      fireEvent(radio, "change");
      return true;
    }
    return false;
  }

  window.__ATS_HANDLERS = window.__ATS_HANDLERS || {};
  window.__ATS_HANDLERS.lever = Lever;
})();