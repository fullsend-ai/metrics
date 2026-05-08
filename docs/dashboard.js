(async function () {
  "use strict";

  // --- Chart helpers ---
  const margin = { top: 20, right: 30, bottom: 30, left: 50 };
  const tooltip = d3.select("#tooltip");

  function chartDimensions(container) {
    const width = container.node().getBoundingClientRect().width;
    const height = 260;
    return {
      width, height,
      innerW: width - margin.left - margin.right,
      innerH: height - margin.top - margin.bottom,
    };
  }

  function createSvg(container, dims) {
    container.select("svg").remove();
    return container.append("svg")
      .attr("viewBox", `0 0 ${dims.width} ${dims.height}`)
      .append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);
  }

  function xTimeScale(daily, innerW) {
    return d3.scaleTime()
      .domain(d3.extent(daily, d => new Date(d.date)))
      .range([0, innerW]);
  }

  function drawXAxis(g, scale, innerH) {
    g.append("g")
      .attr("class", "axis")
      .attr("transform", `translate(0,${innerH})`)
      .call(d3.axisBottom(scale).ticks(6).tickFormat(d3.timeFormat("%b %d")));
  }

  function yScale(domain) {
    if (useLogScale && domain[1] > 1) {
      return d3.scaleLog().domain([Math.max(domain[0], 0.5), domain[1]]).range([0, 0]).clamp(true);
    }
    return d3.scaleLinear().domain(domain).range([0, 0]);
  }

  function drawYAxis(g, scale) {
    const axis = d3.axisLeft(scale);
    if (useLogScale) {
      axis.ticks(5, "~s");
    } else {
      axis.ticks(5);
    }
    g.append("g")
      .attr("class", "axis")
      .call(axis);
  }

  function drawGrid(g, scale, innerW) {
    g.append("g")
      .attr("class", "grid")
      .call(d3.axisLeft(scale).ticks(5).tickSize(-innerW).tickFormat(""));
  }

  function showTooltip(event, html) {
    tooltip.html(html)
      .style("left", (event.pageX + 12) + "px")
      .style("top", (event.pageY - 12) + "px")
      .style("opacity", 1);
  }

  function hideTooltip() {
    tooltip.style("opacity", 0);
  }

  function doraLine(g, y, value, label, color, innerW) {
    if (value > y.domain()[1]) return;
    g.append("line")
      .attr("class", "dora-line")
      .attr("x1", 0).attr("x2", innerW)
      .attr("y1", y(value)).attr("y2", y(value))
      .attr("stroke", color);
    g.append("text")
      .attr("class", "dora-label")
      .attr("x", innerW - 4).attr("y", y(value) - 4)
      .attr("text-anchor", "end")
      .attr("fill", color)
      .text(label);
  }

  // --- State ---
  let allData = [];
  let rangeDays = 30;
  let selectedRepo = "__all__";
  let hideWeekends = true;
  let useLogScale = true;

  // --- Load CSV ---
  const raw = await d3.csv("metrics.csv", d => ({
    date: d.date,
    repo: d.repo,
    prs_opened: +d.prs_opened,
    prs_merged: +d.prs_merged,
    prs_closed: +d.prs_closed,
    issues_opened: +d.issues_opened,
    issues_closed: +d.issues_closed,
    releases: +d.releases,
    pr_lead_time_median_hours: +d.pr_lead_time_median_hours,
  }));
  allData = raw;

  // --- Populate repo filter ---
  const repos = [...new Set(raw.map(d => d.repo))].sort();
  const repoSelect = d3.select("#repo-filter");
  repos.forEach(r => repoSelect.append("option").attr("value", r).text(r));

  // --- Controls ---
  d3.selectAll(".range-btn").on("click", function () {
    d3.selectAll(".range-btn").classed("active", false);
    d3.select(this).classed("active", true);
    rangeDays = +this.dataset.days;
    render();
  });
  repoSelect.on("change", function () {
    selectedRepo = this.value;
    render();
  });
  d3.select("#weekends-toggle").property("checked", !hideWeekends).on("change", function () {
    hideWeekends = !this.checked;
    render();
  });
  d3.select("#log-toggle").property("checked", useLogScale).on("change", function () {
    useLogScale = this.checked;
    render();
  });

  // --- Helpers ---
  function filterData() {
    let data = allData;
    if (selectedRepo !== "__all__") {
      data = data.filter(d => d.repo === selectedRepo);
    }
    if (rangeDays > 0) {
      const cutoff = d3.timeDay.offset(new Date(), -rangeDays);
      const cutoffStr = d3.timeFormat("%Y-%m-%d")(cutoff);
      data = data.filter(d => d.date >= cutoffStr);
    }
    if (hideWeekends) {
      data = data.filter(d => {
        const day = new Date(d.date + "T00:00:00").getDay();
        return day !== 0 && day !== 6;
      });
    }
    return data;
  }

  function aggregateByDate(data) {
    const byDate = d3.rollup(data, rows => ({
      prs_opened: d3.sum(rows, d => d.prs_opened),
      prs_merged: d3.sum(rows, d => d.prs_merged),
      prs_closed: d3.sum(rows, d => d.prs_closed),
      issues_opened: d3.sum(rows, d => d.issues_opened),
      issues_closed: d3.sum(rows, d => d.issues_closed),
      releases: d3.sum(rows, d => d.releases),
      pr_lead_time_median_hours: d3.median(rows.filter(r => r.pr_lead_time_median_hours > 0), d => d.pr_lead_time_median_hours) || 0,
    }), d => d.date);

    return Array.from(byDate, ([date, vals]) => ({ date, ...vals }))
      .sort((a, b) => a.date.localeCompare(b.date));
  }

  // --- Summary Cards ---
  function renderSummaryCards(daily) {
    const container = d3.select("#summary-cards");
    container.html("");

    if (daily.length === 0) return;

    const today = new Date();
    const weekAgo = d3.timeDay.offset(today, -7);
    const twoWeeksAgo = d3.timeDay.offset(today, -14);
    const weekAgoStr = d3.timeFormat("%Y-%m-%d")(weekAgo);
    const twoWeeksAgoStr = d3.timeFormat("%Y-%m-%d")(twoWeeksAgo);

    const thisWeek = daily.filter(d => d.date >= weekAgoStr);
    const lastWeek = daily.filter(d => d.date >= twoWeeksAgoStr && d.date < weekAgoStr);

    const metrics = [
      { label: "PRs Merged", key: "prs_merged", agg: d3.sum },
      { label: "PRs Opened", key: "prs_opened", agg: d3.sum },
      { label: "Lead Time (h)", key: "pr_lead_time_median_hours", agg: arr => d3.median(arr) || 0, invert: true },
      { label: "Issues Opened", key: "issues_opened", agg: d3.sum },
      { label: "Issues Closed", key: "issues_closed", agg: d3.sum },
      { label: "Releases", key: "releases", agg: d3.sum },
    ];

    metrics.forEach(m => {
      const curr = m.agg(thisWeek.map(d => d[m.key]));
      const prev = m.agg(lastWeek.map(d => d[m.key]));
      const delta = prev > 0 ? ((curr - prev) / prev * 100) : 0;
      const isPositive = m.invert ? delta <= 0 : delta >= 0;

      const card = container.append("div").attr("class", "card");
      card.append("div").attr("class", "label").text(m.label);
      card.append("div").attr("class", "value").text(
        m.key === "pr_lead_time_median_hours" ? curr.toFixed(1) : curr
      );
      if (prev > 0) {
        card.append("div")
          .attr("class", `delta ${isPositive ? "positive" : "negative"}`)
          .text(`${delta >= 0 ? "+" : ""}${delta.toFixed(0)}% vs prev week`);
      }
    });
  }

  // --- Frequency Chart ---
  function renderFrequencyChart(daily) {
    const container = d3.select("#chart-frequency");
    if (daily.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(daily, dims.innerW);
    const maxMerges = d3.max(daily, d => d.prs_merged) || 1;
    const y = yScale([0, maxMerges * 1.15]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    g.append("path")
      .datum(daily)
      .attr("fill", "none")
      .attr("stroke", "var(--chart-1)")
      .attr("stroke-width", 2)
      .attr("d", d3.line()
        .x(d => x(new Date(d.date)))
        .y(d => y(d.prs_merged))
        .curve(d3.curveMonotoneX));

    const withReleases = daily.filter(d => d.releases > 0);
    g.selectAll(".release-dot")
      .data(withReleases)
      .join("circle")
      .attr("class", "release-dot")
      .attr("cx", d => x(new Date(d.date)))
      .attr("cy", d => y(d.prs_merged))
      .attr("r", 5)
      .attr("fill", "var(--chart-2)")
      .attr("stroke", "var(--bg)")
      .attr("stroke-width", 2);

    g.selectAll(".merge-dot")
      .data(daily)
      .join("circle")
      .attr("cx", d => x(new Date(d.date)))
      .attr("cy", d => y(d.prs_merged))
      .attr("r", 3)
      .attr("fill", "var(--chart-1)")
      .attr("opacity", 0)
      .on("mouseover", function (event, d) {
        d3.select(this).attr("opacity", 1).attr("r", 5);
        showTooltip(event, `<strong>${d.date}</strong><br>Merges: ${d.prs_merged}<br>Releases: ${d.releases}`);
      })
      .on("mouseout", function () {
        d3.select(this).attr("opacity", 0).attr("r", 3);
        hideTooltip();
      });

    const legend = g.append("g").attr("transform", `translate(0, -8)`);
    legend.append("line").attr("x1", 0).attr("x2", 16).attr("y1", 0).attr("y2", 0).attr("stroke", "var(--chart-1)").attr("stroke-width", 2);
    legend.append("text").attr("x", 20).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text("Merges/day");
    legend.append("circle").attr("cx", 100).attr("cy", 0).attr("r", 4).attr("fill", "var(--chart-2)");
    legend.append("text").attr("x", 108).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text("Release");
  }

  // --- Lead Time Chart ---
  function renderLeadTimeChart(daily) {
    const container = d3.select("#chart-leadtime");
    const withData = daily.filter(d => d.pr_lead_time_median_hours > 0);
    if (withData.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(withData, dims.innerW);
    const maxH = d3.max(withData, d => d.pr_lead_time_median_hours) || 1;
    const y = yScale([0, Math.max(maxH * 1.15, 48)]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    doraLine(g, y, 1, "Elite (<1h)", "var(--dora-elite)", dims.innerW);
    doraLine(g, y, 24, "High (<1d)", "var(--dora-high)", dims.innerW);
    doraLine(g, y, 168, "Medium (<1w)", "var(--dora-medium)", dims.innerW);

    g.append("path")
      .datum(withData)
      .attr("fill", "var(--chart-3)")
      .attr("fill-opacity", 0.15)
      .attr("d", d3.area()
        .x(d => x(new Date(d.date)))
        .y0(dims.innerH)
        .y1(d => y(d.pr_lead_time_median_hours))
        .curve(d3.curveMonotoneX));

    g.append("path")
      .datum(withData)
      .attr("fill", "none")
      .attr("stroke", "var(--chart-3)")
      .attr("stroke-width", 2)
      .attr("d", d3.line()
        .x(d => x(new Date(d.date)))
        .y(d => y(d.pr_lead_time_median_hours))
        .curve(d3.curveMonotoneX));

    g.selectAll(".lt-dot")
      .data(withData)
      .join("circle")
      .attr("cx", d => x(new Date(d.date)))
      .attr("cy", d => y(d.pr_lead_time_median_hours))
      .attr("r", 3)
      .attr("fill", "var(--chart-3)")
      .attr("opacity", 0)
      .on("mouseover", function (event, d) {
        d3.select(this).attr("opacity", 1).attr("r", 5);
        showTooltip(event, `<strong>${d.date}</strong><br>Median lead time: ${d.pr_lead_time_median_hours.toFixed(1)}h`);
      })
      .on("mouseout", function () {
        d3.select(this).attr("opacity", 0).attr("r", 3);
        hideTooltip();
      });
  }

  // --- PR Volume Chart ---
  function renderPRVolumeChart(daily) {
    const container = d3.select("#chart-pr-volume");
    if (daily.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(daily, dims.innerW);
    const maxPR = d3.max(daily, d => d.prs_opened + d.prs_merged + d.prs_closed) || 1;
    const y = yScale([0, maxPR * 1.15]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    const keys = ["prs_opened", "prs_merged", "prs_closed"];
    const colors = ["var(--chart-4)", "var(--chart-1)", "var(--chart-5)"];
    const labels = ["Opened", "Merged", "Closed"];

    keys.forEach((key, i) => {
      g.append("path")
        .datum(daily)
        .attr("fill", "none")
        .attr("stroke", colors[i])
        .attr("stroke-width", 2)
        .attr("d", d3.line()
          .x(d => x(new Date(d.date)))
          .y(d => y(d[key]))
          .curve(d3.curveMonotoneX));
    });

    keys.forEach((key, i) => {
      g.selectAll(`.pr-dot-${i}`)
        .data(daily)
        .join("circle")
        .attr("cx", d => x(new Date(d.date)))
        .attr("cy", d => y(d[key]))
        .attr("r", 3)
        .attr("fill", colors[i])
        .attr("opacity", 0)
        .on("mouseover", function (event, d) {
          d3.select(this).attr("opacity", 1).attr("r", 5);
          showTooltip(event, `<strong>${d.date}</strong><br>Opened: ${d.prs_opened}<br>Merged: ${d.prs_merged}<br>Closed: ${d.prs_closed}`);
        })
        .on("mouseout", function () {
          d3.select(this).attr("opacity", 0).attr("r", 3);
          hideTooltip();
        });
    });

    const legend = g.append("g").attr("transform", `translate(0, -8)`);
    keys.forEach((_, i) => {
      const offset = i * 80;
      legend.append("line").attr("x1", offset).attr("x2", offset + 16).attr("y1", 0).attr("y2", 0).attr("stroke", colors[i]).attr("stroke-width", 2);
      legend.append("text").attr("x", offset + 20).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text(labels[i]);
    });
  }

  // --- Issue Volume Chart ---
  function renderIssueVolumeChart(daily) {
    const container = d3.select("#chart-issue-volume");
    if (daily.length === 0) { container.html("<p>No data</p>"); return; }
    const dims = chartDimensions(container);
    const g = createSvg(container, dims);
    const x = xTimeScale(daily, dims.innerW);
    const maxIss = d3.max(daily, d => Math.max(d.issues_opened, d.issues_closed)) || 1;
    const y = yScale([0, maxIss * 1.15]).range([dims.innerH, 0]);

    drawGrid(g, y, dims.innerW);
    drawXAxis(g, x, dims.innerH);
    drawYAxis(g, y);

    const keys = ["issues_opened", "issues_closed"];
    const colors = ["var(--chart-4)", "var(--chart-3)"];
    const labels = ["Opened", "Closed"];

    keys.forEach((key, i) => {
      g.append("path")
        .datum(daily)
        .attr("fill", colors[i])
        .attr("fill-opacity", 0.1)
        .attr("d", d3.area()
          .x(d => x(new Date(d.date)))
          .y0(dims.innerH)
          .y1(d => y(d[key]))
          .curve(d3.curveMonotoneX));

      g.append("path")
        .datum(daily)
        .attr("fill", "none")
        .attr("stroke", colors[i])
        .attr("stroke-width", 2)
        .attr("d", d3.line()
          .x(d => x(new Date(d.date)))
          .y(d => y(d[key]))
          .curve(d3.curveMonotoneX));
    });

    keys.forEach((key, i) => {
      g.selectAll(`.iss-dot-${i}`)
        .data(daily)
        .join("circle")
        .attr("cx", d => x(new Date(d.date)))
        .attr("cy", d => y(d[key]))
        .attr("r", 3)
        .attr("fill", colors[i])
        .attr("opacity", 0)
        .on("mouseover", function (event, d) {
          d3.select(this).attr("opacity", 1).attr("r", 5);
          showTooltip(event, `<strong>${d.date}</strong><br>Opened: ${d.issues_opened}<br>Closed: ${d.issues_closed}`);
        })
        .on("mouseout", function () {
          d3.select(this).attr("opacity", 0).attr("r", 3);
          hideTooltip();
        });
    });

    const legend = g.append("g").attr("transform", `translate(0, -8)`);
    keys.forEach((_, i) => {
      const offset = i * 80;
      legend.append("line").attr("x1", offset).attr("x2", offset + 16).attr("y1", 0).attr("y2", 0).attr("stroke", colors[i]).attr("stroke-width", 2);
      legend.append("text").attr("x", offset + 20).attr("y", 4).attr("font-size", "0.6875rem").attr("fill", "var(--muted)").text(labels[i]);
    });
  }

  // --- Repo Table ---
  function renderRepoTable(data) {
    const tbody = d3.select("#repo-table tbody");
    tbody.html("");

    const byRepo = d3.rollup(data, rows => ({
      prs_merged: d3.sum(rows, d => d.prs_merged),
      prs_opened: d3.sum(rows, d => d.prs_opened),
      issues_opened: d3.sum(rows, d => d.issues_opened),
      issues_closed: d3.sum(rows, d => d.issues_closed),
      releases: d3.sum(rows, d => d.releases),
      pr_lead_time_median_hours: d3.median(rows.filter(r => r.pr_lead_time_median_hours > 0), d => d.pr_lead_time_median_hours) || 0,
    }), d => d.repo);

    const rows = Array.from(byRepo, ([repo, vals]) => ({ repo, ...vals }))
      .sort((a, b) => b.prs_merged - a.prs_merged);

    rows.forEach(r => {
      const tr = tbody.append("tr");
      tr.append("td").text(r.repo);
      tr.append("td").text(r.prs_merged);
      tr.append("td").text(r.prs_opened);
      tr.append("td").text(r.issues_opened);
      tr.append("td").text(r.issues_closed);
      tr.append("td").text(r.releases);
      tr.append("td").text(r.pr_lead_time_median_hours.toFixed(1));
    });

    d3.selectAll("#repo-table th").on("click", function () {
      const col = this.dataset.col;
      const sorted = [...rows].sort((a, b) => {
        if (col === "repo") return a.repo.localeCompare(b.repo);
        return b[col] - a[col];
      });
      tbody.html("");
      sorted.forEach(r => {
        const tr = tbody.append("tr");
        tr.append("td").text(r.repo);
        tr.append("td").text(r.prs_merged);
        tr.append("td").text(r.prs_opened);
        tr.append("td").text(r.issues_opened);
        tr.append("td").text(r.issues_closed);
        tr.append("td").text(r.releases);
        tr.append("td").text(r.pr_lead_time_median_hours.toFixed(1));
      });
    });
  }

  // --- Render all ---
  function render() {
    const data = filterData();
    const daily = aggregateByDate(data);
    renderSummaryCards(daily);
    renderFrequencyChart(daily);
    renderLeadTimeChart(daily);
    renderPRVolumeChart(daily);
    renderIssueVolumeChart(daily);
    renderRepoTable(data);
  }

  render();
})();
