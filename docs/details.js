(async function () {
  // --- HTML escape helper (for any remaining dynamic HTML) ---
  function esc(s) {
    const el = document.createElement("span");
    el.textContent = s;
    return el.innerHTML;
  }

  // --- Parse URL params ---
  const params = new URLSearchParams(window.location.search);
  let dateFrom, dateTo;
  if (params.has("date")) {
    dateFrom = dateTo = params.get("date");
  } else if (params.has("from") && params.has("to")) {
    dateFrom = params.get("from");
    dateTo = params.get("to");
  } else {
    const yesterday = d3.timeFormat("%Y-%m-%d")(d3.timeDay.offset(new Date(), -1));
    dateFrom = dateTo = yesterday;
  }

  document.getElementById("date-from").value = dateFrom;
  document.getElementById("date-to").value = dateTo;

  // --- Page title ---
  function formatTitle(from, to) {
    const fmt = d3.timeFormat("%b %-d, %Y");
    const d1 = new Date(from + "T00:00:00");
    const d2 = new Date(to + "T00:00:00");
    if (from === to) return "Details for " + fmt(d1);
    return "Details for " + d3.timeFormat("%b %-d")(d1) + " – " + fmt(d2);
  }
  document.getElementById("page-title").textContent = formatTitle(dateFrom, dateTo);

  // --- Date picker ---
  document.getElementById("date-update").addEventListener("click", () => {
    const from = document.getElementById("date-from").value;
    const to = document.getElementById("date-to").value;
    if (from && to) {
      const p = new URLSearchParams();
      if (from === to) {
        p.set("date", from);
      } else {
        p.set("from", from);
        p.set("to", to);
      }
      window.location.search = p.toString();
    }
  });

  // --- Collapsible sections ---
  document.querySelectorAll(".detail-section h2").forEach(h2 => {
    h2.addEventListener("click", () => {
      h2.closest(".detail-section").classList.toggle("collapsed");
    });
  });

  // --- Load CSVs ---
  let reworkDetails = [], failureDetails = [], metricDetails = [];
  try {
    reworkDetails = await d3.csv("rework-details.csv", d => ({
      datetime: d.datetime,
      bot: d.bot,
      repo: d.repo,
      item: +d.item,
      url: d.url,
      is_rework: d.is_rework === "true",
    }));
  } catch (e) { /* file may not exist */ }
  try {
    failureDetails = await d3.csv("failure-details.csv", d => ({
      date: d.date,
      workflow: d.workflow,
      repo: d.repo,
      run_id: d.run_id,
      status: d.status,
      url: d.url,
    }));
  } catch (e) { /* file may not exist */ }
  try {
    metricDetails = await d3.csv("metric-details.csv", d => ({
      date: d.date,
      repo: d.repo,
      type: d.type,
      event: d.event,
      number: +d.number,
      title: d.title,
      url: d.url,
    }));
  } catch (e) { /* file may not exist */ }

  // --- Load rework config ---
  let ignoreBots = [];
  try {
    const config = await d3.json("rework-config.json");
    ignoreBots = config.ignoreBots || [];
  } catch (e) { /* config may not exist */ }

  // --- Filter to date range ---
  const inRange = (d) => d >= dateFrom && d <= dateTo;
  const rework = reworkDetails.filter(d =>
    inRange(d.datetime.substring(0, 10)) && !ignoreBots.includes(d.bot)
  );
  const failures = failureDetails.filter(d => inRange(d.date));
  const metrics = metricDetails.filter(d => inRange(d.date));

  // --- DOM table builder helper ---
  function buildRow(cells) {
    const tr = document.createElement("tr");
    cells.forEach(cell => {
      const td = document.createElement("td");
      if (cell.href) {
        const a = document.createElement("a");
        a.href = cell.href;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = cell.text;
        td.appendChild(a);
      } else if (cell.tag) {
        const span = document.createElement("span");
        span.className = "tag tag-" + cell.tagClass;
        span.textContent = cell.tag;
        td.appendChild(span);
      } else {
        td.textContent = cell.text || "";
      }
      tr.appendChild(td);
    });
    return tr;
  }

  // --- Sortable table helper ---
  function makeTable(tableId, data, rowBuilder, defaultSort, defaultDir, highlightFn) {
    const table = document.getElementById(tableId);
    let sortCol = defaultSort;
    let sortDir = defaultDir || "asc";

    function render() {
      const sorted = [...data].sort((a, b) => {
        let va = a[sortCol], vb = b[sortCol];
        if (typeof va === "boolean") { va = va ? 1 : 0; vb = vb ? 1 : 0; }
        if (typeof va === "string") { va = va.toLowerCase(); vb = vb.toLowerCase(); }
        if (va < vb) return sortDir === "asc" ? -1 : 1;
        if (va > vb) return sortDir === "asc" ? 1 : -1;
        return 0;
      });

      const tbody = table.querySelector("tbody");
      tbody.replaceChildren();
      sorted.forEach(d => {
        const tr = rowBuilder(d);
        if (highlightFn && highlightFn(d)) tr.classList.add("row-highlight");
        tbody.appendChild(tr);
      });

      // Update sort arrows
      table.querySelectorAll("th").forEach(th => {
        const arrow = th.querySelector(".sort-arrow");
        if (arrow) arrow.remove();
        if (th.dataset.col === sortCol) {
          const span = document.createElement("span");
          span.className = "sort-arrow";
          span.textContent = sortDir === "asc" ? "▲" : "▼";
          th.appendChild(span);
        }
      });
    }

    table.querySelectorAll("th").forEach(th => {
      th.addEventListener("click", () => {
        const col = th.dataset.col;
        if (sortCol === col) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortCol = col;
          sortDir = "asc";
        }
        render();
      });
    });

    render();
  }

  // --- Rework section ---
  const reworkCount = rework.filter(d => d.is_rework).length;
  const reworkBots = new Set(rework.map(d => d.bot)).size;
  const reworkRate = rework.length > 0 ? (reworkCount / rework.length * 100).toFixed(1) : "0.0";
  document.getElementById("rework-summary").textContent =
    rework.length + " items touched by " + reworkBots + " bots, " + reworkCount + " rework items (" + reworkRate + "%)";

  makeTable("rework-table", rework, d => buildRow([
    { text: d.datetime.substring(0, 19).replace("T", " ") },
    { text: d.bot },
    { text: d.repo },
    { href: d.url, text: "#" + d.item },
    { tag: d.is_rework ? "yes" : "no", tagClass: d.is_rework ? "yes" : "no" },
  ]), "datetime", "asc", d => d.is_rework);

  // --- Failures section ---
  const failCount = failures.filter(d => d.status === "failure").length;
  const failRate = failures.length > 0 ? (failCount / failures.length * 100).toFixed(1) : "0.0";
  document.getElementById("failure-summary").textContent =
    failures.length + " workflow runs, " + failCount + " failures (" + failRate + "%)";

  makeTable("failure-table", failures, d => buildRow([
    { text: d.date },
    { text: d.workflow },
    { text: d.repo },
    { href: d.url, text: d.run_id },
    { tag: d.status, tagClass: d.status },
  ]), "date", "asc", d => d.status === "failure");

  // --- PR & Issue section ---
  const prOpened = metrics.filter(d => d.type === "pr" && d.event === "opened").length;
  const prMerged = metrics.filter(d => d.type === "pr" && d.event === "merged").length;
  const prClosed = metrics.filter(d => d.type === "pr" && d.event === "closed").length;
  const issOpened = metrics.filter(d => d.type === "issue" && d.event === "opened").length;
  const issClosed = metrics.filter(d => d.type === "issue" && d.event === "closed").length;
  document.getElementById("metric-summary").textContent =
    prOpened + " PRs opened, " + prMerged + " merged, " + prClosed + " closed · " +
    issOpened + " issues opened, " + issClosed + " closed";

  makeTable("metric-table", metrics, d => buildRow([
    { text: d.date },
    { text: d.repo },
    { text: d.type.toUpperCase() },
    { tag: d.event, tagClass: d.event },
    { href: d.url, text: "#" + d.number },
    { text: d.title },
  ]), "date", "asc");
})();
