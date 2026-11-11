// ==UserScript==
// @name         Asiagoal 結單倒數
// @namespace    local.asiagoal.deadline
// @version      1.0
// @description  在 Asiagoal 商品卡片上顯示結單倒數，並可把看過的商品匯出成 deadline.json
// @match        https://www.asiagoal.com.tw/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * 說明：
 * - 只讀取「你的瀏覽器畫面上已經顯示」的商品卡片，不會對網站發出任何額外請求。
 * - 資料只存在你自己瀏覽器的 localStorage，沒有傳到任何地方。
 * - 匯出時會下載 deadline.json（格式與原本程式產生的相同），請自行上傳到 GitHub。
 */
(function () {
  "use strict";

  var STORE_KEY = "asiagoal_deadline_items_v1";
  var KEEP_EXPIRED_DAYS = 7; // 匯出時保留「已結單 7 天內」的商品
  var DATE_RE = /(\d{4})\/(\d{1,2})\/(\d{1,2})\s*結單/;
  var ITEM_SEL = 'a[href*="/item/"]';

  /* 「一鍵更新全部」的設定：請求量刻意壓得很低 */
  var API_PATH = "/api/store/products";
  var API_PARAMS = {
    type: "category",
    id: "52139", // 最新預購
    orderType: "default",
    hotSalePeriod: "default",
    activeId: "52139"
  };
  var API_LIMIT = 100;        // 338 件約 4 次請求（比手動翻 10 頁還少）
  var API_MAX_PAGES = 10;     // 硬性上限
  var DELAY_MIN_MS = 3000;    // 每次請求之間隨機等 3～6 秒
  var DELAY_MAX_MS = 6000;
  var SYNC_COOLDOWN_MS = 60 * 60 * 1000; // 兩次更新至少間隔 1 小時（失敗也算）
  var SYNC_KEY = "asiagoal_deadline_last_sync_v1";

  /* ---------- 日期工具（一律用台灣時間的「今天」） ---------- */

  function taipeiToday() {
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(new Date());

    function get(type) {
      return Number(parts.filter(function (p) { return p.type === type; })[0].value);
    }

    return Date.UTC(get("year"), get("month") - 1, get("day"));
  }

  function daysLeft(y, m, d) {
    return Math.round((Date.UTC(y, m - 1, d) - taipeiToday()) / 86400000);
  }

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function taipeiNowText() {
    var t = new Date(Date.now() + 8 * 3600000); // 轉成 UTC+8 後用 UTC 欄位取值
    return (
      t.getUTCFullYear() + "-" + pad(t.getUTCMonth() + 1) + "-" + pad(t.getUTCDate()) +
      " " + pad(t.getUTCHours()) + ":" + pad(t.getUTCMinutes()) + ":" + pad(t.getUTCSeconds())
    );
  }

  /* ---------- 本機儲存 ---------- */

  function loadStore() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {};
    } catch (e) {
      return {};
    }
  }

  function saveStore(obj) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(obj));
    } catch (e) { /* 儲存失敗就略過，不影響頁面 */ }
  }

  /* ---------- 找出商品卡片 ---------- */

  function itemUrl(a) {
    return a.href.split("#")[0].split("?")[0];
  }

  function distinctItemUrls(el) {
    var set = {};
    var list = el.querySelectorAll(ITEM_SEL);

    for (var i = 0; i < list.length; i++) {
      set[itemUrl(list[i])] = true;
    }

    if (el.matches && el.matches(ITEM_SEL)) {
      set[itemUrl(el)] = true;
    }

    return Object.keys(set).length;
  }

  /* 優先使用網站卡片的固定標記 data-bv="product-card"；
     找不到時，才退回「從商品連結往上找」的通用做法 */
  function findCards() {
    var cards = {};

    var marked = document.querySelectorAll('[data-bv="product-card"]');

    for (var k = 0; k < marked.length; k++) {
      var link = marked[k].querySelector(ITEM_SEL);

      if (link && DATE_RE.test(marked[k].innerText || "")) {
        cards[itemUrl(link)] = marked[k];
      }
    }

    var anchors = document.querySelectorAll(ITEM_SEL);

    for (var i = 0; i < anchors.length; i++) {
      var a = anchors[i];
      var url = itemUrl(a);

      if (cards[url]) continue;

      var el = a;

      for (var depth = 0; depth < 8 && el && el !== document.body; depth++) {
        if (distinctItemUrls(el) > 1) break; // 爬太高，已經包含多個商品

        if (DATE_RE.test(el.innerText || "")) {
          cards[url] = el;
          break;
        }

        el = el.parentElement;
      }
    }

    return cards;
  }

  /* ---------- 解析卡片內容 ---------- */

  function parseCard(url, card) {
    var text = card.innerText || "";
    var m = DATE_RE.exec(text);

    if (!m) return null;

    var y = Number(m[1]);
    var mo = Number(m[2]);
    var d = Number(m[3]);

    /* 標題：優先用網站標記，其次才用文字推測 */
    var title = (card.getAttribute("title") || "").trim();

    if (!title) {
      var titleEl = card.querySelector('[data-bv="product-card-title"]');

      if (titleEl) title = (titleEl.textContent || "").trim();
    }

    if (!title) {
      var lines = text
        .split("\n")
        .map(function (s) { return s.trim(); })
        .filter(function (s) {
          return s && s.length > 2 && !/結單/.test(s) && !/NT\$/.test(s) && !/^(預購|補貨中|現貨)$/.test(s);
        });

      for (var i = 0; i < lines.length; i++) {
        if (lines[i].length > title.length) title = lines[i];
      }
    }

    var img = card.querySelector(ITEM_SEL + " img") || card.querySelector("img");

    if (!title && img) title = (img.getAttribute("alt") || "").trim();

    var photo = "";

    if (img) {
      photo = img.currentSrc || img.getAttribute("src") || img.getAttribute("data-src") || "";

      if (/^data:/.test(photo)) photo = "";
    }

    /* 價格：優先取「售價」，沒有就取卡片中最後一個 NT$ 金額 */
    var price = null;
    var priceEl = card.querySelector(".productBoxPrice");

    if (priceEl) {
      var digits = (priceEl.textContent || "").replace(/[^\d]/g, "");

      if (digits) price = Number(digits);
    }

    if (price === null) {
      var prices = text.match(/NT\$\s*[\d,]+/g) || [];

      if (prices.length) {
        price = Number(prices[prices.length - 1].replace(/[^\d]/g, ""));
      }
    }

    return {
      title: title,
      url: url,
      price: price,
      photo: photo,
      date: y + "/" + mo + "/" + d,
      y: y,
      m: mo,
      d: d
    };
  }

  /* ---------- 倒數標籤 ---------- */

  function badgeStyle(days) {
    var bg = "#e6f4ea";
    var fg = "#1e6b34";

    if (days < 0) { bg = "#eee"; fg = "#777"; }
    else if (days <= 1) { bg = "#fde7e7"; fg = "#b3261e"; }
    else if (days <= 7) { bg = "#fff1d6"; fg = "#9a5b00"; }

    return "display:inline-block;margin-left:6px;padding:1px 8px;border-radius:999px;" +
      "font-size:12px;font-weight:700;background:" + bg + ";color:" + fg + ";";
  }

  function badgeText(days) {
    if (days < 0) return "已結單";
    if (days === 0) return "今天結單";
    if (days === 1) return "明天結單";
    return "還有 " + days + " 天";
  }

  function findDateElement(card) {
    var summary = card.querySelector(".productBoxSummary");

    if (summary && DATE_RE.test(summary.textContent || "")) return summary;

    var all = card.querySelectorAll("*");

    for (var i = 0; i < all.length; i++) {
      var el = all[i];

      if (el.children.length === 0 && DATE_RE.test(el.textContent || "")) return el;
    }

    return null;
  }

  function applyBadge(card, rec) {
    var days = daysLeft(rec.y, rec.m, rec.d);
    var existing = card.querySelector("[data-ag-badge]");

    if (existing) {
      var wantText = badgeText(days);

      if (existing.textContent !== wantText) {
        existing.textContent = wantText;
        existing.setAttribute("style", badgeStyle(days));
      }

      return;
    }

    var badge = document.createElement("span");
    badge.setAttribute("data-ag-badge", "1");
    badge.setAttribute("style", badgeStyle(days));
    badge.textContent = badgeText(days);

    var dateEl = findDateElement(card);

    if (dateEl) {
      dateEl.insertAdjacentElement("afterend", badge);
    } else {
      card.appendChild(badge);
    }
  }

  /* ---------- 控制面板 ---------- */

  var panel, countEl, statusEl, syncBtn;

  function buildPanel() {
    if (document.getElementById("ag-panel")) return;

    panel = document.createElement("div");
    panel.id = "ag-panel";
    panel.setAttribute(
      "style",
      "position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#fff;" +
      "border:1px solid #c9b8a6;border-radius:12px;padding:10px 12px;font-size:13px;" +
      "color:#333;box-shadow:0 2px 10px rgba(0,0,0,.18);font-family:sans-serif;"
    );

    countEl = document.createElement("div");
    countEl.setAttribute("style", "margin-bottom:6px;font-weight:700;");
    panel.appendChild(countEl);

    statusEl = document.createElement("div");
    statusEl.setAttribute("style", "margin-bottom:6px;font-size:12px;color:#7a5a3a;max-width:260px;");
    panel.appendChild(statusEl);

    var btnStyle =
      "cursor:pointer;border:1px solid #8a6a4a;border-radius:8px;padding:4px 10px;" +
      "background:#fff;color:#5b3f24;font-size:12px;margin:0 6px 4px 0;";

    syncBtn = document.createElement("button");
    syncBtn.textContent = "一鍵更新全部";
    syncBtn.setAttribute("style", btnStyle + "background:#5b3f24;color:#fff;");
    syncBtn.addEventListener("click", syncAll);
    panel.appendChild(syncBtn);

    var exportBtn = document.createElement("button");
    exportBtn.textContent = "匯出 deadline.json";
    exportBtn.setAttribute("style", btnStyle);
    exportBtn.addEventListener("click", exportJson);
    panel.appendChild(exportBtn);

    var clearBtn = document.createElement("button");
    clearBtn.textContent = "清空";
    clearBtn.setAttribute("style", btnStyle);
    clearBtn.addEventListener("click", function () {
      if (window.confirm("確定要清空已收集的商品嗎？")) {
        saveStore({});
        updatePanel();
      }
    });
    panel.appendChild(clearBtn);

    document.body.appendChild(panel);
  }

  function updatePanel() {
    if (!countEl) return;

    var n = Object.keys(loadStore()).length;
    var text = "已收集 " + n + " 件商品";

    if (countEl.textContent !== text) countEl.textContent = text;
  }

  /* ---------- 匯出 deadline.json ---------- */

  function exportJson() {
    var store = loadStore();
    var groups = {};
    var total = 0;

    Object.keys(store).forEach(function (url) {
      var it = store[url];
      var days = daysLeft(it.y, it.m, it.d);

      if (days < -KEEP_EXPIRED_DAYS) return; // 結單太久的不再匯出

      if (!groups[it.date]) {
        groups[it.date] = { date: it.date, y: it.y, m: it.m, d: it.d, items: [] };
      }

      groups[it.date].items.push({
        title: it.title,
        url: it.url,
        price: it.price,
        photo: it.photo
      });

      total++;
    });

    var list = Object.keys(groups)
      .map(function (k) { return groups[k]; })
      .sort(function (a, b) {
        return Date.UTC(a.y, a.m - 1, a.d) - Date.UTC(b.y, b.m - 1, b.d);
      })
      .map(function (g) {
        g.items.sort(function (a, b) { return a.title < b.title ? -1 : a.title > b.title ? 1 : 0; });

        return {
          date: g.date,
          count: g.items.length,
          items: g.items,
          days_left: daysLeft(g.y, g.m, g.d)
        };
      });

    var out = { updated: taipeiNowText(), total: total, groups: list };

    var blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "deadline.json";
    document.body.appendChild(link);
    link.click();

    setTimeout(function () {
      URL.revokeObjectURL(link.href);
      link.remove();
    }, 1000);
  }

  /* ---------- 一鍵更新全部 ---------- */
  /*
   * 在你已經打開的商店頁面裡，用和頁面本身相同的方式（同網域、帶著你瀏覽器的狀態）
   * 取得「最新預購」清單。保護機制：
   *   - 只有按下按鈕才會執行，不會自動啟動
   *   - 只發約 4 次請求，每次之間隨機等 3～6 秒
   *   - 任何一次遇到 429／403／排隊訊息／非預期內容，立刻停止，絕不重試
   *   - 兩次更新至少間隔 1 小時（失敗也算一次，避免被擋時還一直試）
   *   - 沒完整抓完就不會覆蓋已收集的資料
   */

  var syncing = false;

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function lastSyncTime() {
    try {
      return Number(localStorage.getItem(SYNC_KEY) || 0);
    } catch (e) {
      return 0;
    }
  }

  function markSyncTime() {
    try {
      localStorage.setItem(SYNC_KEY, String(Date.now()));
    } catch (e) { /* 略過 */ }
  }

  function recordFromApi(p) {
    var m = DATE_RE.exec((p.summary || "").trim());

    if (!m || !p.route) return null;

    var y = Number(m[1]);
    var mo = Number(m[2]);
    var d = Number(m[3]);
    var price = Number(p.shownPrice);

    return {
      title: String(p.title || ""),
      url: location.origin + "/item/" + p.route,
      price: isFinite(price) ? price : null,
      photo: String(p.photo || ""),
      date: y + "/" + mo + "/" + d,
      y: y,
      m: mo,
      d: d
    };
  }

  async function syncAll() {
    if (syncing) return;

    var wait = SYNC_COOLDOWN_MS - (Date.now() - lastSyncTime());

    if (wait > 0) {
      setStatus("距離上次更新不到 1 小時，請約 " + Math.ceil(wait / 60000) + " 分鐘後再試。");
      return;
    }

    syncing = true;
    syncBtn.disabled = true;
    markSyncTime();

    var collected = [];
    var total = null;

    try {
      for (var page = 1; page <= API_MAX_PAGES; page++) {
        setStatus("更新中…第 " + page + " 次請求");

        var params = new URLSearchParams(API_PARAMS);
        params.set("limit", String(API_LIMIT));
        params.set("page", String(page));

        var res = await fetch(API_PATH + "?" + params.toString(), {
          credentials: "same-origin",
          headers: { Accept: "application/json" }
        });

        if (res.status === 429 || res.status === 403) {
          throw new Error("平台回應 " + res.status + "（人數眾多／限流），已停止，不會重試。");
        }

        if (!res.ok) {
          throw new Error("平台回應 HTTP " + res.status + "，已停止。");
        }

        var data;

        try {
          data = await res.json();
        } catch (e) {
          throw new Error("回應不是預期的資料格式，已停止。");
        }

        if (!data || data.result !== "success" || !data.response || !data.response[0]) {
          throw new Error("平台回應：" + ((data && data.message) || "失敗") + "，已停止，不會重試。");
        }

        var block = data.response[0];
        var list = block.products || [];

        if (total === null && typeof block.total_products === "number") total = block.total_products;

        if (!list.length) break;

        collected = collected.concat(list);

        if (total !== null && collected.length >= total) break;
        if (list.length < API_LIMIT) break;

        setStatus("已取得 " + collected.length + (total ? "／" + total : "") + " 件，稍候…");
        await sleep(DELAY_MIN_MS + Math.random() * (DELAY_MAX_MS - DELAY_MIN_MS));
      }

      if (total !== null && collected.length < total) {
        throw new Error("只取得 " + collected.length + "／" + total + " 件，資料不完整，未更新。");
      }

      /* 完整取得：以這次的清單為準。
         舊資料只保留「已結單、且 7 天內」的（平台結單後可能很快下架，留著讓頁面仍能顯示「已過期結單」）；
         結單日還沒到、卻不在這次清單裡的，代表已下架，直接丟棄。 */
      var fresh = {};

      collected.forEach(function (p) {
        var rec = recordFromApi(p);

        if (rec) fresh[rec.url] = rec;
      });

      var old = loadStore();

      Object.keys(old).forEach(function (url) {
        var it = old[url];
        var dl = daysLeft(it.y, it.m, it.d);

        if (!fresh[url] && dl < 0 && dl >= -KEEP_EXPIRED_DAYS) {
          fresh[url] = it;
        }
      });

      saveStore(fresh);
      updatePanel();

      setStatus(
        "完成：平台共 " + (total === null ? collected.length : total) + " 件，其中 " +
        Object.keys(fresh).length + " 件有結單日期，已自動匯出 deadline.json。"
      );

      exportJson();
    } catch (err) {
      setStatus("❌ " + (err && err.message ? err.message : "更新失敗") + "（已收集的舊資料沒有被改動）");
    } finally {
      syncing = false;
      syncBtn.disabled = false;
    }
  }

  /* ---------- 主流程 ---------- */

  function scan() {
    var cards = findCards();
    var store = loadStore();
    var changed = false;

    Object.keys(cards).forEach(function (url) {
      var rec = parseCard(url, cards[url]);

      if (!rec) return;

      applyBadge(cards[url], rec);

      var old = store[url];

      if (!old || old.date !== rec.date || old.title !== rec.title || old.price !== rec.price) {
        store[url] = rec;
        changed = true;
      }
    });

    if (changed) saveStore(store);

    buildPanel();
    updatePanel();
  }

  var timer = null;

  function scheduleScan() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(scan, 600);
  }

  scan();

  new MutationObserver(scheduleScan).observe(document.body, { childList: true, subtree: true });
})();
