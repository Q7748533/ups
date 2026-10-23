// 真浏览器里验证 Zone 交互。桩测只能证明逻辑对，证明不了「用户点下去会怎样」。
//
// 覆盖：
//   1. 计算器：填起止 ZIP -> Zone 下拉框被改写 -> 价格表跟着重算
//   2. 计算器：无效 ZIP / 未分配 ZIP3 -> 给出提示且不静默改 Zone
//   3. 费率页：填 ZIP -> 对应 Zone 列高亮，其它列取消
//   4. 费率页：同一页二次查询，旧高亮必须清掉（不能越查越多）
//   5. 移动端 390px：无横向溢出
//   6. 全程无 console error / 无未捕获异常

const { chromium } = require("playwright");

const BASE = process.env.BASE || "http://127.0.0.1:8899";

const fails = [];
const notes = [];
let checks = 0;

function ok(cond, msg) {
  checks++;
  if (!cond) fails.push(msg);
}

async function newPage(browser, width, height) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const errs = [];
  page.on("console", (m) => {
    if (m.type() === "error") errs.push(m.text());
  });
  page.on("pageerror", (e) => errs.push("pageerror: " + e.message));
  return { ctx, page, errs };
}

async function zoneFinder(page, from, to) {
  await page.fill("#zf-from", from);
  await page.fill("#zf-to", to);
  await page.click("#zf-go");
  // 费率页会先显示 "Looking up…"，那是加载态不是结论。只等非空会误判成结果。
  await page.waitForFunction(
    () => {
      const el = document.getElementById("zf-out");
      const t = el ? el.textContent.trim() : "";
      return t.length > 0 && !/looking up/i.test(t);
    },
    { timeout: 8000 }
  );
  return (await page.textContent("#zf-out")).trim();
}

(async () => {
  // 用系统 Chrome。playwright 自带的 chromium 版本号与本地缓存未必对得上，
  // 为一次布局检查再下一套浏览器不值得。
  let browser;
  try {
    browser = await chromium.launch({ channel: "chrome" });
  } catch (e) {
    browser = await chromium.launch();
  }

  // ---------- 1 & 2. 计算器 ----------
  {
    const { ctx, page, errs } = await newPage(browser, 1280, 900);
    await page.goto(BASE + "/calculator/", { waitUntil: "networkidle" });

    // 默认 2 lb / Zone 5
    let price = await page.textContent("#out tbody tr:nth-child(1) td:nth-child(3)");
    ok(price.trim() === "$14.85", `计算器默认 GA 2lb z5 应为 $14.85，实际 ${price.trim()}`);

    // NY -> Chicago = Zone 5，价格不该变（默认就是 5）
    let msg = await zoneFinder(page, "10001", "60601");
    ok(/zone\s*5/i.test(msg), `10001->60601 应提示 Zone 5，实际 "${msg}"`);
    ok((await page.inputValue("#z")) === "5", `下拉框应为 5，实际 ${await page.inputValue("#z")}`);

    // NY -> LA = Zone 8（官方 GetZone 实测值；换 Zone 价格必须跟着变）
    msg = await zoneFinder(page, "10001", "90001");
    ok(/zone\s*8/i.test(msg), `10001->90001 应提示 Zone 8，实际 "${msg}"`);
    ok((await page.inputValue("#z")) === "8", `下拉框应为 8，实际 ${await page.inputValue("#z")}`);
    price = await page.textContent("#out tbody tr:nth-child(1) td:nth-child(3)");
    ok(price.trim() === "$19.80", `Zone 8 时 GA 2lb 应为 $19.80，实际 ${price.trim()}`);

    // 本地 = Zone 1
    msg = await zoneFinder(page, "60601", "60601");
    ok(/zone\s*1/i.test(msg), `60601->60601 应提示 Zone 1（本地），实际 "${msg}"`);
    ok((await page.inputValue("#z")) === "1", `下拉框应为 1，实际 ${await page.inputValue("#z")}`);

    // 无效输入不能静默改 Zone
    const before = await page.inputValue("#z");
    msg = await zoneFinder(page, "abc", "60601");
    ok(/zip/i.test(msg), `无效 ZIP 应给出提示，实际 "${msg}"`);
    ok((await page.inputValue("#z")) === before, `无效输入不应改动 Zone（${before} -> ${await page.inputValue("#z")}）`);

    // 未分配的 ZIP3（如 000xx 段）
    msg = await zoneFinder(page, "10001", "00001");
    ok(msg.length > 0, "未分配 ZIP3 应给出提示");

    // ---------- APO/FPO：官方不是给一个 Zone，而是逐服务不同 ----------
    // 10001 -> 09012 官方原话："The Zone is 1, except for Priority Mail
    // service where the Zone is 5, and USPS Ground Advantage (< 16 oz.)
    // where the Zone is 5"。只设一个 Zone 会把这两项算错，且不报错。
    await page.fill("#w", "2");
    msg = await zoneFinder(page, "10001", "09012");
    ok(/zone\s*1/i.test(msg), `APO 基础 Zone 应为 1，实际 "${msg}"`);
    ok(/apo|fpo/i.test(msg), `APO 目的地应明确说明，实际 "${msg}"`);
    ok((await page.inputValue("#z")) === "1", `下拉框应为 1，实际 ${await page.inputValue("#z")}`);

    const apoRows = await page.evaluate(() =>
      [...document.querySelectorAll("#out tbody tr")].map((tr) =>
        [...tr.children].map((td) => td.textContent.trim())
      )
    );
    const rowOf = (n) => apoRows.find((r) => r[0] === n) || [];
    const gaRow = rowOf("Ground Advantage");
    const pmRow = rowOf("Priority Mail");
    ok(/Zone\s*5/.test(gaRow[1] || ""), `APO 下 GA 应按 Zone 5 计价，实际 "${gaRow[1]}"`);
    ok(/Zone\s*5/.test(pmRow[1] || ""), `APO 下 Priority Mail 应按 Zone 5 计价，实际 "${pmRow[1]}"`);
    // GA 2lb：Zone 1 = $11.30，Zone 5 = $14.85。取错 Zone 就是这一格的差
    ok((gaRow[2] || "") === "$14.85", `APO 下 GA 2lb 应为 $14.85，实际 ${gaRow[2]}`);
    // 例外里没有 Media Mail，它仍按基础 Zone（而且本来就不分 Zone）
    ok(!/Zone\s*\d/.test(rowOf("Media Mail")[1] || ""),
      `Media Mail 不该标 Zone，实际 "${rowOf("Media Mail")[1]}"`);
    const apoNote = await page.textContent("#out");
    ok(/apo\/fpo/i.test(apoNote), "结果区应有 APO/FPO 说明");

    // 手动改 Zone 后，上一次目的地的服务级例外必须被清掉，
    // 否则页面上会留着别的地址算出来的价。
    await page.selectOption("#z", "3");
    const afterManual = await page.evaluate(() =>
      [...document.querySelectorAll("#out tbody tr")].map((tr) =>
        [...tr.children].map((td) => td.textContent.trim())
      )
    );
    const gaAfter = afterManual.find((r) => r[0] === "Ground Advantage") || [];
    ok(!/Zone\s*\d/.test(gaAfter[1] || ""),
      `手动改 Zone 后不该再带服务级 Zone 标记，实际 "${gaAfter[1]}"`);
    ok(!/apo\/fpo/i.test(await page.textContent("#out")),
      "手动改 Zone 后 APO 说明应消失");

    // 加载 zones.json 不能报错
    ok(errs.length === 0, `计算器页 console 错误: ${errs.slice(0, 3).join(" | ")}`);
    await ctx.close();
  }

  // ---------- 3 & 4. 费率页高亮 ----------
  {
    const { ctx, page, errs } = await newPage(browser, 1280, 1000);
    await page.goto(BASE + "/rates/ground-advantage/", { waitUntil: "networkidle" });

    const hlCount = () =>
      page.evaluate(() => document.querySelectorAll(".zone-hl").length);

    ok((await hlCount()) === 0, "初始不应有任何高亮");

    let msg = await zoneFinder(page, "10001", "60601");
    ok(/zone\s*5/i.test(msg), `费率页 10001->60601 应提示 Zone 5，实际 "${msg}"`);
    let n5 = await hlCount();
    ok(n5 > 0, "Zone 5 应有高亮单元格");
    const cols5 = await page.evaluate(() =>
      [...new Set([...document.querySelectorAll("th.zone-hl")].map((t) => t.textContent.trim()))]
    );
    ok(cols5.length === 1 && /Zone\s*5/i.test(cols5[0]),
      `高亮表头应只有 Zone 5，实际 ${JSON.stringify(cols5)}`);

    // 二次查询必须把旧的清掉
    msg = await zoneFinder(page, "10001", "90001");
    ok(/zone\s*8/i.test(msg), `费率页 10001->90001 应提示 Zone 8，实际 "${msg}"`);
    const cols7 = await page.evaluate(() =>
      [...new Set([...document.querySelectorAll("th.zone-hl")].map((t) => t.textContent.trim()))]
    );
    ok(cols7.length === 1 && /Zone\s*8/i.test(cols7[0]),
      `二次查询后应只剩 Zone 8，实际 ${JSON.stringify(cols7)}`);
    ok((await hlCount()) === n5, `高亮格数应稳定（${n5}），实际 ${await hlCount()}`);

    // 表格不能因为高亮产生横向溢出（桌面端）
    const over = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    );
    ok(over <= 0, `桌面端出现横向溢出 ${over}px`);

    ok(errs.length === 0, `费率页 console 错误: ${errs.slice(0, 3).join(" | ")}`);
    await ctx.close();
  }

  // ---------- 4b. 单服务费率页 + APO：要高亮「该服务」的 Zone ----------
  {
    const { ctx, page, errs } = await newPage(browser, 1280, 1000);
    await page.goto(BASE + "/rates/priority-mail/", { waitUntil: "networkidle" });
    const msg = await zoneFinder(page, "10001", "09012");
    // 这一页整张表都是 Priority Mail 的价，APO 下它自己的 Zone 是 5，不是基础的 1。
    // 高亮 1 会让读者按错的一列报价，而且看起来完全正常。
    ok(/zone\s*5/i.test(msg), `priority-mail 页 APO 应取该服务的 Zone 5，实际 "${msg}"`);
    const cols = await page.evaluate(() =>
      [...new Set([...document.querySelectorAll("th.zone-hl")].map((t) => t.textContent.trim()))]
    );
    ok(cols.length === 1 && /Zone\s*5/i.test(cols[0]),
      `应高亮 Zone 5 那一列，实际 ${JSON.stringify(cols)}`);
    ok(/apo|fpo/i.test(msg), `应说明这是 APO/FPO 地址，实际 "${msg}"`);
    ok(errs.length === 0, `priority-mail 页 console 错误: ${errs.slice(0, 3).join(" | ")}`);
    await ctx.close();
  }

  // ---------- 4c. 对比页 + APO：一张表两个服务，列高亮给不出两列，必须说明 ----------
  {
    const { ctx, page, errs } = await newPage(browser, 1280, 1000);
    await page.goto(BASE + "/compare/priority-mail-vs-ground-advantage/",
      { waitUntil: "networkidle" });
    const msg = await zoneFinder(page, "10001", "09012");
    // 这一页同时讲 Priority Mail 和 Ground Advantage，两者在 APO 下都是 Zone 5，
    // 但基础值是 1。不能悄悄只高亮 1 就完事，必须把「这地址不是单一 Zone」说出来。
    ok(/apo|fpo/i.test(msg), `对比页应说明 APO/FPO，实际 "${msg}"`);
    ok(/priority mail|ground advantage/i.test(msg),
      `应点名哪些服务用了别的 Zone，实际 "${msg}"`);
    ok(errs.length === 0, `对比页 console 错误: ${errs.slice(0, 3).join(" | ")}`);
    await ctx.close();
  }

  // ---------- 5. 移动端 ----------
  {
    const pages = [
      "/calculator/",
      "/rates/ground-advantage/",
      "/rates/priority-mail/",
      "/rates/priority-mail-express/",
      "/compare/flat-rate-vs-regular/",
      "/compare/priority-mail-vs-ground-advantage/",
      "/compare/retail-vs-commercial/",
      "/compare/media-mail-vs-ground-advantage/",
      "/compare/priority-mail-vs-media-mail/",
    ];
    for (const p of pages) {
      const { ctx, page, errs } = await newPage(browser, 390, 844);
      await page.goto(BASE + p, { waitUntil: "networkidle" });
      const over = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth
      );
      ok(over <= 0, `${p} 移动端横向溢出 ${over}px`);
      ok(errs.length === 0, `${p} 移动端 console 错误: ${errs.slice(0, 2).join(" | ")}`);
      await ctx.close();
    }
  }

  // ---------- 6. zones.json 真的能被浏览器取到 ----------
  {
    const { ctx, page, errs } = await newPage(browser, 1280, 900);
    const rsp = await page.goto(BASE + "/zones.json");
    ok(rsp.status() === 200, `zones.json 状态码 ${rsp.status()}`);
    const j = JSON.parse(await rsp.text());
    ok(Object.keys(j.origins || {}).length > 800,
      `zones.json 起点数异常: ${Object.keys(j.origins || {}).length}`);
    ok((j.origins["100"][606] || "").length === 1, "100 行第 606 位应有值");
    ok(errs.length === 0, `zones.json console 错误: ${errs.join(" | ")}`);
    await ctx.close();
  }

  await browser.close();

  console.log(`checks: ${checks}`);
  for (const n of notes) console.log("  ~ " + n);
  if (fails.length) {
    console.log(`FAIL (${fails.length}):`);
    fails.forEach((f) => console.log("  - " + f));
    process.exit(1);
  }
  console.log("problems: 0");
})().catch((e) => {
  console.error("FATAL: " + e.stack);
  process.exit(2);
});
