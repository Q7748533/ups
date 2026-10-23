// 在 node 里真跑页面里那段 Zone 查找 JS，把结果吐成 JSON。
// 由 tools/test_zone_parity.py 调用。
//
// 为什么要真跑而不是在 Python 里复刻：复刻版会随实现一起漂移，
// 测试和实现同时错就永远测不出来。这里执行的是即将上线的那段源码。

const fs = require("fs");

const htmlPath = process.argv[2];
const dataPath = process.argv[3];
const casesPath = process.argv[4];

const html = fs.readFileSync(htmlPath, "utf8");
const data = JSON.parse(fs.readFileSync(dataPath, "utf8"));
const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));

// 找出定义 ParcelZone 的那段 <script>
const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
const source = blocks.find((s) => s.includes("ParcelZone"));
if (!source) {
  console.error("FATAL: 页面里找不到 ParcelZone 脚本");
  process.exit(2);
}

// 最小 DOM 桩：find() 不用 DOM，highlight() 用，测试里给个空实现
const noopList = [];
const document = {
  querySelectorAll() {
    return noopList;
  },
};

// fetch 桩：直接返回已读好的 zones.json，不走网络
let fetchCalls = 0;
function fetch(url) {
  fetchCalls += 1;
  if (!String(url).includes("zones.json")) {
    return Promise.reject(new Error("unexpected url " + url));
  }
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(data),
  });
}

let ParcelZone;
try {
  // 在函数作用域里 eval，让 IIFE 挂到本地 window 上
  const window = {};
  const fn = new Function("document", "fetch", "window", source + "\nreturn window.ParcelZone;");
  ParcelZone = fn(document, fetch, window);
} catch (e) {
  console.error("FATAL: 脚本执行失败: " + e.message);
  process.exit(2);
}

if (!ParcelZone) {
  console.error("FATAL: window.ParcelZone 未定义");
  process.exit(2);
}

(async () => {
  await ParcelZone.load();

  // 第二次 load 不应重复请求
  await ParcelZone.load();
  const cacheOk = fetchCalls === 1;

  const results = cases.map((c) => {
    let zone = null;
    let err = null;
    let all = null;
    try {
      zone = ParcelZone.find(c.origin, c.dest, c.service || null);
      // all() 是 find() 的底层，页面靠它的 byService 给 APO/FPO 目的地
      // 逐个服务取 Zone。它漂了，计算器就会在没有任何提示的情况下报错价。
      const r = ParcelZone.all(c.origin, c.dest);
      all = r ? { base: r.base, byService: r.byService } : null;
    } catch (e) {
      err = String(e.message || e);
    }
    return { zone, all, err };
  });

  process.stdout.write(JSON.stringify({ results, cacheOk, fetchCalls }));
})().catch((e) => {
  console.error("FATAL: " + e.message);
  process.exit(2);
});
