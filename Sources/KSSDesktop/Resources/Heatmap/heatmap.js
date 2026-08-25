(() => {
  const MARKETS = [
    ["all", "全A"],
    ["sse", "上证"],
    ["szse", "深证"],
    ["hs300", "沪深300"],
    ["zza50", "中证A50"],
    ["zza500", "中证A500"],
    ["main", "主板"],
    ["cyb", "创业板"],
    ["kcb", "科创板"],
  ];
  const PERIODS = [
    ["day", "当日"],
    ["week", "近5日"],
    ["month", "近20日"],
    ["year", "今年以来"],
  ];

  const canvas = document.getElementById("map");
  const tip = document.getElementById("tip");
  const statsEl = document.getElementById("stats");
  const industryEl = document.getElementById("industries");
  const marketEl = document.getElementById("market");
  const periodEl = document.getElementById("period");
  const shotEl = document.getElementById("shot");

  let snapshot = null;
  let focused = new Set();
  let scale = 1;
  let panX = 0;
  let panY = 0;
  let drag = null;
  let boxes = [];

  function post(payload) {
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.kssHeatmap) {
      window.webkit.messageHandlers.kssHeatmap.postMessage(payload);
    }
  }

  function fillSelect(el, pairs, value) {
    el.innerHTML = "";
    for (const [id, label] of pairs) {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = label;
      el.appendChild(opt);
    }
    el.value = value;
  }

  fillSelect(marketEl, MARKETS, "all");
  fillSelect(periodEl, PERIODS, "day");

  marketEl.addEventListener("change", () => {
    post({ action: "refetch", market: marketEl.value, period: periodEl.value });
  });
  periodEl.addEventListener("change", () => {
    post({ action: "refetch", market: marketEl.value, period: periodEl.value });
  });

  function formatTurnover(value) {
    if (value >= 1e12) return (value / 1e12).toFixed(2) + " 万亿";
    if (value >= 1e8) return (value / 1e8).toFixed(2) + " 亿";
    if (value >= 1e4) return (value / 1e4).toFixed(2) + " 万";
    return String(Math.round(value));
  }

  function visibleTiles() {
    if (!snapshot) return [];
    if (!focused.size) return snapshot.tiles;
    return snapshot.tiles.filter((tile) => focused.has(tile.industry));
  }

  function localSummary(tiles) {
    let up = 0, flat = 0, down = 0, turnover = 0;
    for (const tile of tiles) {
      turnover += tile.turnover || 0;
      const change = tile.changePct || 0;
      if (Math.abs(change) < 0.1) flat += 1;
      else if (change > 0) up += 1;
      else down += 1;
    }
    return { advanceCount: up, flatCount: flat, declineCount: down, turnoverAmount: turnover };
  }

  function renderStats() {
    const tiles = visibleTiles();
    const summary = focused.size ? localSummary(tiles) : (snapshot && snapshot.summary) || localSummary(tiles);
    statsEl.textContent = `涨 ${summary.advanceCount} 平 ${summary.flatCount} 跌 ${summary.declineCount} · 成交额 ${formatTurnover(summary.turnoverAmount)}`;
  }

  function renderIndustries() {
    industryEl.innerHTML = "";
    if (!snapshot) return;
    const names = [...new Set(snapshot.tiles.map((tile) => tile.industry))].sort();
    for (const name of names) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip" + (focused.has(name) ? " on" : "");
      chip.textContent = name;
      chip.addEventListener("click", () => {
        if (focused.has(name)) focused.delete(name);
        else focused.add(name);
        renderIndustries();
        renderStats();
        draw();
      });
      industryEl.appendChild(chip);
    }
  }

  function tapeColor(change) {
    const t = Math.max(-1, Math.min(1, change / 8));
    if (t >= 0) {
      const a = 0.28 + t * 0.72;
      return `rgba(214, 48, 49, ${a})`;
    }
    const a = 0.28 + (-t) * 0.72;
    return `rgba(0, 148, 92, ${a})`;
  }

  function squarify(items, x, y, w, h) {
    const nodes = items
      .map((item) => ({ item, value: Math.max(item.value, 1) }))
      .sort((a, b) => b.value - a.value);
    const total = nodes.reduce((sum, node) => sum + node.value, 0) || 1;
    const out = [];
    let i = 0;
    let cx = x, cy = y, cw = w, ch = h;
    while (i < nodes.length && cw > 1 && ch > 1) {
      const vertical = cw >= ch;
      const side = vertical ? ch : cw;
      let acc = 0;
      let j = i;
      let worst = Infinity;
      while (j < nodes.length) {
        acc += nodes[j].value;
        const row = nodes.slice(i, j + 1);
        const rowArea = (acc / total) * (w * h);
        const rowMain = rowArea / side;
        let nextWorst = 0;
        for (const node of row) {
          const other = (node.value / acc) * side;
          nextWorst = Math.max(nextWorst, Math.max(rowMain / other, other / rowMain));
        }
        if (nextWorst > worst && j > i) {
          acc -= nodes[j].value;
          break;
        }
        worst = nextWorst;
        j += 1;
      }
      const row = nodes.slice(i, j);
      const rowArea = (acc / total) * (w * h);
      const rowMain = rowArea / side;
      let cursor = 0;
      for (const node of row) {
        const frac = node.value / acc;
        if (vertical) {
          out.push({ item: node.item, x: cx, y: cy + cursor, w: rowMain, h: frac * ch });
          cursor += frac * ch;
        } else {
          out.push({ item: node.item, x: cx + cursor, y: cy, w: frac * cw, h: rowMain });
          cursor += frac * cw;
        }
      }
      if (vertical) {
        cx += rowMain;
        cw -= rowMain;
      } else {
        cy += rowMain;
        ch -= rowMain;
      }
      i = j;
    }
    return out;
  }

  function layout() {
    boxes = [];
    const tiles = visibleTiles();
    if (!tiles.length) return;
    const groups = new Map();
    for (const tile of tiles) {
      const key = tile.industry || "未分类";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(tile);
    }
    const boards = [...groups.entries()].map(([name, children]) => ({
      name,
      value: children.reduce((sum, tile) => sum + Math.max(tile.circMv, 1), 0),
      children,
    }));
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const boardBoxes = squarify(boards, 0, 0, width, height);
    for (const board of boardBoxes) {
      const header = 16;
      const inner = squarify(
        board.item.children.map((tile) => ({ ...tile, value: Math.max(tile.circMv, 1) })),
        board.x + 1,
        board.y + header,
        Math.max(board.w - 2, 1),
        Math.max(board.h - header - 1, 1)
      );
      boxes.push({ kind: "board", name: board.item.name, x: board.x, y: board.y, w: board.w, h: board.h });
      for (const box of inner) {
        boxes.push({ kind: "tile", tile: box.item, x: box.x, y: box.y, w: box.w, h: box.h });
      }
    }
  }

  function draw() {
    layout();
    const ctx = canvas.getContext("2d");
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    ctx.save();
    ctx.setTransform((window.devicePixelRatio || 1), 0, 0, (window.devicePixelRatio || 1), 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--bg") || "#111";
    ctx.fillRect(0, 0, width, height);
    ctx.translate(panX, panY);
    ctx.scale(scale, scale);
    for (const box of boxes) {
      if (box.kind === "board") {
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--surface") || "#1a1a1a";
        ctx.fillRect(box.x, box.y, box.w, box.h);
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--muted") || "#999";
        ctx.font = "11px sans-serif";
        ctx.fillText(box.name, box.x + 4, box.y + 12, Math.max(box.w - 8, 10));
      } else {
        ctx.fillStyle = tapeColor(box.tile.changePct);
        ctx.fillRect(box.x, box.y, box.w, box.h);
        if (box.w > 36 && box.h > 22) {
          ctx.fillStyle = "rgba(255,255,255,0.92)";
          ctx.font = "11px sans-serif";
          ctx.fillText(box.tile.name, box.x + 3, box.y + 13, box.w - 6);
          ctx.fillText((box.tile.changePct >= 0 ? "+" : "") + box.tile.changePct.toFixed(2) + "%", box.x + 3, box.y + 26, box.w - 6);
        }
      }
    }
    ctx.restore();
  }

  function worldPoint(event) {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left - panX) / scale;
    const y = (event.clientY - rect.top - panY) / scale;
    return { x, y };
  }

  function hit(event) {
    const point = worldPoint(event);
    for (let i = boxes.length - 1; i >= 0; i -= 1) {
      const box = boxes[i];
      if (box.kind !== "tile") continue;
      if (point.x >= box.x && point.x <= box.x + box.w && point.y >= box.y && point.y <= box.y + box.h) {
        return box;
      }
    }
    return null;
  }

  canvas.addEventListener("mousemove", (event) => {
    if (drag) {
      panX += event.clientX - drag.x;
      panY += event.clientY - drag.y;
      drag = { x: event.clientX, y: event.clientY };
      draw();
      return;
    }
    const box = hit(event);
    if (!box) {
      tip.hidden = true;
      return;
    }
    const tile = box.tile;
    tip.hidden = false;
    tip.innerHTML = `${tile.name} ${tile.symbol}<br>${tile.industry}<br>${tile.changePct.toFixed(2)}% · 成交 ${formatTurnover(tile.turnover)}`;
    const rect = canvas.getBoundingClientRect();
    tip.style.left = `${event.clientX - rect.left + 12}px`;
    tip.style.top = `${event.clientY - rect.top + 12}px`;
  });

  canvas.addEventListener("mousedown", (event) => {
    drag = { x: event.clientX, y: event.clientY };
    canvas.style.cursor = "grabbing";
  });
  window.addEventListener("mouseup", () => {
    drag = null;
    canvas.style.cursor = "grab";
  });
  canvas.addEventListener("click", (event) => {
    const box = hit(event);
    if (!box) return;
    post({ action: "selectStock", symbol: box.tile.symbol });
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? 1.08 : 0.92;
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const next = Math.max(0.4, Math.min(8, scale * factor));
    panX = mx - (mx - panX) * (next / scale);
    panY = my - (my - panY) * (next / scale);
    scale = next;
    draw();
  }, { passive: false });

  shotEl.addEventListener("click", () => {
    const link = document.createElement("a");
    link.download = `kss-heatmap-${(snapshot && snapshot.tradeDate) || "shot"}.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
  });

  window.addEventListener("resize", () => {
    if (snapshot) draw();
  });

  function applySnapshot(payload) {
    if (!payload || payload.source !== "direct" || !payload.tiles || !payload.tiles.length) {
      return;
    }
    snapshot = payload;
    focused = new Set();
    scale = 1;
    panX = 0;
    panY = 0;
    if (payload.market) marketEl.value = payload.market;
    if (payload.period) periodEl.value = payload.period;
    renderIndustries();
    renderStats();
    draw();
  }

  window.kssSetHeatmap = function (payload) {
    applySnapshot(typeof payload === "string" ? JSON.parse(payload) : payload);
  };

  window.kssSetHeatmapB64 = function (b64) {
    const bytes = Uint8Array.from(atob(b64), (ch) => ch.charCodeAt(0));
    applySnapshot(JSON.parse(new TextDecoder().decode(bytes)));
  };

  window.kssSetTheme = function (payload) {
    const data = typeof payload === "string" ? JSON.parse(payload) : payload;
    const colors = (data && data.colors) || {};
    const root = document.documentElement.style;
    if (colors.bg) root.setProperty("--bg", colors.bg);
    if (colors.surface) root.setProperty("--surface", colors.surface);
    if (colors.ink) root.setProperty("--ink", colors.ink);
    if (colors.muted) root.setProperty("--muted", colors.muted);
    if (colors.line) root.setProperty("--line", colors.line);
    if (colors.accent) root.setProperty("--accent", colors.accent);
    if (snapshot) draw();
  };
})();
