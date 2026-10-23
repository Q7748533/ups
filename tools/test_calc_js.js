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

// 页面里有多个 <script> 块：Zone 查询块、计算器块，还有 JSON-LD。
// 不能只取第一个（那会拿到 Zone 块，里面没有 calc）。按内容挑。
const blocks = [...html.matchAll(/<script(\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .filter((b) => !/application\/ld\+json/.test(b[1] || ""))
  .map((b) => b[2]);

const source = blocks.find((s) => /function\s+calc\s*\(/.test(s));
if (!source) {
  console.error("FATAL: 页面里找不到定义 calc() 的 <script> 块");
  process.exit(2);
}

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
// 计算器页的脚本块里同时挂着 window.ParcelZone（给起止 ZIP 输入框用）。
// 浏览器里 window 天然存在，这里必须补一个，否则整块脚本一执行就报
// "window is not defined" —— 那是桩缺件，不是页面 bug。
const window = {};
const fetch = () => Promise.reject(new Error("测试桩不联网"));

// 在函数作用域里 eval，让 var RATES / function calc 落在本作用域内
let calc, RATES;
try {
  const factory = new Function(
    "document", "window", "fetch",
    source + "\nreturn {calc:calc, RATES:RATES};"
  );
  const api = factory(document, window, fetch);
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
