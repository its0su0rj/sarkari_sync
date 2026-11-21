// Parity test: the JS engine must give exactly the Python results.
//   node tests/golden/check_golden.js tests/golden/golden.json
const fs = require("fs");
const path = require("path");
const E = require(path.join(__dirname, "..", "..", "site", "engine.js"));
const { jobs, reqs, cases } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
// Mode 1: the browser parses each job's rules. Mode 2: rules pre-parsed by Python (jobs.json "req").
const withReq = jobs.map((j, i) => Object.assign({}, j, { req: reqs[i] }));
let checks = 0, bad = 0;
for (const c of cases) for (const set of [jobs, withReq]) {
  const got = E.match(E.loadProfile(c.profile), set, c.today);
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
