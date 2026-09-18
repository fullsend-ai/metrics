(async function () {
  "use strict";

  function themeColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  let rangeDays = 90;
  let smoothDays = 28;
  let selectedRepo = "__all__";
  let raw = [];
  const tooltip = d3.select("#tooltip");

  function resolveColors() {
    return {
      defect: themeColor("--negative", "#f87171"),
      revert: themeColor("--chart-2", "#f5c842"),
    };
  }

  function parseDate(value) {
    return new Date(`${value}T00:00:00Z`);
  }

  function showTooltip(event, html) {
    tooltip.html(html)
      .style("left", `${event.pageX + 12}px`)
      .style("top", `${event.pageY - 12}px`)
      .style("opacity", 1);
  }

  function hideTooltip() {
    tooltip.style("opacity", 0);
  }

  try {
    raw = await d3.csv("quality.csv", d => ({
      date: d.date,
      repo: d.repo,
      merged: +d.merged_prs,
      defects: +d.defect_prs,
      fixes: +d.fix_prs,
      reverts: Math.max(+d.revert_prs || 0, +d.revert_commits || 0),
    }));
  } catch (error) {
    document.getElementById("hero").innerHTML = "<p>Quality data is temporarily unavailable.</p>";
    document.getElementById("chart-quality").innerHTML = "<p class='subtitle'>Run the daily collector to restore this dashboard.</p>";
    return;
  }

  function dailyRows() {
    const source = selectedRepo === "__all__" ? raw : raw.filter(d => d.repo === selectedRepo);
    const byDate = d3.rollup(source, rows => ({
      merged: d3.sum(rows, d => d.merged),
      defects: d3.sum(rows, d => d.defects),
      fixes: d3.sum(rows, d => d.fixes),
      reverts: d3.sum(rows, d => d.reverts),
    }), d => d.date);
    return Array.from(byDate, ([date, value]) => ({ date, ...value }))
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  function rangeRows(rows) {
    if (!rangeDays || !rows.length) return rows;
    const latest = parseDate(rows[rows.length - 1].date);
    const cutoff = d3.timeDay.offset(latest, -(rangeDays - 1));
    return rows.filter(row => parseDate(row.date) >= cutoff);
  }

  function rates(allRows, visibleRows) {
    return visibleRows.map(row => {
      const current = parseDate(row.date);
      const start = smoothDays > 0 ? d3.timeDay.offset(current, -(smoothDays - 1)) : current;
      const slice = smoothDays > 0
        ? allRows.filter(candidate => {
          const candidateDate = parseDate(candidate.date);
          return candidateDate >= start && candidateDate <= current;
        })
        : [row];
      const merged = d3.sum(slice, item => item.merged);
      return {
        ...row,
        defectRate: merged ? d3.sum(slice, item => item.defects) / merged : 0,
        revertRate: merged ? d3.sum(slice, item => item.reverts) / merged : 0,
      };
    });
  }

  function totals(rows) {
    const merged = d3.sum(rows, row => row.merged);
    const defects = d3.sum(rows, row => row.defects);
    const fixes = d3.sum(rows, row => row.fixes);
    const reverts = d3.sum(rows, row => row.reverts);
    return {
      merged,
      defects,
      fixes,
      reverts,
      defectRate: merged ? defects / merged : 0,
      revertRate: merged ? reverts / merged : 0,
    };
  }

  function calendarWindow(rows, latestDate, startOffset, endOffset) {
    const latest = parseDate(latestDate);
    const start = d3.timeDay.offset(latest, -startOffset);
    const finish = d3.timeDay.offset(latest, -endOffset);
    return rows.filter(row => {
      const date = parseDate(row.date);
      return date <= start && date >= finish;
    });
  }

  function renderHero(total, colors) {
    document.getElementById("hero").innerHTML = `
      <div class="stat"><div class="val">${total.merged}</div><div class="lbl">Merged PRs</div></div>
      <div class="stat"><div class="val" style="color:${colors.defect}">${(total.defectRate * 100).toFixed(1)}%</div><div class="lbl">Defect-labeled rate</div></div>
      <div class="stat"><div class="val" style="color:${colors.revert}">${(total.revertRate * 100).toFixed(1)}%</div><div class="lbl">Revert event rate</div></div>
      <div class="stat"><div class="val-sm">${total.defects} labeled · ${total.reverts} revert</div><div class="lbl">Quality signals</div></div>`;
  }

  function renderChart(rows, colors) {
    const container = d3.select("#chart-quality");
    container.selectAll("*").remove();
    if (!rows.length) {
      container.html("<p class='subtitle'>No data in range.</p>");
      return;
    }
    const width = Math.max(container.node().getBoundingClientRect().width || 900, 640);
    const height = 340;
    const margin = { top: 16, right: 20, bottom: 42, left: 50 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const svg = container.append("svg").attr("viewBox", `0 0 ${width} ${height}`);
    const group = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);
    const x = d3.scaleTime().domain(d3.extent(rows, row => parseDate(row.date))).range([0, innerWidth]);
    const maxRate = Math.max(0.1, d3.max(rows, row => Math.max(row.defectRate, row.revertRate)) || 0.1);
    const y = d3.scaleLinear().domain([0, Math.ceil(maxRate * 100 / 5) * 5 / 100]).nice().range([innerHeight, 0]);

    group.append("g").attr("class", "grid")
      .call(d3.axisLeft(y).ticks(5).tickSize(-innerWidth).tickFormat(""));
    group.append("g").attr("class", "axis").attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(6).tickFormat(d3.timeFormat("%b %d")));
    group.append("g").attr("class", "axis")
      .call(d3.axisLeft(y).ticks(5).tickFormat(value => `${(value * 100).toFixed(0)}%`));

    const series = [
      ["defectRate", "Defect-labeled rate", colors.defect],
      ["revertRate", "Revert event rate", colors.revert],
    ];
    series.forEach(([key, label, color]) => {
      group.append("path").datum(rows).attr("fill", "none").attr("stroke", color)
        .attr("stroke-width", 2.5).attr("aria-label", label)
        .attr("d", d3.line().x(row => x(parseDate(row.date))).y(row => y(row[key])).curve(d3.curveMonotoneX));
    });
    series.forEach(([, label, color], index) => {
      const item = svg.append("g").attr("transform", `translate(${margin.left + index * 145},${height - 8})`);
      item.append("line").attr("x2", 16).attr("stroke", color).attr("stroke-width", 3);
      item.append("text").attr("x", 22).attr("y", 4).text(label);
    });

    const bisect = d3.bisector(row => parseDate(row.date)).center;
    const focus = group.append("g").style("display", "none");
    focus.append("line").attr("class", "chart-focus").attr("y1", 0).attr("y2", innerHeight);
    const overlay = group.append("rect").attr("width", innerWidth).attr("height", innerHeight)
      .attr("fill", "none").attr("pointer-events", "all");
    overlay.on("mouseenter", () => focus.style("display", null))
      .on("mousemove", event => {
        const [mouseX] = d3.pointer(event);
        const index = Math.max(0, Math.min(rows.length - 1, bisect(rows, x.invert(mouseX))));
        const row = rows[index];
        focus.attr("transform", `translate(${x(parseDate(row.date))},0)`);
        const html = `<strong>${row.date}</strong><br>`
          + `Defect-labeled: ${(row.defectRate * 100).toFixed(1)}%<br>`
          + `Revert events: ${(row.revertRate * 100).toFixed(1)}%<br>`
          + `Merged PRs: ${row.merged}`;
        showTooltip(event, html);
      })
      .on("mouseleave", () => { focus.style("display", "none"); hideTooltip(); });
  }

  function renderBreakdown(rows) {
    const latest = rows[rows.length - 1];
    if (!latest) return;
    const current = totals(calendarWindow(rows, latest.date, 0, 29));
    const previous = totals(calendarWindow(rows, latest.date, 30, 59));
    const format = (label, total) => `<div class="quality-row"><span>${label}</span>`
      + `<strong>${(total.defectRate * 100).toFixed(1)}%</strong>`
      + `<strong>${(total.revertRate * 100).toFixed(1)}%</strong>`
      + `<span>${total.merged}</span></div>`;
    document.getElementById("breakdown").innerHTML = format("Current 30 days", current) + format("Previous 30 days", previous);
  }

  function render() {
    const allRows = dailyRows();
    const visibleRows = rangeRows(allRows);
    const colors = resolveColors();
    renderHero(totals(visibleRows), colors);
    renderChart(rates(allRows, visibleRows), colors);
    renderBreakdown(allRows);
  }

  document.querySelectorAll(".range-btn").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll(".range-btn").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    rangeDays = +button.dataset.days;
    render();
  }));
  document.querySelectorAll(".smooth-btn").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll(".smooth-btn").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    smoothDays = +button.dataset.days;
    render();
  }));
  document.getElementById("repo-filter").addEventListener("change", event => {
    selectedRepo = event.target.value;
    render();
  });
  window.addEventListener("resize", render);
  render();
})();
