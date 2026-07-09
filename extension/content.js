// content.js — runs on every supported ATS page, injects autofill UI

(function () {
  "use strict";

  // Load the matching ATS handler script synchronously via the manifest order.
  // manifest.json already lists content scripts in this order:
  //   ["ats/detect.js", "content.js"]
  // To register handlers, we append the matching one before running.
  const ATS = window.__ats || "unknown";

  // Handlers are pre-loaded by content_scripts via the manifest's "js" array.
  // content.js is the LAST script in the manifest, so all ats/*.js must be listed BEFORE it.
  // (See manifest.json — ats/greenhouse.js etc are loaded automatically when added to "js".)
  let busy = false;
  let pausedReason = null;

  // Create the floating action button + overlay
  function injectUI() {
    if (document.getElementById("job-agent-fab")) return;

    const fab = document.createElement("div");
    fab.id = "job-agent-fab";
    fab.innerHTML = `
      <button id="job-agent-fab-btn" title="job-agent: fill this form">🤖</button>
    `;
    document.body.appendChild(fab);

    fab.querySelector("#job-agent-fab-btn").addEventListener("click", async () => {
      if (busy) return;
      await runPipeline();
    });
  }

  function showBanner(text, kind = "info") {
    let banner = document.getElementById("job-agent-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "job-agent-banner";
      document.body.appendChild(banner);
    }
    banner.className = `job-agent-banner-${kind}`;
    banner.innerHTML = `<span>${text}</span><button id="job-agent-banner-x">×</button>`;
    banner.style.display = "flex";
    banner.querySelector("#job-agent-banner-x").onclick = () => {
      banner.style.display = "none";
    };
  }

  function hideBanner() {
    const b = document.getElementById("job-agent-banner");
    if (b) b.style.display = "none";
  }

  async function getProfile() {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "GET_PROFILE" }, (resp) => {
        resolve(resp?.profile || null);
      });
    });
  }

  async function getTailoredResume(jobUrl) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "GET_TAILORED_RESUME", jobUrl }, (resp) => {
        resolve(resp?.tailored || null);
      });
    });
  }

  function getHandler() {
    return window.__ATS_HANDLERS?.[ATS];
  }

  async function runPipeline() {
    if (busy) return;
    busy = true;
    try {
      showBanner(`job-agent: ATS=${ATS}, fetching profile...`);
      const profile = await getProfile();
      if (!profile) {
        showBanner("job-agent: no profile loaded. Run daemon once and fill candidate_profile.", "error");
        return;
      }

      const handler = getHandler();
      if (!handler) {
        showBanner(`job-agent: ATS "${ATS}" not yet supported. Drop URL in Telegram.`, "warn");
        return;
      }

      // CAPTCHA check first
      if (handler.isCaptchaPage && handler.isCaptchaPage()) {
        showBanner("job-agent: CAPTCHA detected. Solve it in this tab, then click 🤖 again.", "warn");
        return;
      }

      // Apply page check
      if (handler.isApplyPage && !handler.isApplyPage()) {
        showBanner("job-agent: doesn't look like an apply page. Navigate to the actual application.", "warn");
        return;
      }

      // Fill
      showBanner("job-agent: filling fields...");
      const result = handler.fill(profile);
      console.log("[job-agent] fill result:", result);

      // Attach resume
      showBanner("job-agent: attaching tailored resume...");
      const tailored = await getTailoredResume(location.href);
      if (tailored?.file_data_url && handler.attachResume) {
        const attachResult = await handler.attachResume(tailored.file_data_url, tailored.file_name);
        if (attachResult.ok) {
          showBanner(`job-agent: attached ${attachResult.attached}`, "ok");
        } else {
          showBanner(`job-agent: resume attach failed: ${attachResult.error}. Drag from Downloads.`, "warn");
        }
      } else {
        showBanner("job-agent: no tailored resume found in Supabase for this URL. Tailor via Telegram first.", "warn");
      }

      // Check Submit
      if (handler.hasSubmitButton && handler.hasSubmitButton()) {
        showBanner(`
          ✅ job-agent: filled and resume attached.<br>
          <b>Click Submit when ready.</b> I'll detect the confirmation email automatically.
        `, "ok");
      } else if (handler.hasNextButton && handler.hasNextButton()) {
        showBanner("job-agent: filled. Clicking Next for multi-step form...", "info");
        setTimeout(() => {
          handler.clickNext();
          // After Next, the new step will retrigger our content script via mutation
        }, 800);
      } else {
        showBanner("job-agent: filled. No Next/Submit found — review and submit manually.", "info");
      }
    } catch (e) {
      console.error("[job-agent] pipeline error", e);
      showBanner(`job-agent: error: ${e.message}`, "error");
    } finally {
      busy = false;
    }
  }

  // Watch for URL changes (SPAs navigate without full reload)
  let lastUrl = location.href;
  function watchUrl() {
    const obs = new MutationObserver(() => {
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        setTimeout(injectUI, 500);
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  // Init
  setTimeout(() => {
    injectUI();
    watchUrl();
  }, 800);
})();