const fs = require('fs');
const greenhouse = fs.readFileSync('extension/ats/greenhouse.js', 'utf-8');
const { JSDOM } = require('jsdom');

const dom = new JSDOM(`
<!DOCTYPE html><html><body>
<form id="application_form">
  <label for="first_name">First Name</label>
  <input id="first_name" name="first_name" type="text" required>
  <label for="email">Email</label>
  <input id="email" name="email" type="email" required>
</form>
</body></html>`);

const window = dom.window;
const fn = new Function('window', 'document', greenhouse + '; return window.__ATS_HANDLERS && window.__ATS_HANDLERS.greenhouse;');
const handler = fn(window, window.document);

console.log('isApplyPage:', handler.isApplyPage());
console.log('fields:');
const fields = handler.fields();
for (const f of fields) {
  console.log(`  name=${f.name} label="${f.label}" type=${f.type}`);
}

const result = handler.fill({ first_name: 'Jane', email: 'jane@example.com' });
console.log('fill result:', JSON.stringify(result, null, 2));
