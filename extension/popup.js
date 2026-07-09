// popup.js — settings UI for the extension

document.addEventListener("DOMContentLoaded", async () => {
  const { supabase_url, supabase_key } = await chrome.storage.local.get(["supabase_url", "supabase_key"]);
  document.getElementById("supabase-url").value = supabase_url || "";
  document.getElementById("supabase-key").value = supabase_key || "";

  document.getElementById("save").addEventListener("click", async () => {
    const url = document.getElementById("supabase-url").value.trim();
    const key = document.getElementById("supabase-key").value.trim();
    await chrome.storage.local.set({ supabase_url: url, supabase_key: key });
    const status = document.getElementById("status");
    status.style.display = "block";
    status.className = "status ok";
    status.textContent = "Saved. Visit any ATS apply page and click the 🤖 button.";
  });
});