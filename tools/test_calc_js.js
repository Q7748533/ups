// 在 node 里真跑计算器页面里的那段 JS，把结果吐成 JSON。
// 由 tools/test_calc_parity.py 调用，不接受手工输入。
//
// 为什么要真跑而不是在 Python 里复刻一遍取价逻辑：
// 复刻版会随实现一起漂移，测试和实现同时错就永远测不出来。
// 这里执行的是即将上线的那段源码。

const fs = require("fs");

const htmlPath = process.argv[2];
const casesJson = process.argv[3];

const html = fs.readFileSync(htmlPath, "utf8");
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) {
  console.error("FATAL: 页面里找不到 <script> 块");
  process.exit(2);
}
const source = m[1];

// 最小 DOM 桩：只提供计算器真正用到的东西
const els = {};
function makeEl(id) {
  return {
    id,
    value: "",
    innerHTML: "",
    addEventListener() {},
  };
}
const document = {
  getElementById(id) {
    if (!els[id]) els[id] = makeEl(id);
    return els[id];
  },
};

// 在函数作用域里 eval，让 var RATES / function calc 落在本作用域内
let calc, RATES;
try {
  const factory = new Function("document", source + "\nreturn {calc:calc, RATES:RATES};");
  const api = factory(document);
  calc = api.calc;
  RATES = api.RATES;
} catch (e) {
  console.error("FATAL: 执行页面 JS 失败: " + e.message);
  process.exit(2);
}

// 把 innerHTML 里的表格解析回 [[service, pricedAt, priceText]]
function parseRows(html) {
  const out = [];
  const re = /<tr><td>(.*?)<\/td><td>(.*?)<\/td><td[^>]*>(.*?)<\/td><\/tr>/g;
  let r;
  while ((r = re.exec(html)) !== null) {
    out.push([r[1], r[2], r[3]]);
  }
  return out;
}

const cases = JSON.parse(fs.readFileSync(casesJson, "utf8"));
const results = [];
for (const c of cases) {
  document.getElementById("w").value = String(c.w);
  document.getElementById("z").value = String(c.z);
  calc();
  const htmlOut = document.getElementById("out").innerHTML;
  const rows = parseRows(htmlOut);
  const cheapestMatch = htmlOut.match(/Cheapest published rate[^<]*<b>(.*?) at (\$[\d.]+)<\/b>/);
  results.push({
    w: c.w,
    z: c.z,
    rows,
    cheapest: cheapestMatch ? [cheapestMatch[1], cheapestMatch[2]] : null,
  });
}

process.stdout.write(JSON.stringify({ services: Object.keys(RATES), results }));
