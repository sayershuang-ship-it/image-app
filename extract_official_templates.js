// extract_official_templates.js
// One-time extraction tool: reads the `const TEMPLATES = {...}` object literal
// out of templates/templates.html and dumps it as official_templates.json,
// shaped as a flat array of {tab, subcategory, title, prompt}.
//
// Run: node extract_official_templates.js

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'templates', 'templates.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const startMarker = 'const TEMPLATES = ';
const startIdx = html.indexOf(startMarker);
if (startIdx === -1) {
  throw new Error('Could not find "const TEMPLATES = " in templates/templates.html');
}
const afterStart = startIdx + startMarker.length;
const endIdx = html.indexOf('\n};\n', afterStart);
if (endIdx === -1) {
  throw new Error('Could not find end of TEMPLATES object literal');
}
const objectLiteral = html.slice(afterStart, endIdx + 2); // include closing "}"

// eslint-disable-next-line no-eval
const TEMPLATES = eval('(' + objectLiteral + ')');

const flat = [];
for (const [tab, subcats] of Object.entries(TEMPLATES)) {
  for (const [subcategory, items] of Object.entries(subcats)) {
    for (const item of items) {
      flat.push({ tab, subcategory, title: item.name, prompt: item.prompt });
    }
  }
}

fs.writeFileSync(
  path.join(__dirname, 'official_templates.json'),
  JSON.stringify(flat, null, 2) + '\n'
);

console.log(`Wrote ${flat.length} templates to official_templates.json`);
