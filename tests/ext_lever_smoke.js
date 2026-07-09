const fs = require('fs');
const lever = fs.readFileSync('extension/ats/lever.js', 'utf-8');
const { JSDOM } = require('jsdom');

const dom = new JSDOM(`
<!DOCTYPE html><html><body>
<form class="application-form" data-qa="apply-form">
  <input type="file" name="resume">
  <input type="text" name="name" placeholder="Full name">
  <input type="email" name="email" placeholder="Email">
  <input type="text" name="phone" placeholder="Phone">
  <input type="text" name="location" placeholder="Location">
  <input type="text" name="org" placeholder="Current company">
  <input type="text" name="urls[LinkedIn]" placeholder="LinkedIn URL">
  <input type="text" name="urls[GitHub]" placeholder="GitHub URL">
  <input type="text" name="urls[Other]" placeholder="Other URL">
  <!-- EEO checkboxes that we deliberately skip -->
  <input type="checkbox" name="pronouns" value="he/him">
  <input type="checkbox" name="pronouns" value="she/her">
  <input type="checkbox" name="eeo[gender]" value="male">
</form>
</body></html>`);

const window = dom.window;
const fn = new Function('window', 'document', lever + '; return window.__ATS_HANDLERS.lever;');
const handler = fn(window, dom.window.document);

const profile = {
  full_name: 'Jane Doe',
  email: 'jane@example.com',
  phone: '+1-555-0100',
  linkedin_url: 'https://linkedin.com/in/janedoe',
  github_url: 'https://github.com/janedoe',
  work_history: [{ company: 'Stripe', title: 'Senior PM', dates: '2022-present', bullets: [] }],
};

const result = handler.fill(profile);
const checks = [
  ['name', 'Jane Doe'],
  ['email', 'jane@example.com'],
  ['phone', '+1-555-0100'],
  ['location', '?'],  // we don't have a 'location' field in our test profile, will be empty
  ['org', 'Stripe'],
  ['urls[LinkedIn]', 'https://linkedin.com/in/janedoe'],
  ['urls[GitHub]', 'https://github.com/janedoe'],
];

let pass = 0, fail = 0;
for (const [name, expected] of checks) {
  const el = window.document.querySelector(`[name="${name}"]`);
  if (!el) { console.log(`  ✗ ${name}: missing`); fail++; continue; }
  const actual = el.value || '(empty)';
  const ok = expected === '?' ? true : actual === expected;
  console.log(`  ${ok ? '✓' : '✗'} ${name}: ${actual}${ok ? '' : `  expected ${expected}`}`);
  if (ok) pass++; else fail++;
}

// Verify pronouns checkboxes were NOT filled
const pronouns = window.document.querySelectorAll('[name="pronouns"]');
const anyChecked = Array.from(pronouns).some(c => c.checked);
console.log(`  ${!anyChecked ? '✓' : '✗'} pronouns checkboxes untouched: ${anyChecked ? 'SOME CHECKED (BAD)' : 'all unchecked'}`);
if (!anyChecked) pass++; else fail++;

console.log(`\n${pass}/${pass+fail} checks passed`);
console.log(`filled=${result.filled.length} skipped=${result.skipped.length}`);
process.exit(fail === 0 ? 0 : 1);
