// 素材选择弹窗：按资产类别整理（角色 / 场景 / 道具 / 风格 / 分镜 / 其他）。
// 类别默认从节点名「类别｜名字」的前缀推出来；右键卡片可手动归类（存在节点的 assetCategory 字段）。
// 同一类别下按前缀分成可折叠的小文件夹，顶部带搜索。挂在 canvas.js 的 AssetPicker 上，不改原文件。
(function () {
  if (typeof AssetPicker === "undefined") return;

  const CATS = [
    { id: "all", label: "全部" },
    { id: "role", label: "角色" },
    { id: "scene", label: "场景" },
    { id: "prop", label: "道具" },
    { id: "style", label: "风格" },
    { id: "shot", label: "分镜" },
    { id: "other", label: "其他" },
  ];
  const CAT_LABEL = Object.fromEntries(CATS.map((c) => [c.id, c.label]));
  const ORDER = Object.fromEntries(CATS.map((c, i) => [c.id, i]));
  const RULES = [
    ["role", /人物|角色|定妆|无头|压力测试|sheet|character/i],
    ["scene", /场景|scene|环境|地点/i],
    ["prop", /道具|prop|物件/i],
    ["style", /宣传|风格|style|海报|色调|参考风/i],
    ["shot", /首帧|尾帧|视频|分镜|^镜\s*\d|镜头|片头|shot/i],
  ];
  const COLLAPSE_KEY = "assetPicker.collapsed";

  const prefixOf = (name) => (name.includes("｜") ? name.split("｜")[0].trim() : "");

  function autoCat(node) {
    const name = node.customName || "";
    const p = prefixOf(name);
    for (const [cat, re] of RULES) if (p && re.test(p)) return cat;
    for (const [cat, re] of RULES) if (re.test(name)) return cat;
    if (node.type === "video" || node.type === "ref-video") return "shot";
    return "other";
  }
  const catOf = (node) => (CAT_LABEL[node.assetCategory] && node.assetCategory !== "all" ? node.assetCategory : autoCat(node));
  const subOf = (node, cat) => prefixOf(node.customName || "") || CAT_LABEL[cat];

  let collapsed;
  try { collapsed = new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || "[]")); } catch { collapsed = new Set(); }
  const saveCollapsed = () => { try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify([...collapsed])); } catch {} };

  AssetPicker.cat = "all";
  AssetPicker.query = "";

  function ensureChrome(picker) {
    const toolbar = document.querySelector("#asset-picker .asset-picker-toolbar");
    if (!toolbar || document.getElementById("asset-picker-cats")) return;
    const row = document.createElement("div");
    row.className = "asset-picker-catbar";
    row.innerHTML =
      `<div class="asset-picker-cats" id="asset-picker-cats">${CATS.map((c) => `<button type="button" class="asset-cat" data-cat="${c.id}">${c.label}<span class="asset-cat-n"></span></button>`).join("")}</div>` +
      `<input type="search" id="asset-picker-search" class="asset-picker-search" placeholder="搜索素材名" autocomplete="off" />`;
    toolbar.after(row);
    row.querySelectorAll(".asset-cat").forEach((b) =>
      b.addEventListener("click", () => { picker.cat = b.dataset.cat; picker.render(); }));
    const search = row.querySelector("#asset-picker-search");
    search.addEventListener("input", () => { picker.query = search.value.trim().toLowerCase(); picker.render(); });
    // 右键菜单
    const menu = document.createElement("div");
    menu.id = "asset-cat-menu";
    menu.className = "asset-cat-menu";
    menu.hidden = true;
    document.body.appendChild(menu);
    document.addEventListener("pointerdown", (e) => { if (!menu.contains(e.target)) menu.hidden = true; }, true);
  }

  const origCollect = AssetPicker._collectCanvasItems;
  AssetPicker._collectCanvasItems = function () {
    const all = origCollect.call(this).map((it) => {
      const cat = catOf(it.node);
      return { ...it, cat, sub: subOf(it.node, cat) };
    });
    // 不到 3 张的小文件夹并回类别本身，免得满屏零碎分组
    const subN = new Map();
    for (const it of all) subN.set(`${it.cat}/${it.sub}`, (subN.get(`${it.cat}/${it.sub}`) || 0) + 1);
    for (const it of all) if (subN.get(`${it.cat}/${it.sub}`) < 3) it.sub = CAT_LABEL[it.cat];
    this._catCounts = {};
    for (const it of all) this._catCounts[it.cat] = (this._catCounts[it.cat] || 0) + 1;
    this._catCounts.all = all.length;
    const q = this.query;
    return all
      .filter((it) => this.cat === "all" || it.cat === this.cat)
      .filter((it) => !q || (getNodeDisplayName(it.node, it.board) || "").toLowerCase().includes(q) || (it.node.customName || "").toLowerCase().includes(q))
      .map((it, i) => ({ ...it, _i: i }))
      .sort((a, b) => ORDER[a.cat] - ORDER[b.cat] || a.sub.localeCompare(b.sub, "zh") || a._i - b._i);
  };

  const origRender = AssetPicker.render;
  AssetPicker.render = function () {
    ensureChrome(this);
    origRender.call(this);
    const grid = document.getElementById("asset-picker-grid");
    if (!grid) return;
    document.querySelectorAll("#asset-picker .asset-cat").forEach((b) => {
      const n = this._catCounts?.[b.dataset.cat] || 0;
      b.classList.toggle("active", b.dataset.cat === this.cat);
      b.querySelector(".asset-cat-n").textContent = n ? ` ${n}` : "";
      b.classList.toggle("is-empty", !n && b.dataset.cat !== "all");
    });
    const items = this._collectCanvasItems();
    const byKey = new Map(items.map((it) => [it.key, it]));
    const cards = [...grid.querySelectorAll(".asset-card")];
    if (!cards.length) {
      if (this.query || this.cat !== "all") grid.innerHTML = `<div class="asset-picker-empty">没有符合条件的素材</div>`;
      return;
    }
    // 每个「类别 / 前缀」一个可折叠的小文件夹
    const groupCount = new Map();
    for (const it of items) { const g = `${it.cat}/${it.sub}`; groupCount.set(g, (groupCount.get(g) || 0) + 1); }
    let last = null;
    for (const card of cards) {
      const it = byKey.get(card.dataset.key);
      if (!it) continue;
      const g = `${it.cat}/${it.sub}`;
      card.dataset.group = g;
      const meta = card.querySelector(".asset-card-meta");
      if (meta && !meta.dataset.cat) { meta.dataset.cat = "1"; meta.textContent = `${CAT_LABEL[it.cat]} · ${meta.textContent}`; }
      if (g !== last) {
        const head = document.createElement("button");
        head.type = "button";
        head.className = "asset-group-head" + (collapsed.has(g) ? " is-collapsed" : "");
        head.dataset.group = g;
        const showCat = this.cat === "all" && it.sub !== CAT_LABEL[it.cat];
        head.innerHTML = `<span class="asset-group-caret">▾</span><span class="asset-group-name">${escapeHtml(showCat ? `${CAT_LABEL[it.cat]} / ${it.sub}` : it.sub)}</span><span class="asset-group-n">${groupCount.get(g)}</span>`;
        head.addEventListener("click", () => {
          if (collapsed.has(g)) collapsed.delete(g); else collapsed.add(g);
          saveCollapsed();
          head.classList.toggle("is-collapsed", collapsed.has(g));
          grid.querySelectorAll(".asset-card").forEach((c) => { if (c.dataset.group === g) c.hidden = collapsed.has(g); });
        });
        card.before(head);
        last = g;
      }
      card.hidden = collapsed.has(g) && !this.query;
      card.addEventListener("contextmenu", (e) => { e.preventDefault(); openMenu(this, it, e.clientX, e.clientY); });
    }
  };

  // 从节点的 + 上传本地图片：传完直接牵线到这个节点并关窗，不用再点「添加」
  const origUpload = AssetPicker.uploadLocalImage;
  AssetPicker.uploadLocalImage = async function (file) {
    const hostId = this.placeMode ? null : this.hostNodeId;
    const board = CanvasStore.activeBoard();
    const before = new Set((board?.nodes || []).map((n) => n.id));
    await origUpload.call(this, file);
    if (!hostId || CanvasStore.activeBoard() !== board) return;
    const added = board.nodes.find((n) => !before.has(n.id) && n.type === "ref-image" && n.imageUrl);
    if (!added) return;
    this.selected.delete(`${board.id}:${added.id}`);
    if (CanvasStore.addConnection(added.id, hostId)) CanvasHistory.push();
    refreshNodeInputRefs(hostId);
    if (!this.selected.size) this.close();
    await CanvasStore.flushSave();
    if (typeof showCanvasToast === "function") showCanvasToast(`已上传并连到当前节点：${file.name}`);
  };

  // 节点工具栏「存为资产」：选类别、小类、名字，写成「小类｜名字」并记下类别
  const DEFAULT_SUB = { role: "人物定妆", scene: "场景", prop: "道具", style: "风格", shot: "首帧", other: "其他" };
  const findNode = (id) => { for (const b of CanvasStore.boards) { const n = b.nodes?.find((x) => x.id === id); if (n) return n; } return null; };
  const origCopyBtn = window.renderNodeCopyRefBtn;
  if (typeof origCopyBtn === "function") {
    window.renderNodeCopyRefBtn = function (id) {
      const node = findNode(String(id || "").trim());
      const media = node && (node.imageUrl || node.videoUrl || node.coverUrl || node.audioUrl);
      return origCopyBtn(id) + (media
        ? `<button type="button" class="media-tool-icon-btn" data-action="tool-save-asset" data-node="${escapeAttr(node.id)}" title="存为资产（归类）" aria-label="存为资产">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M2.5 4.5a1 1 0 011-1h3l1.3 1.5h4.7a1 1 0 011 1v6a1 1 0 01-1 1h-9a1 1 0 01-1-1z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M8 7.3v3.4M6.3 9h3.4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
        </button>`
        : "");
    };
  }

  function openSaveAsset(nodeId, anchor) {
    const node = findNode(nodeId);
    if (!node) return;
    let pop = document.getElementById("asset-save-pop");
    if (!pop) {
      pop = document.createElement("div");
      pop.id = "asset-save-pop";
      pop.className = "asset-save-pop";
      document.body.appendChild(pop);
      document.addEventListener("pointerdown", (e) => { if (!pop.hidden && !pop.contains(e.target) && !e.target.closest?.('[data-action="tool-save-asset"]')) pop.hidden = true; }, true);
    }
    const name = node.customName || "";
    let cat = catOf(node);
    const curSub = prefixOf(name);
    const bare = curSub ? name.slice(name.indexOf("｜") + 1).trim() : name;
    pop.innerHTML =
      `<div class="asset-save-title">存为资产</div>` +
      `<div class="asset-save-cats">${CATS.filter((c) => c.id !== "all").map((c) => `<button type="button" data-cat="${c.id}">${c.label}</button>`).join("")}</div>` +
      `<label>小类（文件夹）<input type="text" id="asset-save-sub" /></label>` +
      `<label>名字<input type="text" id="asset-save-name" placeholder="如：陈三更、保安室_夜" /></label>` +
      `<div class="asset-save-foot"><button type="button" class="btn btn-ghost" data-act="cancel">取消</button><button type="button" class="btn btn-primary" data-act="ok">保存</button></div>`;
    const subIn = pop.querySelector("#asset-save-sub"), nameIn = pop.querySelector("#asset-save-name");
    subIn.value = curSub || DEFAULT_SUB[cat];
    nameIn.value = bare;
    const syncCats = () => pop.querySelectorAll(".asset-save-cats button").forEach((b) => b.classList.toggle("is-on", b.dataset.cat === cat));
    pop.querySelectorAll(".asset-save-cats button").forEach((b) => b.addEventListener("click", () => {
      // 小类还是上一个类别的默认值时，跟着换
      if (Object.values(DEFAULT_SUB).includes(subIn.value.trim()) || !subIn.value.trim()) subIn.value = DEFAULT_SUB[b.dataset.cat];
      cat = b.dataset.cat;
      syncCats();
    }));
    syncCats();
    const save = () => {
      const sub = subIn.value.replace(/｜/g, "").trim(), nm = nameIn.value.replace(/｜/g, "").trim();
      if (!nm) { nameIn.focus(); return; }
      CanvasHistory.push();
      CanvasStore.updateNode(node.id, { customName: sub ? `${sub}｜${nm}` : nm, assetCategory: cat });
      pop.hidden = true;
      renderNodes();
      void CanvasStore.flushSave();
      if (typeof showCanvasToast === "function") showCanvasToast(`已存为资产：${CAT_LABEL[cat]} / ${sub || CAT_LABEL[cat]} / ${nm}`);
    };
    pop.querySelector('[data-act="ok"]').addEventListener("click", save);
    pop.querySelector('[data-act="cancel"]').addEventListener("click", () => { pop.hidden = true; });
    pop.querySelectorAll("input").forEach((i) => i.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") save();
      if (e.key === "Escape") pop.hidden = true;
    }));
    const r = anchor.getBoundingClientRect();
    pop.hidden = false;
    pop.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 300))}px`;
    pop.style.top = `${Math.min(r.bottom + 8, window.innerHeight - pop.offsetHeight - 8)}px`;
    nameIn.focus();
    nameIn.select();
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.('[data-action="tool-save-asset"]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    openSaveAsset(btn.dataset.node, btn);
  }, true);
  if (typeof renderNodes === "function") try { renderNodes(); } catch {}

  function openMenu(picker, it, x, y) {
    const menu = document.getElementById("asset-cat-menu");
    if (!menu) return;
    const cur = it.node.assetCategory;
    menu.innerHTML =
      `<div class="asset-cat-menu-title">归类到</div>` +
      CATS.filter((c) => c.id !== "all").map((c) => `<button type="button" data-cat="${c.id}" class="${cur === c.id ? "is-on" : ""}">${c.label}</button>`).join("") +
      `<button type="button" data-cat="" class="${!cur ? "is-on" : ""}">自动（按名字：${CAT_LABEL[autoCat(it.node)]}）</button>`;
    menu.style.left = `${Math.min(x, window.innerWidth - 200)}px`;
    menu.style.top = `${Math.min(y, window.innerHeight - 280)}px`;
    menu.hidden = false;
    menu.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        CanvasStore.updateNode(it.nodeId, { assetCategory: b.dataset.cat || null });
        menu.hidden = true;
        picker.render();
        if (typeof showCanvasToast === "function") showCanvasToast(b.dataset.cat ? `已归类到「${CAT_LABEL[b.dataset.cat]}」` : "已改回按名字自动归类");
      }));
  }
})();
