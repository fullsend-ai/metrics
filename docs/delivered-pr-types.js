(async function () {
  "use strict";

  const PR_TYPE_KEYS = ["feat", "fix", "docs", "ci", "chore", "test", "perf", "other"];
  // Concrete hex — CSS vars are unreliable as SVG fill in some browsers
  const TYPE_COLORS = {
    feat: "#6b93f7", fix: "#ff453a", docs: "#9775fa", ci: "#f5c842",
    chore: "#8e8e93", test: "#4fbc6b", perf: "#f4845f", other: "#5c5c5e",
  };
  const FIX_KEYS = ["core", "external", "bot"];
  const FIX_COLORS = { core: "#6b93f7", external: "#4fbc6b", bot: "#f4845f" };
  const FIX_LABELS = {
    core: "Core team filed",
    external: "External human filed",
    bot: "Bot filed",
  };

  const tooltip = d3.select("#tooltip");
  let rangeDays = 90;
  let smoothDays = 28;
  let selectedRepo = "__all__";
  let hideWeekends = true;
  let holidayDates = new Set();
  let raw = [];

  try {
    const text = await d3.text("holidays.yaml");
    for (const line of text.split("\n")) {
      const m = line.match(/^\s+(\d{4}-\d{2}-\d{2}):/);
      if (m) holidayDates.add(m[1]);
    }
  } catch (e) { /* optional */ }

  try {
    raw = await d3.csv("pr-type.csv", d => ({
      date: d.date,
      repo: d.repo,
      feat: +d.feat, fix: +d.fix, docs: +d.docs, ci: +d.ci,
      chore: +d.chore, test: +d.test, perf: +d.perf, other: +d.other,
      fix_core: +d.fix_core, fix_external: +d.fix_external,
      fix_bot: +d.fix_bot, fix_unlinked: +d.fix_unlinked,
    }));
  } catch (e) {
    document.getElementById("hero").innerHTML = "<p>No pr-type.csv yet — run the collector.</p>";
    return;
  }

  function isNonWorkDay(dateStr) {
    const d = new Date(dateStr + "T12:00:00");
    const day = d.getDay();
    return day === 0 || day === 6 || holidayDates.has(dateStr);
  }

  function smoothDaily(daily, window, keys) {
    if (window <= 0 || daily.length === 0) return daily;
    return daily.map((d, i) => {
      const start = Math.max(0, i - window + 1);
      const slice = daily.slice(start, i + 1);
      const smoothed = { ...d };
      keys.forEach(k => { smoothed[k] = d3.mean(slice, s => s[k]); });
      return smoothed;
    });
  }

  function filterDaily(applySmooth) {
    let data = raw;
    if (selectedRepo !== "__all__") data = data.filter(d => d.repo === selectedRepo);
    if (rangeDays > 0) {
      const cutoff = d3.timeDay.offset(new Date(), -rangeDays);
      const cutoffStr = d3.timeFormat("%Y-%m-%d")(cutoff);
      data = data.filter(d => d.date >= cutoffStr);
    }
    if (hideWeekends) data = data.filter(d => !isNonWorkDay(d.date));

    const byDate = d3.rollup(data, rows => {
      const row = {};
      PR_TYPE_KEYS.forEach(k => { row[k] = d3.sum(rows, d => d[k]); });
      row.fix_core = d3.sum(rows, d => d.fix_core);
      row.fix_external = d3.sum(rows, d => d.fix_external);
      row.fix_bot = d3.sum(rows, d => d.fix_bot);
      row.fix_unlinked = d3.sum(rows, d => d.fix_unlinked);
      return row;
    }, d => d.date);

    const daily = Array.from(byDate, ([date, vals]) => ({ date, ...vals }))
      .sort((a, b) => a.date.localeCompare(b.date))
      // Drop empty days so sparse repos don't create 0-width spikes
      .filter(d => PR_TYPE_KEYS.some(k => d[k] > 0));

    if (!applySmooth) return daily;
    return smoothDaily(daily, smoothDays, [
      ...PR_TYPE_KEYS, "fix_core", "fix_external", "fix_bot", "fix_unlinked",
    ]);
  }

  function totals(daily) {
    const t = { merged: 0, fix_core: 0, fix_external: 0, fix_bot: 0, fix_unlinked: 0 };
    PR_TYPE_KEYS.forEach(k => { t[k] = 0; });
    daily.forEach(d => {
      PR_TYPE_KEYS.forEach(k => { t[k] += d[k] || 0; });
      t.fix_core += d.fix_core || 0;
      t.fix_external += d.fix_external || 0;
      t.fix_bot += d.fix_bot || 0;
      t.fix_unlinked += d.fix_unlinked || 0;
    });
    PR_TYPE_KEYS.forEach(k => { t[k] = Math.round(t[k]); });
    t.fix_core = Math.round(t.fix_core);
    t.fix_external = Math.round(t.fix_external);
    t.fix_bot = Math.round(t.fix_bot);
    t.fix_unlinked = Math.round(t.fix_unlinked);
    t.merged = PR_TYPE_KEYS.reduce((s, k) => s + t[k], 0);
    t.fix_linked = t.fix_core + t.fix_external + t.fix_bot;
    return t;
  }

  function showTooltip(event, html) {
    tooltip.html(html)
      .style("left", (event.pageX + 12) + "px")
      .style("top", (event.pageY - 12) + "px")
      .style("opacity", 1);
  }
  function hideTooltip() { tooltip.style("opacity", 0); }

  function chartDims(container, height) {
    const width = Math.max(container.node().getBoundingClientRect().width || 960, 640);
    const m = { top: 12, right: 16, bottom: 48, left: 44 };
    return { width, height, m, innerW: width - m.left - m.right, innerH: height - m.top - m.bottom };
  }

  function topKeys(t, keys, n) {
    return keys.slice().sort((a, b) => t[b] - t[a]).slice(0, n);
  }

  function renderLineCounts(sel, daily, keys, colors, labels, height) {
    const container = d3.select(sel);
    container.selectAll("*").remove();
    if (!daily.length || !keys.length) {
      container.html("<p class='subtitle'>No data in range</p>");
      return;
    }

    const dims = chartDims(container, height);
    dims.m.bottom = 28;
    dims.innerH = dims.height - dims.m.top - dims.m.bottom;

    const svg = container.append("svg").attr("viewBox", `0 0 ${dims.width} ${dims.height}`);
    const g = svg.append("g").attr("transform", `translate(${dims.m.left},${dims.m.top})`);

    const x = d3.scaleTime()
      .domain(d3.extent(daily, d => new Date(d.date)))
      .range([0, dims.innerW]);
    const yMax = d3.max(daily, d => d3.max(keys, k => d[k] || 0)) || 1;
    const y = d3.scaleLinear().domain([0, yMax]).nice().range([dims.innerH, 0]);

    g.append("g").attr("class", "grid")
      .call(d3.axisLeft(y).ticks(5).tickSize(-dims.innerW).tickFormat(""));
    g.append("g").attr("class", "axis").attr("transform", `translate(0,${dims.innerH})`)
      .call(d3.axisBottom(x).ticks(6).tickFormat(d3.timeFormat("%b %d")));
    g.append("g").attr("class", "axis")
      .call(d3.axisLeft(y).ticks(5));

    keys.forEach(k => {
      const line = d3.line()
        .x(d => x(new Date(d.date)))
        .y(d => y(d[k] || 0))
        .curve(d3.curveMonotoneX);
      g.append("path")
        .datum(daily)
        .attr("fill", "none")
        .attr("stroke", colors[k])
        .attr("stroke-width", 2.5)
        .attr("d", line);
    });

    const bisect = d3.bisector(d => new Date(d.date)).left;
    const overlay = g.append("rect")
      .attr("width", dims.innerW).attr("height", dims.innerH)
      .attr("fill", "transparent");
    const guide = g.append("line")
      .attr("y1", 0).attr("y2", dims.innerH)
      .attr("stroke", "var(--fg-light)").attr("stroke-width", 1)
      .attr("stroke-dasharray", "3 3").style("opacity", 0);

    overlay.on("mousemove", (event) => {
      const xm = x.invert(d3.pointer(event)[0]);
      const i = Math.min(daily.length - 1, Math.max(0, bisect(daily, xm, 1)));
      const d0 = daily[i - 1] || daily[i];
      const d1 = daily[i];
      const d = xm - new Date(d0.date) > new Date(d1.date) - xm ? d1 : d0;
      guide.attr("x1", x(new Date(d.date))).attr("x2", x(new Date(d.date))).style("opacity", 1);
      const lines = keys.map(k => {
        const label = labels ? labels[k] : k;
        const val = d[k] || 0;
        return `${label}: ${Number.isInteger(val) ? val : val.toFixed(1)}`;
      }).join("<br>");
      showTooltip(event, `<strong>${d.date}</strong><br>${lines}`);
    }).on("mouseleave", () => {
      guide.style("opacity", 0);
      hideTooltip();
    });

    const legend = container.append("div").attr("class", "chart-legend");
    keys.forEach(k => {
      const item = legend.append("span").attr("class", "chart-legend-item");
      item.append("span").attr("class", "swatch").style("background", colors[k]);
      item.append("span").text(labels ? labels[k] : k);
    });
  }

  function renderHero(t) {
    const topType = PR_TYPE_KEYS.slice().sort((a, b) => t[b] - t[a])[0];
    const topPct = t.merged ? (100 * t[topType] / t.merged).toFixed(1) : "0.0";
    const typeBits = PR_TYPE_KEYS
      .slice()
      .sort((a, b) => t[b] - t[a])
      .slice(0, 4)
      .map(k => `${k} ${(100 * t[k] / (t.merged || 1)).toFixed(0)}%`)
      .join(" · ");
    document.getElementById("hero").innerHTML = `
      <div class="stat"><div class="val">${t.merged.toLocaleString()}</div><div class="lbl">Merged PRs</div></div>
      <div class="stat"><div class="val" style="color:#ff453a">${t.fix}</div><div class="lbl">Fix PRs</div></div>
      <div class="stat"><div class="val" style="color:#6b93f7">${topPct}%</div><div class="lbl">Top type: ${topType}</div></div>
      <div class="stat stat-wide"><div class="val-sm">${typeBits || "—"}</div><div class="lbl">Mix in selected range</div></div>`;
  }

  function renderBuckets(t) {
    const pct = k => t.fix_linked ? (100 * t[k] / t.fix_linked).toFixed(1) : "0.0";
    document.getElementById("fix-buckets").innerHTML = FIX_KEYS.map(k => {
      const key = "fix_" + k;
      return `<div class="fix-bucket ${k}">
        <div class="pct">${pct(key)}%</div>
        <div class="label">${FIX_LABELS[k]}</div>
        <div class="sub">${t[key]} of ${t.fix_linked} linked · ${t.fix_unlinked} unlinked</div>
      </div>`;
    }).join("");
  }

  function render() {
    const exact = filterDaily(false);
    const smoothed = filterDaily(true);
    const t = totals(exact);
    const top4 = topKeys(t, PR_TYPE_KEYS, 4);
    const fixDaily = smoothed.map(d => ({
      date: d.date,
      core: d.fix_core || 0,
      external: d.fix_external || 0,
      bot: d.fix_bot || 0,
    })).filter(d => d.core + d.external + d.bot > 0);

    renderHero(t);
    renderBuckets(t);
    renderLineCounts("#chart-pr-type", smoothed, top4, TYPE_COLORS, null, 340);
    renderLineCounts("#chart-fix-source", fixDaily, FIX_KEYS, FIX_COLORS, FIX_LABELS, 260);
  }

  document.querySelectorAll(".range-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      rangeDays = +btn.dataset.days;
      render();
    });
  });
  document.querySelectorAll(".smooth-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".smooth-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      smoothDays = +btn.dataset.days;
      render();
    });
  });
  document.getElementById("repo-filter").addEventListener("change", e => {
    selectedRepo = e.target.value;
    render();
  });
  document.getElementById("weekends-toggle").addEventListener("change", e => {
    hideWeekends = !e.target.checked;
    render();
  });

  window.addEventListener("resize", () => render());
  render();
})();
