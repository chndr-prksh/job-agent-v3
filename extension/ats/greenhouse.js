// ats/greenhouse.js — Greenhouse-specific autofill
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
    const ancestorLabel = el.parentElement && el.parentElement.querySelector("label");
    if (ancestorLabel && ancestorLabel !== el) return getText(ancestorLabel);
    return el.getAttribute("data-qa") || el.getAttribute("aria-label") || "";
  }

  const Greenhouse = {
    name: "greenhouse",

    isApplyPage() {
      return !!document.querySelector('form#application_form, form.application-form, [data-mapped-to-application-form]');
    },

    isCaptchaPage() {
      return !!document.querySelector('iframe[src*="recaptcha"][src*="challenge"]');
    },

    fields() {
      const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
      return inputs
        .filter((el) => {
          if (el.type === 'hidden') return false;
          try {
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
          } catch (e) {}
          return true;
        })
        .map((el) => ({
          el,
          name: el.getAttribute("name") || "",
          id: el.getAttribute("id") || "",
          type: el.getAttribute("type") || el.tagName.toLowerCase(),
          label: detectLabel(el),
        }));
    },

    fill(profile) {
      const fields = this.fields();
      const filled = [];
      const skipped = [];

      for (const f of fields) {
        const matched = matchField(f, profile);
        if (!matched) {
          skipped.push({ name: f.name, label: f.label });
          continue;
        }
        try {
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
          console.warn("[job-agent] fill error", f.name, e);
        }
      }
      return { filled, skipped };
    },

    async attachResume(resumeDataUrl, resumeName) {
      const fileInputs = Array.from(document.querySelectorAll('input[type="file"]'));
      if (!fileInputs.length) return { ok: false, error: "no file input found" };
      const input = fileInputs[0];
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

    hasNextButton() {
      const btns = Array.from(document.querySelectorAll('button, input[type="submit"]'));
      return btns.find((b) => {
        const t = (b.innerText || b.value || "").trim().toLowerCase();
        return t === "next" || t === "continue" || t === "save and continue";
      });
    },

    clickNext() {
      const btn = this.hasNextButton();
      if (btn) { btn.click(); return true; }
      return false;
    },

    hasSubmitButton() {
      const btns = Array.from(document.querySelectorAll('button, input[type="submit"]'));
      return btns.find((b) => {
        const t = (b.innerText || b.value || "").trim().toLowerCase();
        return t.includes("submit");
      });
    },
  };

  // Order matters: more specific matches first.
  function matchField(field, profile) {
    const label = (field.label || "").toLowerCase();
    const name = field.name.toLowerCase();
    const blob = `${label} ${name}`;

    // Identity
    if (blob.includes("first") && blob.includes("name") && !blob.includes("last"))
      return { value: profile.first_name || splitName(profile.full_name)[0] || "", source: "first_name" };
    if (blob.includes("last") && blob.includes("name"))
      return { value: profile.last_name || splitName(profile.full_name)[1] || "", source: "last_name" };
    if (blob.includes("preferred") && blob.includes("first"))
      return { value: profile.first_name || "", source: "first_name" };

    // Contact
    if (blob.includes("email")) return { value: profile.email || "", source: "email" };
    if (blob.includes("phone")) return { value: profile.phone || "", source: "phone" };
    if (blob.includes("linkedin")) return { value: profile.linkedin_url || "", source: "linkedin_url" };
    if (blob.includes("website") || blob.includes("portfolio")) return { value: profile.portfolio_url || "", source: "portfolio_url" };
    if (blob.includes("github")) return { value: profile.github_url || "", source: "github_url" };

    // Work authorization (must come BEFORE country because questions mention "country")
    if (blob.includes("authorized") || blob.includes("authorisation") || blob.includes("right to work") || blob.includes("eligible to work")) {
      return { value: profile.work_authorization?.authorized_us ? "yes" : "no", source: "work_authorization" };
    }
    if (blob.includes("sponsor")) {
      return { value: profile.work_authorization?.needs_sponsorship ? "yes" : "no", source: "sponsorship" };
    }

    // Location (after work-auth so the work-auth question's "country" mention doesn't win)
    if (blob.includes("city") || (blob.includes("location") && !blob.includes("country") && !blob.includes("where"))) {
      return { value: profile.location || "", source: "location" };
    }
    // Country is OK here — most standalone "Country" labels are real country pickers
    if (/^country\b/.test(label) || /\bcountry\b/.test(name)) {
      return { value: profile.country || "United States", source: "country" };
    }

    if (blob.includes("relocate") || blob.includes("relocation")) {
      return { value: profile.preferences?.willing_to_relocate ? "yes" : "no", source: "willing_to_relocate" };
    }

    if (profile.question_bank) {
      for (const [q, a] of Object.entries(profile.question_bank)) {
        if (blob.includes(q.toLowerCase())) return { value: a, source: `question_bank:${q}` };
      }
    }
    return null;
  }

  function splitName(full) {
    if (!full) return ["", ""];
    const parts = full.trim().split(/\s+/);
    if (parts.length === 1) return [parts[0], ""];
    return [parts[0], parts.slice(1).join(" ")];
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
  window.__ATS_HANDLERS.greenhouse = Greenhouse;
})();