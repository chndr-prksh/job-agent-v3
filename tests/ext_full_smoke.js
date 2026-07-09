const fs = require('fs');
const greenhouse = fs.readFileSync('extension/ats/greenhouse.js', 'utf-8');
const { JSDOM } = require('jsdom');

const dom = new JSDOM(`
<!DOCTYPE html><html><body>
<form id="application_form">
  <label for="first_name">First Name</label>
  <input id="first_name" name="first_name" type="text" required>
  <label for="last_name">Last Name</label>
  <input id="last_name" name="last_name" type="text" required>
  <label for="email">Email</label>
  <input id="email" name="email" type="email" required>
  <label for="phone">Phone</label>
  <input id="phone" name="phone" type="tel">
  <label for="resume">Resume/CV</label>
  <input id="resume" name="resume" type="file">
  <label for="linkedin">LinkedIn Profile</label>
  <input id="linkedin" name="linkedin" type="url">
  <label>Are you legally authorized to work in the country where this job is located?</label>
  <select name="work_auth">
    <option value="">--</option>
    <option value="yes">Yes</option>
    <option value="no">No</option>
  </select>
  <label>Do you now, or will you ever, require employment sponsorship?</label>
  <select name="sponsorship">
    <option value="">--</option>
    <option value="yes">Yes</option>
    <option value="no">No</option>
  </select>
</form>
</body></html>`);

const window = dom.window;
const fn = new Function('window', 'document', greenhouse + '; return window.__ATS_HANDLERS && window.__ATS_HANDLERS.greenhouse;');
const handler = fn(window, window.document);

const profile = {
  full_name: 'Jane Doe',
  email: 'jane@example.com',
  phone: '+1-555-0100',
  linkedin_url: 'https://linkedin.com/in/janedoe',
  work_authorization: { authorized_us: true, needs_sponsorship: false },
};

const result = handler.fill(profile);
const checks = [
  ['first_name', 'Jane'],
  ['last_name', 'Doe'],
  ['email', 'jane@example.com'],
  ['phone', '+1-555-0100'],
  ['linkedin', 'https://linkedin.com/in/janedoe'],
  ['work_auth', 'yes'],
  ['sponsorship', 'no'],
];

let pass = 0, fail = 0;
for (const [name, expected] of checks) {
  const el = window.document.querySelector(`[name="${name}"]`);
  const actual = el ? el.value : null;
  const ok = actual === expected;
  console.log(`  ${ok ? '✓' : '✗'} ${name}: ${actual || '(empty)'}${ok ? '' : `  expected ${expected}`}`);
  if (ok) pass++; else fail++;
}
console.log(`\n${pass}/${pass+fail} checks passed`);
console.log('Filled:', result.filled.length, 'Skipped:', result.skipped.length);
process.exit(fail === 0 ? 0 : 1);
