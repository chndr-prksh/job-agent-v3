// background.js — service worker. Holds Supabase creds (in chrome.storage)
// and proxies profile/tailored-resume fetches from content scripts.

const SUPABASE_URL_KEY = "supabase_url";
const SUPABASE_KEY_KEY = "supabase_key";
// Storage keys must match what popup.js writes. Extension never embeds the URL.

chrome.runtime.onInstalled.addListener(() => {
  console.log("[job-agent-bg] installed");
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GET_PROFILE") {
    fetchProfile().then((profile) => sendResponse({ profile }));
    return true;
  }
  if (msg.type === "GET_TAILORED_RESUME") {
    fetchTailored(msg.jobUrl).then((tailored) => sendResponse({ tailored }));
    return true;
  }
});

async function fetchProfile() {
  const { [SUPABASE_URL_KEY]: url, [SUPABASE_KEY_KEY]: key } = await chrome.storage.local.get([
    SUPABASE_URL_KEY, SUPABASE_KEY_KEY,
  ]);
  if (!url || !key) return null;
  try {
    const r = await fetch(`${url}/rest/v1/candidate_profile?limit=1`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
    });
    if (!r.ok) return null;
    const data = await r.json();
    return data?.[0] || null;
  } catch (e) {
    console.warn("[job-agent-bg] fetchProfile error", e);
    return null;
  }
}

async function fetchTailored(jobUrl) {
  const { [SUPABASE_URL_KEY]: url, [SUPABASE_KEY_KEY]: key } = await chrome.storage.local.get([
    SUPABASE_URL_KEY, SUPABASE_KEY_KEY,
  ]);
  if (!url || !key) return null;
  try {
    // Look up job by URL
    const jobRes = await fetch(`${url}/rest/v1/jobs?url=eq.${encodeURIComponent(jobUrl)}&limit=1`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
    });
    const jobs = await jobRes.json();
    if (!jobs?.length) return null;
    const job = jobs[0];

    // Look up tailored resume
    const trRes = await fetch(`${url}/rest/v1/tailored_resumes?job_id=eq.${job.id}&limit=1`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
    });
    const trs = await trRes.json();
    if (!trs?.length) return { file_path: null, file_name: null };
    const tr = trs[0];

    // If storage_path exists, fetch the actual file as data URL
    let file_data_url = null;
    if (tr.storage_path) {
      const fileRes = await fetch(`${url}/storage/v1/object/tailored/${tr.storage_path}`, {
        headers: { apikey: key, Authorization: `Bearer ${key}` },
      });
      if (fileRes.ok) {
        const blob = await fileRes.blob();
        file_data_url = await blobToDataUrl(blob);
      }
    }

    return {
      file_path: tr.file_path,
      file_name: tr.file_name,
      file_data_url,
      storage_path: tr.storage_path,
    };
  } catch (e) {
    console.warn("[job-agent-bg] fetchTailored error", e);
    return null;
  }
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}