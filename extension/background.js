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
    // Schema note: jobs column is apply_url (not url), resume_versions (not tailored_resumes)
    const jobRes = await fetch(`${url}/rest/v1/jobs?apply_url=eq.${encodeURIComponent(jobUrl)}&limit=1`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
    });
    const jobs = await jobRes.json();
    if (!jobs?.length) return null;
    const job = jobs[0];

    const trRes = await fetch(`${url}/rest/v1/resume_versions?job_id=eq.${job.id}&order=created_at.desc&limit=1`, {
      headers: { apikey: key, Authorization: `Bearer ${key}` },
    });
    const trs = await trRes.json();
    if (!trs?.length) return { file_path: null, file_name: null };
    const tr = trs[0];

    // Try multiple storage paths: file_url may be a local path, a Supabase Storage URL, or just a URL.
    let file_data_url = null;
    const candidate = tr.file_url;

    if (!candidate) {
      return { file_path: null, file_name: null };
    }

    if (candidate.startsWith("data:")) {
      file_data_url = candidate;
    } else if (candidate.startsWith("http://") || candidate.startsWith("https://")) {
      // Try public URL first (Supabase Storage public bucket). If 401/404, give up.
      try {
        const r = await fetch(candidate);
        if (r.ok) {
          const blob = await r.blob();
          file_data_url = await blobToDataUrl(blob);
        } else {
          console.warn("[job-agent-bg] fetchTailored: storage fetch failed", r.status, candidate);
        }
      } catch (e) {
        console.warn("[job-agent-bg] fetchTailored: storage fetch exception", e);
      }
    } else {
      // Local filesystem path — extension cannot read it (security boundary).
      // User must drag from Downloads.
      console.log("[job-agent-bg] fetchTailored: local path, user must drag", candidate);
    }

    const file_name = (candidate || "").split("/").pop() || "resume.pdf";

    return {
      file_path: candidate,
      file_name,
      file_data_url,
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