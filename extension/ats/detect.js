// ats/detect.js — runs on every supported ATS page, sets window.__ats
(function () {
  const h = location.hostname.toLowerCase();
  let ats = "unknown";
  if (h.includes("greenhouse.io")) ats = "greenhouse";
  else if (h.includes("lever.co")) ats = "lever";
  else if (h.includes("ashbyhq.com")) ats = "ashby";
  else if (h.includes("myworkdayjobs.com")) ats = "workday";
  else if (h.includes("icims.com")) ats = "icims";
  else if (h.includes("eightfold.ai")) ats = "eightfold";
  else if (h.includes("rippling.com")) ats = "rippling";

  window.__ats = ats;
  window.__jobUrl = location.href;
  console.log("[job-agent] ATS detected:", ats, location.href);
})();