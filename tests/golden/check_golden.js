// Parity test: the JS engine must give exactly the Python results.
//   node tests/golden/check_golden.js tests/golden/golden.json
const fs = require("fs");
const path = require("path");
const E = require(path.join(__dirname, "..", "..", "site", "engine.js"));
const { jobs, cases } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
let checks = 0, bad = 0;
for (const c of cases) {
  const got = E.match(E.loadProfile(c.profile), jobs, c.today);
  for (let i = 0; i < c.expected.length; i++) {
    checks++;
    const a = JSON.stringify(c.expected[i]), b = JSON.stringify(got[i]);
    if (a !== b) {
      if (bad < 8) console.log(`MISMATCH (today ${c.today})\n  profile ${JSON.stringify(c.profile)}\n  python  ${a}\n  js      ${b}\n`);
      bad++;
    }
  }
}
console.log(`${checks} checks, ${bad} mismatches`);
process.exit(bad ? 1 : 0);
