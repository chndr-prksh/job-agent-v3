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
    // Greenhouse: labels often live in a sibling label/legend or higher up. Walk up.
    let container = el.parentElement;
    for (let i = 0; i < 5 && container; i++) {
      const lab = container.querySelector(':scope > label, :scope > .field-label, :scope > legend');
      if (lab && lab !== el) return getText(lab);
      container = container.parentElement;
    }
    return el.getAttribute("data-qa") || el.getAttribute("aria-label") || el.id || "";
  }

  const Greenhouse = {
    name: "greenhouse",

    isApplyPage() {
      // Loosened: any Greenhouse URL with apply/job pattern OR known form selectors OR visible inputs
      const url = location.href.toLowerCase();
      if (url.includes('greenhouse.io') && (url.includes('/jobs/') || url.includes('#application') || url.includes('application') || url.includes('apply'))) return true;
      if (document.querySelector('form#application_form, form#application-form, form.application-form, [class*="application--form" i], [data-mapped-to-application-form]')) return true;
      // Greenhouse embedded forms
      if (document.querySelector('[data-testid*="application"], [class*="application-form" i], form[class*="job-application" i]')) return true;
      // Fallback: if there are many form inputs on a greenhouse page, treat it as apply
      const inputCount = document.querySelectorAll('input:not([type="hidden"]), textarea, select').length;
      if (inputCount >= 4) return true;
      return false;
    },

    isCaptchaPage() {
      return !!document.querySelector('iframe[src*="recaptcha"][src*="challenge"]');
    },

    fields() {
      const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
      return inputs
        .filter((el) => {
          if (el.type === 'hidden') return false;
          // Skip intl-tel-input's hidden country search box
          if (el.id && el.id.includes('iti-') && el.id.includes('__search-input')) return false;
          try {
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
          } catch (e) {}
          return true;
        })
        .map((el) => {
          const isIntl = isIntlTelInput(el);
          return {
            el,
            name: el.getAttribute("name") || "",
            id: el.getAttribute("id") || "",
            type: el.getAttribute("type") || el.tagName.toLowerCase(),
            label: isIntl ? "PHONE_COUNTRY" : detectLabel(el),
            is_intl_tel: isIntl,
          };
        });
    },

    async fill(profile) {
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
            // Special case: intl-tel-input phone country (wrapper sets data-value)
            if (isIntlTelInput(f.el)) {
              setIntlTelCountry(f.el, matched.value);
            } else if (isComboboxInput(f.el)) {
              await fillCombobox(f.el, matched.value);
            } else {
              f.el.value = matched.value;
              fireEvent(f.el, "input");
              fireEvent(f.el, "change");
            }
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

      // Detect MIME from extension if not provided
      let mime = "application/octet-stream";
      if (resumeName && resumeName.toLowerCase().endsWith(".pdf")) mime = "application/pdf";
      else if (resumeName && resumeName.toLowerCase().endsWith(".docx")) mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
      else if (resumeName && resumeName.toLowerCase().endsWith(".doc")) mime = "application/msword";

      try {
        const blob = await (await fetch(resumeDataUrl)).blob();
        const typedBlob = new Blob([blob], { type: mime });
        const file = new File([typedBlob], resumeName, { type: mime, lastModified: Date.now() });
        const dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;

        // Greenhouse is React-controlled — must dispatch synthetic events to trigger handlers.
        // Native change alone isn't enough. Use InputEvent + bubble.
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "files")?.set;
        if (nativeInputValueSetter) {
          nativeInputValueSetter.call(input, dt.files);
        }
        fireEvent(input, "input");
        fireEvent(input, "change");

        // Confirm file actually got set
        if (input.files && input.files.length > 0 && input.files[0].name === resumeName) {
          return { ok: true, attached: resumeName };
        }
        return { ok: false, error: "browser refused to set files" };
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
        if (t.includes("submit")) return true;
        if (t.includes("send application")) return true;
        if (t.includes("apply now")) return true;
        // Greenhouse forms often have a button[data-mapped-to-application-form]
        if (b.matches('[data-mapped-to-application-form], button[type="submit"]')) return true;
        // Geotab / Greenhouse: input with id like 'submit_app' or 'application--submit'
        if (b.id && /submit|apply/i.test(b.id)) return true;
        return false;
      });
    },

    clickSubmit() {
      const btn = this.hasSubmitButton();
      if (btn) { btn.click(); return true; }
      return false;
    },
  };

  // Order matters: more specific matches first.
  function matchField(field, profile) {
    const label = (field.label || "").toLowerCase();
    const name = field.name.toLowerCase();
    const id = (field.id || "").toLowerCase();
    const blob = `${label} ${name} ${id}`;

    // Identity — match by id first (Geotab uses id without name)
    if (id === 'first_name' || (blob.includes("first") && blob.includes("name") && !blob.includes("last")))
      return { value: profile.first_name || splitName(profile.full_name)[0] || "", source: "first_name" };
    if (id === 'last_name' || (blob.includes("last") && blob.includes("name")))
      return { value: profile.last_name || splitName(profile.full_name)[1] || "", source: "last_name" };
    if (blob.includes("preferred") && blob.includes("first"))
      return { value: profile.first_name || "", source: "first_name" };

    // Education (school--0 / degree--0 patterns)
    if (id.startsWith('school') || blob.includes("school")) return { value: ((profile.education && profile.education[0]) || {}).school || "", source: "education.school" };
    if (id.startsWith('degree') || blob.includes("degree")) return { value: ((profile.education && profile.education[0]) || {}).degree || "", source: "education.degree" };
    if (id.startsWith('discipline') || blob.includes("discipline") || blob.includes("major")) return { value: ((profile.education && profile.education[0]) || {}).discipline || "", source: "education.discipline" };

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

    // EEOC / Demographics (these are RADIO/Select for "yes/no" or "I decline")
    // MUST come before any location/ethnicity guesses
    if (blob.includes("hispanic") || blob.includes("latino") || blob.includes("ethnicity") || blob.includes("race")) {
      return { value: "decline", source: "eeoc.race" };
    }
    if (blob === "gender" || /\bgender\b/.test(blob)) {
      return { value: profile.demographics?.gender || "decline", source: "eeoc.gender" };
    }
    if (blob.includes("veteran")) {
      return { value: "I am not a protected veteran", source: "eeoc.veteran" };
    }
    if (blob.includes("disability")) {
      return { value: "I don't have a disability", source: "eeoc.disability" };
    }

    // Location (after work-auth + EEOC so those questions don't get mis-classified as location)
    if (blob.includes("city") || (blob.includes("location") && !blob.includes("country") && !blob.includes("where") && !blob.includes("ethnicity") && !blob.includes("hispanic"))) {
      return { value: profile.location || "", source: "location" };
    }
    // Country: only if not intl-tel-input (intl-tel is handled separately)
    if (!field.is_intl_tel && (/^country\b/.test(label) || /\bcountry\b/.test(name))) {
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

  // ---- intl-tel-input handling ----
  // intl-tel-input is a phone-number widget with a country picker hidden behind it.
  // The "country" input inside is NOT a real combobox — its wrapper gets data-value
  // and the popup is the same one for all 244 countries.
  function isIntlTelInput(el) {
    if (!el) return false;
    if (el.id && el.id.startsWith("iti-")) return true;
    if (el.closest && el.closest(".iti, .intl-tel-input, .phone-input__country")) return true;
    if (el.classList && el.classList.contains("iti__search-input")) return true;
    return false;
  }

  function setIntlTelCountry(countryInput, value) {
    // Find the wrapper that holds data-value
    const wrapper = countryInput.closest("[data-value]") || countryInput.closest(".iti, .intl-tel-input, .phone-input__country");
    if (!wrapper) {
      console.warn("[job-agent] intl-tel: no wrapper found");
      return;
    }
    // value is something like "United States" — intl-tel-input uses ISO codes (us) or dial codes (+1)
    // Just set data-value to the country name; the form's react handler reads this
    wrapper.setAttribute("data-value", String(value));
    // Some implementations also expect a hidden <input> with the iso code
    const hidden = wrapper.querySelector('input[type="hidden"]');
    if (hidden) {
      // We don't know the ISO code without a lookup; set to lowercased name as fallback
      hidden.value = String(value).toLowerCase().split(" ")[0].slice(0, 2);
    }
    console.log("[job-agent] intl-tel: set country data-value =", value);
  }

  // ---- Combobox / typeahead handling (Greenhouse react-aria-combobox) ----

  // Detect if an input is a combobox / typeahead (vs a plain text input)
  function isComboboxInput(el) {
    if (!el) return false;
    if (el.tagName === "SELECT") return false;
    // Greenhouse uses react-aria comboboxes — inputs have role=combobox, aria-autocomplete=list, or are inside a combobox wrapper
    if (el.getAttribute("role") === "combobox") return true;
    if (el.getAttribute("aria-autocomplete") === "list") return true;
    if (el.getAttribute("aria-haspopup")) return true;
    // Common Greenhouse CSS markers
    if ((el.className || "").toLowerCase().includes("typeahead")) return true;
    if ((el.className || "").toLowerCase().includes("combobox")) return true;
    if (el.closest('[class*="typeahead" i], [class*="combobox" i]')) return true;
    return false;
  }

  // Fill a combobox input. Types the value, waits for dropdown, picks the best matching option.
  async function fillCombobox(input, value) {
    if (!value) return;
    const target = String(value);
    input.focus();

    // Greenhouse's custom select widget needs KEYSTROKES to open, not just input events.
    // Strategy: clear input, simulate real keystrokes character by character.
    // Each keystroke fires: keydown, keypress, input, keyup — Greenhouse opens the popup on first char.
    try {
      const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
      if (nativeSetter) {
        nativeSetter.call(input, "");
      } else {
        input.value = "";
      }
      fireEvent(input, "input");
      await new Promise((r) => setTimeout(r, 100));
    } catch (e) {}

    // Type each character with a key event so the popup opens
    for (let i = 0; i < target.length; i++) {
      const ch = target[i];
      const keydown = new KeyboardEvent("keydown", { key: ch, code: `Key${ch.toUpperCase()}`, bubbles: true, cancelable: true });
      const keypress = new KeyboardEvent("keypress", { key: ch, bubbles: true, cancelable: true });
      input.dispatchEvent(keydown);
      input.dispatchEvent(keypress);
      try {
        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
        if (nativeSetter) nativeSetter.call(input, target.slice(0, i + 1));
        else input.value = target.slice(0, i + 1);
      } catch (e) {
        input.value = target.slice(0, i + 1);
      }
      const inputEvent = new Event("input", { bubbles: true, cancelable: true });
      input.dispatchEvent(inputEvent);
      const keyup = new KeyboardEvent("keyup", { key: ch, code: `Key${ch.toUpperCase()}`, bubbles: true, cancelable: true });
      input.dispatchEvent(keyup);
      await new Promise((r) => setTimeout(r, 30));
    }

    // Wait for the popup to fully render
    await new Promise((r) => setTimeout(r, 800));

    // Find the popup. Greenhouse may render it in a portal at body level.
    const allListboxes = Array.from(document.querySelectorAll('[role="listbox"]'));
    const visible = allListboxes.find((lb) => {
      const r = lb.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });

    if (!visible) {
      // Last resort: send ArrowDown which forces many comboboxes to open their popup
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", code: "ArrowDown", bubbles: true, cancelable: true }));
      await new Promise((r) => setTimeout(r, 500));
    }

    const listbox = visible || Array.from(document.querySelectorAll('[role="listbox"]')).find((lb) => {
      const r = lb.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });

    if (!listbox) {
      console.warn("[job-agent] combobox: still no listbox after ArrowDown", target);
      // Just commit the typed value
      fireEvent(input, "change");
      fireEvent(input, "blur");
      return;
    }

    const options = Array.from(listbox.querySelectorAll('[role="option"]'));
    if (!options.length) {
      console.warn("[job-agent] combobox: listbox empty", target);
      return;
    }

    // Filter out the intl-tel-input phone country list (244 options starting with country codes)
    const intlListbox = listbox.id && listbox.id.includes("iti-");
    if (intlListbox) {
      console.warn("[job-agent] combobox: only intl-tel-input listbox found, skipping", target);
      return;
    }

    const targetLower = target.toLowerCase().trim();
    let best = options.find((o) => (o.innerText || o.textContent || "").trim().toLowerCase() === targetLower);
    if (!best) best = options.find((o) => (o.innerText || o.textContent || "").trim().toLowerCase().startsWith(targetLower));
    if (!best) best = options.find((o) => (o.innerText || o.textContent || "").trim().toLowerCase().includes(targetLower));
    if (!best) {
      console.warn("[job-agent] combobox: no option matched", { target, count: options.length, samples: options.slice(0, 3).map(o => o.innerText) });
      return;
    }

    // Click via mousedown + mouseup + click — Greenhouse listens on mousedown
    const mouseDown = new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window });
    const mouseUp = new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window });
    const click = new MouseEvent("click", { bubbles: true, cancelable: true, view: window });
    best.dispatchEvent(mouseDown);
    best.dispatchEvent(mouseUp);
    best.dispatchEvent(click);

    fireEvent(input, "change");
    fireEvent(input, "blur");
    console.log("[job-agent] combobox selected:", { target, picked: (best.innerText || best.textContent || "").trim() });
  }

  window.__ATS_HANDLERS = window.__ATS_HANDLERS || {};
  window.__ATS_HANDLERS.greenhouse = Greenhouse;
})();