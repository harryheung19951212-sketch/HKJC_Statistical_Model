let races = [];
let selectedRaceId = null;
let intervalSeconds = 30;
let countdown = 30;
let raceDayJobTimer = null;
let backfillJobTimer = null;
let currentPredictions = [];
let selectedHorseId = null;
let autoFollowRace = true;
const raceFolderState = { upcoming: true, resulted: false };
const raceDateFolderState = {};
let activeWatchRaceId = null;
let currentView = "race";
const viewDataLoaded = { coverage: false, analytics: false };

const text = {
  scheduled: "\u672a\u958b\u8dd1",
  live: "\u9032\u884c\u4e2d",
  resulted: "\u5df2\u5b8c\u5834",
  loading: "\u8f09\u5165\u4e2d...",
  loadDone: "\u5b8c\u6210",
  loadFailed: "\u8f09\u5165\u5931\u6557",
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function formatPct(value) {
  return `${((Number(value) || 0) * 100).toFixed(1)}%`;
}

function formatNum(value, digits = 2) {
  if (value === null || value === undefined) return "-";
  return Number(value).toFixed(digits);
}

function formatSigned(value, digits = 3) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function evClass(value) {
  return Number(value) > 0 ? "positive" : "negative";
}

function localStatus(status) {
  return text[status] || status || text.scheduled;
}

function localizedHorse(row) {
  return row.display_name || row.horse_name_zh || row.horse_name || row.horse_id || "-";
}

function localizedJockey(row) {
  return row.display_jockey || row.jockey_zh || row.jockey || "-";
}

function localizedTrainer(row) {
  return row.display_trainer || row.trainer_zh || row.trainer || "-";
}

function hkjcDateInputValue(value) {
  const textValue = String(value || "").trim();
  const match = textValue.match(/^(\d{4})[/-](\d{2})[/-](\d{2})$/);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : textValue;
}

function hkjcDisplayDate(value) {
  const textValue = String(value || "").trim();
  const match = textValue.match(/^(\d{4})[/-](\d{2})[/-](\d{2})$/);
  return match ? `${match[1]}/${match[2]}/${match[3]}` : textValue || "-";
}

function requiredDateValue(id) {
  const value = hkjcDateInputValue($(id).value);
  if (!value) throw new Error("請先選擇日期");
  $(id).value = value;
  return value;
}

async function loadState() {
  const state = await api("/api/state");
  intervalSeconds = Number(state.odds_interval_seconds || 30);
  const active = state.active_race_id ? `｜使用中：${raceNoLabel(state.active_race_id)}` : "｜未有打開場次";
  $("system-status").textContent = `每 ${intervalSeconds} 秒只刷新已打開場次 ${active}`;
  if (!selectedRaceId && state.current_race_id) selectedRaceId = state.current_race_id;
  const lifecycle = await api("/api/lifecycle");
  renderLifecycle(lifecycle);
  if (autoFollowRace && selectedRaceId && lifecycle.next_scheduled_race_id) {
    const selected = races.find((race) => race.race_id === selectedRaceId);
    if (selected && selected.status === "resulted" && lifecycle.next_scheduled_race_id !== selectedRaceId) {
      sleepSelectedRace();
      selectedRaceId = lifecycle.next_scheduled_race_id;
      selectedHorseId = null;
      activeWatchRaceId = null;
    }
  }
}

async function loadRaces() {
  races = await api("/api/races");
  if (!selectedRaceId && races.length) selectedRaceId = races[0].race_id;
  renderRaceList();
  renderRaceHeader();
}

function renderRaceList() {
  const list = $("race-list");
  list.innerHTML = "";
  const grouped = {
    upcoming: races.filter((race) => race.status !== "resulted"),
    resulted: races.filter((race) => race.status === "resulted"),
  };
  [
    ["upcoming", "未進行"],
    ["resulted", "已完賽"],
  ].forEach(([key, label]) => {
    const folder = document.createElement("details");
    folder.className = `race-folder race-folder-${key}`;
    folder.open = Boolean(raceFolderState[key] || grouped[key].some((race) => race.race_id === selectedRaceId));
    folder.addEventListener("toggle", () => {
      raceFolderState[key] = folder.open;
    });

    const summary = document.createElement("summary");
    summary.innerHTML = `<span>${label}</span><b>${grouped[key].length}</b>`;
    folder.appendChild(summary);

    const byDate = groupByRaceDate(grouped[key]);
    Object.keys(byDate).sort((a, b) => key === "resulted" ? b.localeCompare(a) : a.localeCompare(b)).forEach((date) => {
      folder.appendChild(renderRaceDateFolder(key, date, byDate[date]));
    });
    list.appendChild(folder);
  });
}

function renderRaceDateFolder(statusKey, date, items) {
  const key = `${statusKey}:${date}`;
  const folder = document.createElement("details");
  folder.className = "race-date-folder";
  const containsSelected = items.some((race) => race.race_id === selectedRaceId);
  folder.open = raceDateFolderState[key] ?? (statusKey === "upcoming" || containsSelected);
  folder.addEventListener("toggle", () => {
    raceDateFolderState[key] = folder.open;
  });

  const summary = document.createElement("summary");
  const trackNames = [...new Set(items.map((race) => localTrack(race.track)).filter(Boolean))].join(" / ");
  summary.innerHTML = `<span>${formatDateLabel(date)} ${trackNames}</span><b>${items.length}</b>`;
  folder.appendChild(summary);

  items.forEach((race) => {
    const item = document.createElement("button");
    item.className = `race-item ${race.race_id === selectedRaceId ? "active" : ""}`;
    item.innerHTML = `
      <strong>${raceNoLabel(race.race_id)}</strong>
      <span>${localTrack(race.track)} ${race.distance_m}\u7c73 | ${localStatus(race.status)}</span>
    `;
    item.addEventListener("click", () => {
      if (activeWatchRaceId && activeWatchRaceId !== race.race_id) sleepSelectedRace();
      switchAppView("race");
      selectedRaceId = race.race_id;
      autoFollowRace = false;
      activeWatchRaceId = null;
      countdown = intervalSeconds;
      renderRaceList();
      refreshSelectedRace();
    });
    folder.appendChild(item);
  });
  return folder;
}

function groupByRaceDate(items) {
  return items.reduce((groups, race) => {
    const date = race.date || "未有日期";
    if (!groups[date]) groups[date] = [];
    groups[date].push(race);
    return groups;
  }, {});
}

function formatDateLabel(value) {
  const textValue = String(value || "");
  const match = textValue.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return textValue;
  return `${Number(match[2])}月${Number(match[3])}日`;
}

function raceNoLabel(raceId) {
  const match = String(raceId || "").match(/-(\d{2})$/);
  return match ? `第 ${Number(match[1])} 場` : raceId;
}

function renderLifecycle(data) {
  const counts = data.counts || {};
  $("lifecycle-mode").textContent = data.mode === "active_race_only" ? "只更新已打開場次" : "-";
  $("lifecycle-current").textContent = data.active_race_id || "-";
  $("lifecycle-frozen").textContent = data.frozen_race_id || "-";
  $("lifecycle-resulted").textContent = counts.resulted || 0;
}

function selectedRace() {
  return races.find((race) => race.race_id === selectedRaceId);
}

async function switchAppView(view) {
  currentView = view;
  document.querySelectorAll(".app-view").forEach((item) => {
    item.classList.toggle("active", item.id === `${view}-view`);
  });
  document.querySelectorAll(".page-nav").forEach((item) => {
    item.classList.toggle("active", item.dataset.view === view);
  });

  if (view !== "race") {
    sleepSelectedRace();
  }
  if (view === "race") {
    activeWatchRaceId = null;
    countdown = 1;
  } else if (view === "coverage" && !viewDataLoaded.coverage) {
    await refreshCoverage();
    viewDataLoaded.coverage = true;
  } else if (view === "analytics" && !viewDataLoaded.analytics) {
    await refreshModelReports({ includeCoverage: false });
    viewDataLoaded.analytics = true;
  }
}

function renderRaceHeader() {
  const race = selectedRace();
  if (!race) return;
  $("race-status").textContent = localStatus(race.status);
  $("race-title").textContent = `${race.race_id}`;
  $("race-meta").textContent = formatRaceMeta(race);
  $("runner-count").textContent = race.runners || 0;
  $("odds-count").textContent = `${race.odds_ticks || 0} / ${race.exotic_dividends || 0}`;
  $("last-refresh").textContent = race.last_odds_refresh_at || "-";
  $("feed-status").textContent = race.notes ? `賠率來源：${race.notes}` : "";
  $("feed-source").textContent = race.notes || "-";
  const isResulted = race.status === "resulted";
  $("mark-live").disabled = isResulted;
  $("mark-scheduled").disabled = isResulted;
  $("refresh-now").disabled = isResulted || race.status === "live";
}

async function watchSelectedRace() {
  if (!selectedRaceId || document.hidden) return null;
  const payload = await api(`/api/watch-race?race_id=${encodeURIComponent(selectedRaceId)}`, { method: "POST" });
  if (payload.active_race_id) activeWatchRaceId = payload.active_race_id;
  return payload;
}

function sleepSelectedRace() {
  if (!activeWatchRaceId) return;
  const raceId = encodeURIComponent(activeWatchRaceId);
  activeWatchRaceId = null;
  if (navigator.sendBeacon) {
    navigator.sendBeacon(`/api/sleep-race?race_id=${raceId}`);
    return;
  }
  api(`/api/sleep-race?race_id=${raceId}`, { method: "POST", keepalive: true }).catch(() => {});
}

async function refreshSelectedRace(options = {}) {
  if (!selectedRaceId) return;
  const full = Boolean(options.full);
  await watchSelectedRace();
  await loadRaces();
  const payload = await api(`/api/predictions?race_id=${encodeURIComponent(selectedRaceId)}`);
  currentPredictions = payload.predictions || [];
  if (!selectedHorseId && currentPredictions.length) selectedHorseId = currentPredictions[0].horse_id;
  if (!currentPredictions.some((row) => row.horse_id === selectedHorseId)) {
    selectedHorseId = currentPredictions.length ? currentPredictions[0].horse_id : null;
  }
  renderPredictions(currentPredictions);
  renderRunnerDetail(currentPredictions.find((row) => row.horse_id === selectedHorseId));
  const betting = await api(`/api/betting?race_id=${encodeURIComponent(selectedRaceId)}&bankroll=${encodeURIComponent(bettingBankroll())}&risk=${encodeURIComponent(bettingRisk())}`);
  renderBetting(betting);
  const ledger = await api(`/api/betting-ledger?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderBettingLedger(ledger);
  if (full) await refreshModelReports();
  const feed = await api(`/api/odds-feed?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderOddsFeed(feed);
  const comparison = await api(`/api/model-comparison?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderModelComparison(comparison);
  const history = await api(`/api/odds-history?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderOddsHistory(history);
  const results = await api(`/api/results?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderResults(results.results || [], results.place_odds_completeness);
  const weather = await api(`/api/weather?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderWeather(weather);
}

async function refreshModelReports(options = {}) {
  const includeCoverage = options.includeCoverage !== false;
  const backtest = await api("/api/backtest");
  renderBacktest(backtest);
  const evolution = await api("/api/evolution");
  renderEvolution(evolution);
  const taxonomy = await api("/api/error-taxonomy");
  renderErrorTaxonomy(taxonomy);
  const modelVersions = await api("/api/model-versions");
  renderModelVersions(modelVersions);
  const registry = await api("/api/model-registry");
  renderModelRegistry(registry);
  const quality = await api("/api/data-quality");
  renderDataQuality(quality);
  if (includeCoverage) {
    const coverage = await api("/api/coverage");
    renderCoverage(coverage);
    viewDataLoaded.coverage = true;
  }
  viewDataLoaded.analytics = true;
}

function renderWeather(weather) {
  const box = $("weather-status");
  if (!weather || weather.status !== "ok") {
    box.textContent = weather && weather.message ? weather.message : "";
    return;
  }
  const update = weather.update_time ? `｜更新 ${weather.update_time}` : "";
  box.textContent = `賽日天氣：${weather.summary}${update}`;
}

function bettingBankroll() {
  return Math.max(Number($("bankroll-input").value || 0), 0);
}

function bettingRisk() {
  return $("risk-profile").value || "standard";
}

function renderBetting(data) {
  const summary = $("betting-summary");
  const box = $("betting-tickets");
  const settings = data.risk_settings || {};
  const poolCount = Object.keys(data.pool_rules || {}).length;
  summary.innerHTML = `
    <div><label>本場上限</label><strong>${formatMoney(data.max_race_stake)}</strong></div>
    <div><label>建議總注</label><strong>${formatMoney(data.total_recommended_stake)}</strong></div>
    <div><label>Kelly</label><strong>${formatPct(settings.fractional_kelly)}</strong></div>
    <div><label>\u5f69\u6c60\u6210\u672c\u6a21\u578b</label><strong>${poolCount || "-"} \u500b</strong></div>
    <div><label>期望值門檻</label><strong>${formatPct(settings.min_expected_value)}</strong></div>
    <div><label>狀態</label><strong>${localStatus(data.race_status)}</strong></div>
  `;
  const tickets = data.tickets || [];
  const exoticHtml = renderExoticSection(data.exotic_candidates || [], data.upgrade_paths || []);
  if (!tickets.length) {
    const top = (data.decisions || []).slice(0, 4);
    box.innerHTML = `
      <div class="betting-empty">未有符合風險條件嘅下注建議</div>
      ${top.map(renderDecisionCard).join("")}
      ${exoticHtml}
    `;
    return;
  }
  box.innerHTML = `${tickets.slice(0, 8).map(renderDecisionCard).join("")}${exoticHtml}`;
}

function renderDecisionCard(row) {
  return `
    <div class="ticket ${ticketClass(row.action)}">
      <div class="ticket-main">
        <span>${row.market_label}</span>
        <strong>${row.horse_no || "-"} ${localizedHorse(row)}</strong>
        <small>模型第 ${row.model_rank} ｜ ${row.reason}</small>
      </div>
      <div class="ticket-metrics">
        <div><label>機率</label><strong>${formatPct(row.probability)}</strong></div>
        <div><label>賠率</label><strong>${formatNum(row.odds, 2)}</strong></div>
        <div><label>公允</label><strong>${formatNum(row.fair_odds, 2)}</strong></div>
        <div><label>價值差</label><strong class="${evClass(row.edge)}">${formatPct(row.edge)}</strong></div>
        <div><label>期望值</label><strong class="${evClass(row.expected_value)}">${row.expected_value === null ? "-" : Number(row.expected_value).toFixed(3)}</strong></div>
        <div><label>\u6263\u6210\u672cEV</label><strong class="${evClass(row.cost_adjusted_expected_value)}">${formatSigned(row.cost_adjusted_expected_value, 3)}</strong></div>
        <div><label>\u6240\u9700\u8ce0\u7387</label><strong>${formatNum(row.required_dividend, 2)}</strong></div>
        <div><label>\u62bd\u6c34/\u566a\u97f3</label><strong>${formatPoolCost(row.pool_rule)}</strong></div>
        <div><label>注碼</label><strong>${formatMoney(row.recommended_stake)}</strong></div>
      </div>
      <div class="ticket-action">${row.action}</div>
    </div>
  `;
}

function ticketClass(action) {
  if (action === "有值博") return "bet";
  if (action === "觀望") return "watch";
  if (action === "只供回測" || action === "停止下注") return "review";
  return "avoid";
}

function renderExoticSection(candidates, upgradePaths) {
  if (!candidates.length) return "";
  const markets = ["QPL", "TRIO", "QIN", "FCT", "TCE", "FIRST4", "QUARTET"];
  const selected = [];
  markets.forEach((market) => {
    selected.push(...candidates.filter((row) => row.market === market).slice(0, 2));
  });
  return `
    <div class="exotic-section">
      <div class="exotic-head">
        <strong>組合投注候選</strong>
        <span>先睇模型機率、打和派彩同官方即時派彩；有值先轉下注建議</span>
      </div>
      <div class="upgrade-list">
        ${(upgradePaths || []).slice(0, 3).map((path) => `
          <div class="upgrade-card">
            <span>${path.from_markets.join(" / ")} 位置Q基礎</span>
            <strong>升級考慮：${path.to_label} ${path.to_market}</strong>
            <small>中獎率 ${formatPct(path.trio_probability)}｜打和派彩 ${formatNum(path.trio_break_even_dividend, 2)} 倍</small>
          </div>
        `).join("")}
      </div>
      <div class="exotic-grid">
        ${selected.map(renderExoticCard).join("")}
      </div>
    </div>
  `;
}

function renderExoticCard(row) {
  return `
    <div class="exotic-card">
      <div>
        <span>${row.market_label}</span>
        <strong>${row.combination}</strong>
        <small>${row.reason}</small>
      </div>
      <div class="exotic-metrics">
        <label>中獎率 <b>${formatPct(row.probability)}</b></label>
        <label>打和派彩 <b>${formatNum(row.break_even_dividend, 2)}x</b></label>
        <label>\u6240\u9700\u6d3e\u5f69 <b>${formatNum(row.required_dividend, 2)}x</b></label>
        <label>官方/估算 <b>${formatNum(row.dividend, 2)}x</b></label>
        <label>期望值 <b class="${evClass(row.expected_value)}">${row.expected_value === null || row.expected_value === undefined ? "-" : Number(row.expected_value).toFixed(3)}</b></label>
        <label>\u6263\u6210\u672cEV <b class="${evClass(row.cost_adjusted_expected_value)}">${formatSigned(row.cost_adjusted_expected_value, 3)}</b></label>
      </div>
    </div>
  `;
}

function formatPoolCost(rule) {
  if (!rule) return "-";
  return `${formatPct(rule.takeout_rate)} / ${formatPct(rule.efficiency_buffer)}`;
}

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `$${number.toFixed(1)}`;
}

function formatRaceMeta(race) {
  return `${hkjcDisplayDate(race.date)}｜${localTrack(race.track)}｜${localCourse(race.course)}｜${race.distance_m}米｜${localGoing(race.going)}｜${localClass(race.class_rating)}`;
}

function localTrack(value) {
  const textValue = String(value || "");
  const map = {
    "Sha Tin": "沙田",
    "Happy Valley": "跑馬地",
    ST: "沙田",
    HV: "跑馬地",
  };
  return map[textValue] || textValue;
}

function localCourse(value) {
  const textValue = String(value || "");
  const lower = textValue.toLowerCase();
  if (lower.includes("all weather")) return "全天候跑道";
  if (lower.includes("turf")) return "草地";
  if (lower.includes("course")) return textValue.replace(/Course/gi, "跑道");
  return textValue || "-";
}

function localGoing(value) {
  const textValue = String(value || "");
  const normalized = textValue.toLowerCase();
  const ordered = [
    ["good to firm", "好至快地"],
    ["good to yielding", "好至黏地"],
    ["yielding to soft", "黏至軟地"],
    ["good", "好地"],
    ["firm", "快地"],
    ["yielding", "黏地"],
    ["soft", "軟地"],
    ["wet", "濕慢地"],
  ];
  const found = ordered.find(([needle]) => normalized.includes(needle));
  return found ? found[1] : textValue || "-";
}

function localClass(value) {
  const textValue = String(value || "");
  const match = textValue.match(/Class\s*(\d+)/i);
  if (match) return `第${match[1]}班`;
  return textValue || "-";
}

function renderPredictions(predictions) {
  const body = $("prediction-body");
  body.innerHTML = "";
  predictions.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.className = `clickable ${row.horse_id === selectedHorseId ? "selected" : ""}`;
    tr.innerHTML = `
      <td>${index + 1}</td>
      <td>${row.horse_no || "-"}</td>
      <td><strong>${localizedHorse(row)}</strong><br><span>${row.horse_id}</span></td>
      <td>${row.draw || "-"}</td>
      <td>${localizedJockey(row)}</td>
      <td>${localizedTrainer(row)}</td>
      <td>${formatPct(row.win_probability)}</td>
      <td>${formatPct(row.top3_probability)}</td>
      <td>${formatNum(row.latest_win_odds, 2)}</td>
      <td>${formatPct(row.market_probability)}</td>
      <td class="${evClass(row.expected_value)}">${row.expected_value === null ? "-" : Number(row.expected_value).toFixed(3)}</td>
      <td class="${evClass(row.value_gap)}">${formatPct(row.value_gap)}</td>
      <td>${formatNum(row.place_odds, 2)}<br><span>${placeOddsSourceLabel(row.place_odds_source)}</span></td>
      <td class="${evClass(row.top3_expected_value)}">${row.top3_expected_value === null ? "-" : Number(row.top3_expected_value).toFixed(3)}</td>
      <td class="${evClass(row.top3_value_gap)}">${formatPct(row.top3_value_gap)}</td>
    `;
    tr.addEventListener("click", () => {
      selectedHorseId = row.horse_id;
      renderPredictions(currentPredictions);
      renderRunnerDetail(row);
    });
    body.appendChild(tr);
  });
}

function renderResults(results, completeness = null) {
  const body = $("results-body");
  const summary = $("results-summary");
  body.innerHTML = "";
  if (!results.length) {
    summary.textContent = "未有賽果";
    body.innerHTML = `<tr><td colspan="14" class="empty-cell">未有賽果</td></tr>`;
    return;
  }
  summary.textContent = resultSummaryText(results, completeness);
  results.forEach((row) => {
    const tr = document.createElement("tr");
    const position = Number(row.finish_position);
    tr.className = position === 1 ? "winner" : position <= 3 ? "placed" : "";
    tr.innerHTML = `
      <td>${row.finish_position || "-"}</td>
      <td>${row.horse_no || "-"}</td>
      <td><strong>${localizedHorse(row)}</strong><br><span>${row.horse_id}</span></td>
      <td>${row.draw || "-"}</td>
      <td>${localizedJockey(row)}</td>
      <td>${localizedTrainer(row)}</td>
      <td>${formatRaceTime(row.finish_time_sec)}</td>
      <td>${formatMargin(row.margin_lengths)}</td>
      <td>${formatNum(row.win_odds, 2)}</td>
      <td>${formatNum(row.final_place_odds, 2)}</td>
      <td class="${evClass(row.top3_expected_value)}">${row.top3_expected_value === null || row.top3_expected_value === undefined ? "-" : Number(row.top3_expected_value).toFixed(3)}</td>
      <td>${row.prediction_rank || "-"}</td>
      <td>${formatPct(row.win_probability)}</td>
      <td>${formatPct(row.top3_probability)}</td>
    `;
    body.appendChild(tr);
  });
}

function renderRunnerDetail(row) {
  const box = $("runner-detail");
  if (!row) {
    box.innerHTML = `<p class="runner-subtitle">\u8acb\u5148\u9078\u64c7\u4e00\u5339\u99ac</p>`;
    return;
  }
  const explanation = row.explanation || { positive: [], negative: [] };
  box.innerHTML = `
    <p class="runner-title">${localizedHorse(row)}</p>
    <p class="runner-subtitle">
      \u99ac\u865f ${row.horse_no || "-"} | \u6a94\u4f4d ${row.draw || "-"} | ${row.horse_id}<br>
      \u9a0e\u5e2b\uff1a${localizedJockey(row)} | \u7df4\u99ac\u5e2b\uff1a${localizedTrainer(row)}<br>
      \u7368\u8d0f\u52dd\u7387\uff1a${formatPct(row.win_probability)} | \u5165\u4e09\u7532\uff1a${formatPct(row.top3_probability)} | \u4f4d\u7f6e\u671f\u671b\u503c\uff1a${row.top3_expected_value === null ? "-" : Number(row.top3_expected_value).toFixed(3)}<br>
      \u4e09\u7532\u4f86\u6e90\uff1a${top3SourceLabel(row.top3_model_source)}
    </p>
    <div class="stat"><label>\u5165\u4e09\u7532\u6a5f\u7387</label><strong>${formatPct(row.top3_probability)}</strong></div>
    <div class="stat"><label>\u7368\u8d0f\u8ce0\u7387</label><strong>${formatNum(row.latest_win_odds, 2)}</strong></div>
    <div class="stat"><label>\u7368\u8d0f\u671f\u671b\u503c</label><strong class="${evClass(row.expected_value)}">${row.expected_value === null ? "-" : Number(row.expected_value).toFixed(3)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u8ce0\u7387</label><strong>${formatNum(row.place_odds, 2)} ${placeOddsSourceLabel(row.place_odds_source)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u5e02\u5834</label><strong>${formatPct(row.place_market_probability)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u671f\u671b\u503c</label><strong class="${evClass(row.top3_expected_value)}">${row.top3_expected_value === null ? "-" : Number(row.top3_expected_value).toFixed(3)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u50f9\u503c\u5dee</label><strong class="${evClass(row.top3_value_gap)}">${formatPct(row.top3_value_gap)}</strong></div>
    <div class="stat"><label>5分鐘賠率流</label><strong class="${evClass(row.odds_delta_5m)}">${formatSigned(row.odds_delta_5m)}</strong></div>
    <div class="stat"><label>2分鐘賠率流</label><strong class="${evClass(row.odds_delta_2m)}">${formatSigned(row.odds_delta_2m)}</strong></div>
    <div class="stat"><label>30秒賠率流</label><strong class="${evClass(row.odds_delta_30s)}">${formatSigned(row.odds_delta_30s)}</strong></div>
    <div class="stat"><label>熱捧/轉冷</label><strong>${formatSigned(row.late_steam)} / ${formatSigned(row.late_drift)}</strong></div>
    <div class="stat"><label>同日內檔偏差</label><strong class="${evClass(row.same_day_inside_bias)}">${formatSigned(row.same_day_inside_bias)}</strong></div>
    <div class="stat"><label>同日外檔偏差</label><strong class="${evClass(row.same_day_outside_bias)}">${formatSigned(row.same_day_outside_bias)}</strong></div>
    <div class="stat"><label>同日跑法偏差</label><strong class="${evClass(row.same_day_pace_bias)}">${formatSigned(row.same_day_pace_bias)}</strong></div>
    <div class="factor-list">
      ${renderFactorSection("\u6b63\u9762\u56e0\u7d20", explanation.positive || [], "positive")}
      ${renderFactorSection("\u8ca0\u9762\u56e0\u7d20", explanation.negative || [], "negative")}
    </div>
  `;
}

function top3SourceLabel(value) {
  if (value === "independent_top3_model") return "獨立三甲模型";
  return "獨贏排名推算";
}

function formatRaceTime(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "-";
  const minutes = Math.floor(value / 60);
  const rest = value - minutes * 60;
  return `${minutes}:${rest.toFixed(2).padStart(5, "0")}`;
}

function formatMargin(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number === 0 ? "頭馬" : number.toFixed(2);
}

function placeOddsSourceLabel(value) {
  if (value === "hkjc_mqtt") return "MQTT";
  if (value === "hkjc_graphql") return "官方";
  if (value === "hkjc_results_final") return "賽後";
  if (value === "hkjc_final_place_snapshot") return "最後位置";
  if (value === "hkjc_final_place_backfill") return "官方補抓";
  return "-";
}

function renderOddsFeed(feed) {
  const box = $("feed-health");
  const summary = $("feed-health-summary");
  if (!feed || !feed.runner_count) {
    summary.textContent = "未有參賽馬資料";
    box.innerHTML = `<div class="feed-empty">未有參賽馬資料</div>`;
    return;
  }
  summary.textContent = feedSummaryText(feed);
  $("feed-status").textContent = feedStatusText(feed);
  const rows = feed.runners || [];
  box.innerHTML = `
    <div class="feed-cards">
      <div><label>獨贏覆蓋</label><strong>${feed.win_covered}/${feed.runner_count}</strong></div>
      <div><label>位置覆蓋</label><strong>${feed.place_covered}/${feed.runner_count}</strong></div>
      <div><label>官方 ticks</label><strong>${feed.official_tick_count}</strong></div>
      <div><label>Live ticks</label><strong>${feed.live_tick_count}</strong></div>
      <div><label>最新官方時間</label><strong>${formatTimestamp(feed.latest_official_timestamp)}</strong></div>
      <div><label>訓練狀態</label><strong class="${feed.training_ready ? "positive" : "negative"}">${feed.training_ready ? "可用" : "未完整"}</strong></div>
    </div>
    <div class="table-wrap">
      <table class="feed-table">
        <thead>
          <tr>
            <th>馬號</th>
            <th>馬匹</th>
            <th>獨贏 tick</th>
            <th>最新獨贏</th>
            <th>位置 tick</th>
            <th>最新位置</th>
            <th>位置來源</th>
            <th>缺口</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(renderFeedRunnerRow).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderModelComparison(data) {
  const box = $("model-comparison");
  const summary = $("comparison-summary");
  const runners = data && data.runners ? data.runners : [];
  if (!runners.length) {
    summary.textContent = "未有對照資料";
    box.innerHTML = `<div class="comparison-empty">未有對照資料</div>`;
    return;
  }
  const info = data.summary || {};
  summary.textContent = comparisonSummaryText(info);
  box.innerHTML = `
    <div class="comparison-cards">
      <div><label>純能力首選</label><strong>${info.top_ability_name || "-"}</strong></div>
      <div><label>市場融合首選</label><strong>${info.top_market_name || "-"}</strong></div>
      <div><label>首選一致</label><strong class="${info.top_pick_same ? "positive" : "negative"}">${info.top_pick_same ? "一致" : "不同"}</strong></div>
      <div><label>最大排名擺動</label><strong>${info.max_rank_swing || 0}</strong></div>
    </div>
    <div class="table-wrap">
      <table class="comparison-table">
        <thead>
          <tr>
            <th>馬號</th>
            <th>馬匹</th>
            <th>純能力排名</th>
            <th>市場排名</th>
            <th>變化</th>
            <th>純能力勝率</th>
            <th>市場勝率</th>
            <th>獨贏賠率</th>
            <th>EV</th>
          </tr>
        </thead>
        <tbody>
          ${runners.map(renderComparisonRow).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderComparisonRow(row) {
  const swingClass = Math.abs(Number(row.rank_delta || 0)) >= 3 ? "comparison-swing" : "";
  return `
    <tr class="${swingClass}">
      <td>${row.horse_no || "-"}</td>
      <td><strong>${localizedHorse(row)}</strong><br><span>${row.horse_id}</span></td>
      <td>${row.ability_rank || "-"}</td>
      <td>${row.market_rank || "-"}</td>
      <td class="${rankDeltaClass(row.rank_delta)}">${formatRankDelta(row.rank_delta)}</td>
      <td>${formatPct(row.ability_win_probability)}</td>
      <td>${formatPct(row.market_win_probability)}</td>
      <td>${formatNum(row.latest_win_odds, 2)}</td>
      <td class="${evClass(row.expected_value)}">${row.expected_value === null || row.expected_value === undefined ? "-" : Number(row.expected_value).toFixed(3)}</td>
    </tr>
  `;
}

function comparisonSummaryText(info) {
  const same = info.top_pick_same ? "首選一致" : "首選不同";
  return `${same} | 擺動 ${info.rank_disagreement || 0} | 移除市場特徵 ${(info.market_features_removed || []).length}`;
}

function formatRankDelta(value) {
  const number = Number(value || 0);
  if (!number) return "-";
  return `${number > 0 ? "+" : ""}${number}`;
}

function rankDeltaClass(value) {
  const number = Number(value || 0);
  if (number > 0) return "positive";
  if (number < 0) return "negative";
  return "";
}

function renderFeedRunnerRow(row) {
  const missing = [
    row.missing_win_tick ? "獨贏" : "",
    row.missing_place_tick ? "位置" : "",
  ].filter(Boolean).join(" / ") || "-";
  return `
    <tr class="${missing === "-" ? "" : "feed-missing"}">
      <td>${row.horse_no || "-"}</td>
      <td><strong>${localizedHorse(row)}</strong><br><span>${row.horse_id}</span></td>
      <td>${row.win_tick_count || 0}</td>
      <td>${formatNum(row.latest_win_odds, 2)}<br><span>${sourceLabel(row.latest_win_source)}</span></td>
      <td>${row.place_tick_count || 0}</td>
      <td>${formatNum(row.latest_place_odds, 2)}<br><span>${formatTimestamp(row.latest_place_timestamp)}</span></td>
      <td>${sourceLabel(row.final_place_source || row.latest_place_source)}</td>
      <td>${missing}</td>
    </tr>
  `;
}

function feedSummaryText(feed) {
  const status = feedStatusLabel(feed.status);
  const stale = feed.is_stale ? " | 已過期" : "";
  return `${status} | WIN ${feed.win_covered}/${feed.runner_count} | PLA ${feed.place_covered}/${feed.runner_count}${stale}`;
}

function feedStatusText(feed) {
  const place = feed.place_odds_completeness || {};
  const sourceCounts = Object.entries(feed.source_counts || {})
    .map(([source, count]) => `${sourceLabel(source)} ${count}`)
    .join(" / ");
  const pieces = [
    `賠率錄影：${feedStatusLabel(feed.status)}`,
    `WIN ${feed.win_covered}/${feed.runner_count}`,
    `PLA ${feed.place_covered}/${feed.runner_count}`,
  ];
  if (feed.lifecycle_status === "resulted") {
    pieces.push(`最後位置 ${place.place_odds_count || 0}/${place.runner_count || feed.runner_count}`);
  }
  if (sourceCounts) pieces.push(sourceCounts);
  return pieces.join(" | ");
}

function feedStatusLabel(value) {
  const labels = {
    recording_complete: "錄影完整",
    training_ready: "訓練可用",
    incomplete_final_place: "最後位置未齊",
    partial: "錄影未齊",
    stale: "來源過期",
    no_official_ticks: "未有官方 tick",
    missing_runners: "缺參賽馬",
  };
  return labels[value] || value || "-";
}

function sourceLabel(value) {
  if (value === "hkjc_mqtt") return "MQTT";
  if (value === "hkjc_graphql") return "官方";
  if (value === "hkjc_results_final") return "賽果";
  if (value === "hkjc_final_place_snapshot") return "最後位置";
  if (value === "hkjc_final_place_backfill") return "官方補抓";
  return "-";
}

function resultSummaryText(results, completeness) {
  const total = results.length;
  if (!completeness || !completeness.runner_count) return `${total} 匹馬`;
  const count = Number(completeness.place_odds_count || 0);
  const runnerCount = Number(completeness.runner_count || total);
  if (count >= runnerCount) return `${total} 匹馬 | 位置賠率 ${count}/${runnerCount}`;
  return `${total} 匹馬 | 位置賠率 ${count}/${runnerCount} | 缺 ${runnerCount - count} 匹（官方歷史賠率未補齊）`;
}

function renderFactorSection(title, factors, kind) {
  if (!factors.length) {
    return `<div class="runner-subtitle">${title}\uff1a-</div>`;
  }
  const maxAbs = Math.max(...factors.map((factor) => Math.abs(Number(factor.contribution) || 0)), 0.0001);
  return `
    <div class="runner-subtitle">${title}</div>
    ${factors.map((factor) => {
      const contribution = Number(factor.contribution) || 0;
      const width = Math.max(6, Math.min(100, Math.abs(contribution) / maxAbs * 100));
      return `
        <div class="factor">
          <span>${factor.label}</span>
          <span class="factor-bar"><span class="factor-fill ${kind}" style="width:${width}%"></span></span>
          <span class="factor-value">${contribution.toFixed(3)}</span>
        </div>
      `;
    }).join("")}
  `;
}

function renderBacktest(data) {
  $("backtest").innerHTML = `
    <div class="stat"><label>\u7368\u8d0f\u6ce8\u6578</label><strong>${data.bets}</strong></div>
    <div class="stat"><label>\u7368\u8d0f\u547d\u4e2d</label><strong>${data.wins}</strong></div>
    <div class="stat"><label>\u7368\u8d0f\u547d\u4e2d\u7387</label><strong>${formatPct(data.hit_rate)}</strong></div>
    <div class="stat"><label>\u7368\u8d0f\u56de\u5831\u7387</label><strong class="${evClass(data.roi)}">${formatPct(data.roi)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u6ce8\u6578</label><strong>${data.place_bets}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u547d\u4e2d</label><strong>${data.place_wins}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u547d\u4e2d\u7387</label><strong>${formatPct(data.place_hit_rate)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u56de\u5831\u7387</label><strong class="${evClass(data.place_roi)}">${formatPct(data.place_roi)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u6295\u6ce8\u984d</label><strong>${formatNum(data.place_staked, 2)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u6d3e\u5f69</label><strong>${formatNum(data.place_returned, 2)}</strong></div>
  `;
}

function renderEvolution(report) {
  const metrics = report.metrics || {};
  $("evolution-summary").innerHTML = `
    <div class="stat"><label>已評估場次</label><strong>${metrics.races || 0}</strong></div>
    <div class="stat"><label>首選命中率</label><strong>${formatPct(metrics.top_pick_hit_rate)}</strong></div>
    <div class="stat"><label>頭馬平均勝率</label><strong>${formatPct(metrics.mean_winner_probability)}</strong></div>
    <div class="stat"><label>對數損失</label><strong>${formatNum(metrics.log_loss, 3)}</strong></div>
    <div class="stat"><label>布萊爾分數</label><strong>${formatNum(metrics.brier_score, 3)}</strong></div>
    <div class="stat"><label>價值回報率</label><strong class="${evClass(metrics.value_roi)}">${formatPct(metrics.value_roi)}</strong></div>
  `;
  renderCalibration(report.calibration || []);
  renderEvolutionIdeas(report.ideas || [], report.data_quality || []);
  renderDiagnostics(report.diagnostics || []);
  if (report.model_versions) renderModelVersions(report.model_versions);
}

function renderModelVersions(data) {
  const summary = data.summary || {};
  $("version-recommendation").textContent = summary.recommendation || "未有足夠資料比較模型版本。";
  const versions = data.versions || [];
  if (!versions.length) {
    $("model-versions").innerHTML = `<p class="runner-subtitle">未有模型版本結果</p>`;
    return;
  }
  $("model-versions").innerHTML = versions.map((version, index) => {
    const metrics = version.metrics || {};
    return `
      <div class="version-card ${index === 0 ? "best" : ""}">
        <div class="version-head">
          <strong>${index + 1}. ${version.label}</strong>
          <span>${version.variant_id}</span>
        </div>
        <p>${version.description}</p>
        <div class="version-metrics">
          <div><label>驗證場次</label><b>${metrics.races || 0}</b></div>
          <div><label>首選</label><b>${formatPct(metrics.top_pick_hit_rate)}</b></div>
          <div><label>對數損失</label><b>${formatNum(metrics.log_loss, 3)}</b></div>
          <div><label>布萊爾</label><b>${formatNum(metrics.brier_score, 3)}</b></div>
          <div><label>頭馬排名</label><b>${formatNum(metrics.avg_winner_rank, 1)}</b></div>
          <div><label>價值回報率</label><b class="${evClass(metrics.value_roi)}">${formatPct(metrics.value_roi)}</b></div>
        </div>
        <span class="version-verdict">${version.verdict}</span>
      </div>
    `;
  }).join("");
}

function renderModelRegistry(data) {
  const summary = data.summary || {};
  $("model-registry-summary").innerHTML = `
    <div class="stat"><label>已保存評估</label><strong>${summary.run_count || 0}</strong></div>
    <div class="stat"><label>最新 Gate</label><strong>${summary.latest_gate_label || "-"}</strong></div>
    <div class="stat"><label>升級候選</label><strong>${summary.upgrade_candidates || 0}</strong></div>
  `;
  $("model-registry-clv").textContent = data.clv_status || "";
  const runs = data.runs || [];
  if (!runs.length) {
    $("model-registry-runs").innerHTML = `<p class="runner-subtitle">未保存任何 out-of-sample 評估</p>`;
    return;
  }
  $("model-registry-runs").innerHTML = runs.slice(0, 8).map((run) => `
    <div class="registry-card ${run.promotion_gate === "upgrade_candidate" ? "candidate" : ""}">
      <div class="version-head">
        <strong>${formatTimestamp(run.created_at)}</strong>
        <span>${run.promotion_gate_label || run.promotion_gate}</span>
      </div>
      <p>最佳版本：${run.best_label || "-"}｜驗證折數：${run.folds || 0}｜賽事：${run.race_count || 0}</p>
      <div class="version-metrics">
        <div><label>最佳 Log Loss</label><b>${formatNum(run.best_log_loss, 3)}</b></div>
        <div><label>Baseline Log Loss</label><b>${formatNum(run.baseline_log_loss, 3)}</b></div>
        <div><label>最佳 ROI</label><b class="${evClass(run.best_value_roi)}">${formatPct(run.best_value_roi)}</b></div>
        <div><label>最大回撤</label><b>${formatNum(run.best_max_drawdown, 1)}</b></div>
      </div>
      <span class="version-verdict">${run.recommendation || "-"}</span>
    </div>
  `).join("");
}

async function runModelRegistry() {
  $("system-status").textContent = "OOS 評估保存中...";
  const result = await api("/api/model-registry/run", { method: "POST" });
  renderModelRegistry(await api("/api/model-registry"));
  if (result.report) renderModelVersions(result.report);
  $("system-status").textContent = `OOS 評估已保存：${(result.run || {}).promotion_gate_label || "-"}`;
}

function renderBettingLedger(data) {
  const summary = data.summary || {};
  $("betting-ledger-summary").innerHTML = `
    <div class="stat"><label>建議數</label><strong>${summary.recommendations || 0}</strong></div>
    <div class="stat"><label>已對數</label><strong>${summary.reconciled || 0}</strong></div>
    <div class="stat"><label>未對數</label><strong>${summary.pending || 0}</strong></div>
    <div class="stat"><label>實際回報率</label><strong class="${evClass(summary.roi)}">${formatPct(summary.roi)}</strong></div>
    <div class="stat"><label>命中率</label><strong>${formatPct(summary.hit_rate)}</strong></div>
    <div class="stat"><label>平均 CLV</label><strong class="${evClass(summary.avg_clv)}">${summary.avg_clv === null || summary.avg_clv === undefined ? "-" : formatPct(summary.avg_clv)}</strong></div>
  `;
  $("betting-ledger-note").textContent = data.clv_note || "";
  const items = data.items || [];
  if (!items.length) {
    $("betting-ledger-list").innerHTML = `<p class="runner-subtitle">本場未有已保存投注建議</p>`;
    return;
  }
  $("betting-ledger-list").innerHTML = items.slice(0, 10).map((row) => `
    <div class="ledger-card ${row.reconciliation_status === "reconciled" ? "reconciled" : ""}">
      <div class="version-head">
        <strong>${row.market_label || row.market}｜${row.horse_no || "-"} ${row.horse_name || row.horse_id}</strong>
        <span>${row.reconciliation_status === "reconciled" ? "已對數" : "未對數"}</span>
      </div>
      <p>${formatTimestamp(row.created_at)}｜${row.reason || ""}</p>
      <div class="version-metrics">
        <div><label>建議賠率</label><b>${formatNum(row.recommended_odds, 2)}</b></div>
        <div><label>最後賠率</label><b>${formatNum(row.final_odds, 2)}</b></div>
        <div><label>期望值</label><b class="${evClass(row.expected_value)}">${formatSigned(row.expected_value, 3)}</b></div>
        <div><label>注碼</label><b>${formatMoney(row.recommended_stake)}</b></div>
        <div><label>盈虧</label><b class="${evClass(row.profit)}">${formatMoney(row.profit)}</b></div>
        <div><label>CLV</label><b class="${evClass(row.clv)}">${row.clv === null || row.clv === undefined ? "-" : formatPct(row.clv)}</b></div>
      </div>
    </div>
  `).join("");
}

async function reconcileBettingLedger() {
  if (!selectedRaceId) return;
  $("system-status").textContent = "投注留痕對數中...";
  const result = await api(`/api/betting-ledger/reconcile?race_id=${encodeURIComponent(selectedRaceId)}`, { method: "POST" });
  renderBettingLedger(result.ledger || {});
  $("system-status").textContent = `投注留痕已對數：${result.updated || 0} 筆`;
}

function renderDataQuality(data) {
  const totals = data.totals || {};
  $("data-quality").innerHTML = `
    <div><label>質素分</label><strong>${formatPct(data.quality_score)}</strong></div>
    <div><label>賽事</label><strong>${totals.races || 0}</strong></div>
    <div><label>已賽</label><strong>${data.resulted_races || 0}</strong></div>
    <div><label>馬匹</label><strong>${totals.runners || 0}</strong></div>
    <div><label>賽果</label><strong>${totals.results || 0}</strong></div>
    <div><label>賠率</label><strong>${totals.odds_ticks || 0}</strong></div>
  `;
  const issues = data.issues || [];
  $("quality-issues").innerHTML = issues.map((issue) => `
    <div class="issue ${issue.severity}">
      <span>${issue.label}</span>
      <strong>${issue.count || 0}</strong>
    </div>
  `).join("");
}

async function refreshCoverage() {
  const coverage = await api("/api/coverage");
  renderCoverage(coverage);
  viewDataLoaded.coverage = true;
}

function renderCoverage(data) {
  const summary = data.summary || {};
  const counts = summary.status_counts || {};
  const database = summary.database || {};
  $("coverage-objective").textContent = data.objective || "";
  $("coverage-summary").innerHTML = `
    <div><label>覆蓋分</label><strong>${formatPct(summary.coverage_score)}</strong></div>
    <div><label>總項目</label><strong>${summary.total_groups || 0}</strong></div>
    <div><label>主分析項</label><strong>${summary.core_groups || 0}</strong></div>
    <div><label>額外盲點</label><strong>${summary.blind_spot_groups || 0}</strong></div>
    <div><label>已完成</label><strong>${counts["已完成"] || 0}</strong></div>
    <div><label>部分完成</label><strong>${counts["部分完成"] || 0}</strong></div>
    <div><label>未完成</label><strong>${counts["未完成"] || 0}</strong></div>
    <div><label>外部數據</label><strong>${counts["需要外部數據"] || 0}</strong></div>
    <div><label>賽事資料</label><strong>${database.races || 0}</strong></div>
    <div><label>馬匹資料</label><strong>${database.runners || 0}</strong></div>
    <div><label>賽果資料</label><strong>${database.results || 0}</strong></div>
    <div><label>賠率 ticks</label><strong>${database.odds_ticks || 0}</strong></div>
  `;
  $("coverage-priority").innerHTML = (data.priority_next_steps || []).map((item) => `
    <div class="coverage-priority-card">
      <div class="coverage-card-head">
        <strong>${item.id}. ${item.name}</strong>
        <span class="coverage-tag">${item.status}</span>
      </div>
      <p>${item.model_risk}</p>
      <p><b>下一步</b>：${item.next_steps}</p>
      <p><b>驗證</b>：${item.validation_gate}</p>
    </div>
  `).join("");
  $("coverage-list").innerHTML = (data.items || []).map(renderCoverageCard).join("");
}

function renderCoverageCard(item) {
  return `
    <details class="coverage-card status-${item.status_key}">
      <summary class="coverage-card-head">
        <strong>${item.id}. ${item.name}</strong>
        <span class="coverage-tag">${item.status}</span>
      </summary>
      <p>${item.current_support}</p>
      <div class="coverage-meta">
        <span><b>類別</b>：${item.category_label}</span>
        <span><b>資料來源</b>：${listText(item.data_sources)}</span>
        <span><b>支援檔案</b>：${listText(item.supported_files)}</span>
        <span><b>缺口</b>：${item.gaps}</span>
        <span><b>下一步</b>：${item.next_steps}</span>
        <span><b>模型風險</b>：${item.model_risk}</span>
        <span><b>驗證門檻</b>：${item.validation_gate}</span>
      </div>
    </details>
  `;
}

function listText(values) {
  const items = values || [];
  return items.length ? items.join(" / ") : "未接入";
}

async function repairData() {
  $("repair-message").textContent = "修復中...";
  const result = await api("/api/repair-data", { method: "POST" });
  const repair = result.repair || {};
  $("repair-message").textContent = `已補回 ${repair.inserted_runners || 0} 匹缺失馬匹，並重新訓練模型。`;
  renderDataQuality(result.quality || {});
  if (result.model_versions) renderModelVersions(result.model_versions);
  await refreshSelectedRace({ full: true });
}

async function completeRunners() {
  $("completion-message").textContent = "補完中...";
  const result = await api("/api/complete-runners", { method: "POST" });
  const completion = result.completion || {};
  $("completion-message").textContent = `已檢查 ${completion.races_checked || 0} 場，更新 ${completion.updated_runners || 0} 匹馬。`;
  renderDataQuality(result.quality || {});
  if (result.model_versions) renderModelVersions(result.model_versions);
  await refreshSelectedRace({ full: true });
}

function renderCalibration(rows) {
  $("calibration-bins").innerHTML = rows.map((row) => {
    const expected = Number(row.avg_prediction) || 0;
    const observed = Number(row.observed_rate) || 0;
    const gap = Number(row.gap) || 0;
    const expectedWidth = Math.max(3, Math.min(100, expected * 100));
    const observedWidth = Math.max(3, Math.min(100, observed * 100));
    return `
      <div class="calibration-row">
        <div class="calibration-head">
          <strong>${row.label}</strong>
          <span>${row.count || 0} 匹 | 差距 <b class="${evClass(gap)}">${formatPct(gap)}</b></span>
        </div>
        <div class="calibration-track"><span style="width:${expectedWidth}%"></span></div>
        <div class="calibration-track observed"><span style="width:${observedWidth}%"></span></div>
      </div>
    `;
  }).join("");
}

function renderEvolutionIdeas(ideas, notes) {
  const noteHtml = notes.map((note) => `<p class="quality-note">${note}</p>`).join("");
  const ideaHtml = ideas.map((idea) => `
    <div class="idea">
      <strong>${idea.title}</strong>
      <p>${idea.reason}</p>
      <span>${idea.action}</span>
    </div>
  `).join("");
  $("evolution-ideas").innerHTML = noteHtml + ideaHtml;
}

function renderDiagnostics(rows) {
  if (!rows.length) {
    $("race-diagnostics").innerHTML = `<p class="runner-subtitle">未有明顯錯誤診斷</p>`;
    return;
  }
  $("race-diagnostics").innerHTML = rows.map((row) => `
    <div class="diagnostic">
      <strong>${row.race_id}</strong>
      <p>頭馬：${row.winner_horse_no || "-"} ${row.winner} | 模型排名 ${row.winner_rank} | 勝率 ${formatPct(row.winner_probability)}</p>
      <p>首選：${row.top_pick_horse_no || "-"} ${row.top_pick} | 跑第 ${row.top_pick_finish || "-"}</p>
      <span>${(row.tags || []).join(" / ")}</span>
    </div>
  `).join("");
}

function renderErrorTaxonomy(data) {
  const summary = data.summary || {};
  $("error-taxonomy-summary").innerHTML = `
    <div class="stat"><label>已覆核場次</label><strong>${summary.reviewed_races || 0}</strong></div>
    <div class="stat"><label>錯誤標籤</label><strong>${summary.review_count || 0}</strong></div>
    <div class="stat"><label>高嚴重</label><strong>${summary.high_severity || 0}</strong></div>
    <div class="stat"><label>中嚴重</label><strong>${summary.medium_severity || 0}</strong></div>
  `;
  const recommendations = data.recommendations || [];
  const latest = data.reviews || [];
  $("error-taxonomy-categories").innerHTML = `
    ${(data.categories || []).map((row) => `
      <div class="taxonomy-card">
        <div class="taxonomy-head">
          <strong>${row.label}</strong>
          <span>${row.count} 次</span>
        </div>
        <p>${row.sample_evidence}</p>
      </div>
    `).join("")}
    ${recommendations.length ? `<div class="taxonomy-section-title">下一步實驗焦點</div>` : ""}
    ${recommendations.map((row) => `
      <div class="taxonomy-card focus">
        <div class="taxonomy-head">
          <strong>${row.label}</strong>
          <span>${row.count} 次</span>
        </div>
        <p>${row.next_step}</p>
      </div>
    `).join("")}
    ${latest.length ? `<div class="taxonomy-section-title">最近覆核</div>` : ""}
    ${latest.slice(0, 8).map((row) => `
      <div class="taxonomy-card ${row.severity}">
        <div class="taxonomy-head">
          <strong>${row.race_id}｜${row.category_label}</strong>
          <span>${row.severity}</span>
        </div>
        <p>${row.horse_no || "-"} ${localizedHorse(row)}｜模型第 ${row.model_rank || "-"}｜跑第 ${row.finish_position || "-"}</p>
        <p>${row.evidence}</p>
      </div>
    `).join("")}
  `;
}

async function refreshErrorTaxonomy() {
  $("system-status").textContent = "錯誤分類更新中...";
  const taxonomy = await api("/api/error-taxonomy?refresh=1");
  renderErrorTaxonomy(taxonomy);
  $("system-status").textContent = `錯誤分類已更新：${(taxonomy.summary || {}).review_count || 0} 個標籤`;
}

async function runGptIteration() {
  $("gpt-iteration-notes").textContent = "Codex 分析中...";
  const result = await api("/api/gpt-iteration", { method: "POST" });
  if (result.status === "done") {
    $("gpt-iteration-notes").textContent = result.notes || "-";
    renderEvolution(result.report || {});
    return;
  }
  if (result.status === "not_configured") {
    $("gpt-iteration-notes").textContent = "未設定 AI 引擎。已先顯示本地規則產生嘅迭代建議。";
    renderEvolution(result.report || {});
    return;
  }
  $("gpt-iteration-notes").textContent = `Codex 分析失敗：${result.message || "未知錯誤"}`;
  renderEvolution(result.report || {});
}

function renderOddsHistory(rows) {
  const latest = rows.slice(0, 12);
  renderOddsChart(rows);
  $("odds-history").innerHTML = latest.map((row) => `
    <div class="history-row">
      <span><b>${localizedHorse(row)}</b><br>馬號 ${row.horse_no || "-"} | ${formatTimestamp(row.timestamp)}</span>
      <strong>${formatNum(row.win_odds, 2)}</strong>
    </div>
  `).join("");
}

function formatTimestamp(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").replace(/\.\d+/, "").replace(/\+00:00$/, "");
}

function renderOddsChart(rows) {
  const svg = $("odds-chart");
  const byHorse = new Map();
  const labels = new Map();
  rows.slice().reverse().forEach((row) => {
    if (!byHorse.has(row.horse_id)) byHorse.set(row.horse_id, []);
    byHorse.get(row.horse_id).push(row);
    if (!labels.has(row.horse_id)) {
      labels.set(row.horse_id, localizedHorse(row));
    }
  });
  const series = [...byHorse.entries()]
    .map(([horseId, items]) => [horseId, items.slice(-12)])
    .filter(([, items]) => items.length >= 2)
    .slice(0, 5);
  if (!series.length) {
    svg.innerHTML = `<text x="18" y="96">\u672a\u6709\u8db3\u5920\u8ce0\u7387\u8a18\u9304</text>`;
    return;
  }
  const values = series.flatMap(([, items]) => items.map((item) => Number(item.win_odds))).filter(Number.isFinite);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = 24;
  const width = 360;
  const height = 190;
  const colors = ["#0f7a5b", "#1f5d9b", "#a23b2a", "#7a5b10", "#5d4e9b"];
  const y = (value) => {
    if (max === min) return height / 2;
    return height - pad - ((value - min) / (max - min)) * (height - pad * 2);
  };
  const lineFor = (items) => items.map((item, index) => {
    const x = pad + (index / Math.max(items.length - 1, 1)) * (width - pad * 2);
    return `${x.toFixed(1)},${y(Number(item.win_odds)).toFixed(1)}`;
  }).join(" ");
  const grid = `
    <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}" stroke="#d9dee7"/>
    <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#d9dee7"/>
    <text x="${pad}" y="16">${max.toFixed(1)}</text>
    <text x="${pad}" y="${height - 6}">${min.toFixed(1)}</text>
  `;
  const lines = series.map(([horseId, items], index) => `
    <polyline fill="none" stroke="${colors[index % colors.length]}" stroke-width="2" points="${lineFor(items)}"/>
    <text x="${width - 78}" y="${20 + index * 15}" fill="${colors[index % colors.length]}">${shortLabel(labels.get(horseId) || horseId, 5)}</text>
  `).join("");
  svg.innerHTML = grid + lines;
}

function shortLabel(value, limit) {
  const textValue = String(value || "");
  return textValue.length > limit ? `${textValue.slice(0, limit)}...` : textValue;
}

async function manualRefreshOdds() {
  if (!selectedRaceId) return;
  const raceKey = encodeURIComponent(selectedRaceId);
  const result = await api(`/api/refresh-odds?race_id=${raceKey}`, { method: "POST" });
  const exotic = await api(`/api/refresh-exotic-dividends?race_id=${raceKey}`, { method: "POST" });
  if (result.status === "frozen" || exotic.status === "frozen") {
    $("system-status").textContent = "賽事已開跑或完場，保留最後實時賠率";
  } else if (result.status === "error" || exotic.status === "error") {
    const message = [result.error, exotic.error].filter(Boolean).join(" | ");
    $("system-status").textContent = `賠率/派彩未完整更新：${message}`;
  } else {
    $("system-status").textContent = `已更新賠率 ${result.inserted || 0} 筆，組合派彩 ${exotic.inserted || 0} 筆`;
  }
  countdown = intervalSeconds;
  await refreshSelectedRace();
}

async function lifecycleStep() {
  const result = await api("/api/lifecycle-step", { method: "POST" });
  if (autoFollowRace && result.next_race_id && result.next_race_id !== selectedRaceId) {
    sleepSelectedRace();
    selectedRaceId = result.next_race_id;
    selectedHorseId = null;
    activeWatchRaceId = null;
  }
  $("system-status").textContent = result.race_id ? `全域流程已處理：${result.race_id}` : "未有未開跑場次";
  countdown = intervalSeconds;
  await loadState();
  if (currentView === "race") {
    await refreshSelectedRace();
  } else if (currentView === "coverage") {
    await refreshCoverage();
  } else if (currentView === "analytics") {
    await refreshModelReports({ includeCoverage: false });
  }
}

async function markLive() {
  if (!selectedRaceId) return;
  const result = await api(`/api/mark-live?race_id=${encodeURIComponent(selectedRaceId)}`, { method: "POST" });
  $("system-status").textContent = result.status === "resulted"
    ? "已完場賽事不能改為開跑，最後賠率會保留作訓練材料"
    : "本場已凍結，保留最後實時賠率";
  await loadState();
  await refreshSelectedRace();
}

async function markScheduled() {
  if (!selectedRaceId) return;
  const result = await api(`/api/mark-scheduled?race_id=${encodeURIComponent(selectedRaceId)}`, { method: "POST" });
  if (result.status !== "resulted") autoFollowRace = true;
  $("system-status").textContent = result.status === "resulted"
    ? "已完場賽事不能改回未開跑，避免污染賽果及最後賠率"
    : "本場已恢復未開跑刷新";
  await loadState();
  await refreshSelectedRace();
}

async function refreshResults() {
  if (!selectedRaceId) return;
  const result = await api(`/api/refresh-results?race_id=${encodeURIComponent(selectedRaceId)}`, { method: "POST" });
  if (result.status === "resulted") {
    const lifecycle = await api("/api/lifecycle");
    if (autoFollowRace && lifecycle.next_scheduled_race_id) {
      sleepSelectedRace();
      selectedRaceId = lifecycle.next_scheduled_race_id;
      selectedHorseId = null;
      activeWatchRaceId = null;
    }
  }
  await refreshSelectedRace();
}

async function loadRaceDay(event) {
  event.preventDefault();
  let date;
  try {
    date = requiredDateValue("race-date");
  } catch (error) {
    $("race-day-message").textContent = error.message;
    return;
  }
  const venue = $("race-venue").value;
  const raceCount = Number($("race-count").value || 10);
  $("race-day-message").textContent = text.loading;
  const job = await api(`/api/load-race-day?date=${encodeURIComponent(date)}&venue=${encodeURIComponent(venue)}&races=${raceCount}`, { method: "POST" });
  watchRaceDayJob(job.job_id);
}

function watchRaceDayJob(jobId) {
  if (raceDayJobTimer) clearInterval(raceDayJobTimer);
  raceDayJobTimer = setInterval(async () => {
    const job = await api(`/api/job?id=${encodeURIComponent(jobId)}`);
    const current = job.current || 0;
    const total = job.total || 0;
    $("race-day-message").textContent = `${text.loading} ${current}/${total}`;
    if (job.status === "done") {
      clearInterval(raceDayJobTimer);
      const result = job.result || {};
      $("race-day-message").textContent = `${text.loadDone}\uff1a${result.imported_races || 0} \u5834\uff0c${result.errors || 0} \u500b\u932f\u8aa4`;
      selectedRaceId = result.first_race_id || selectedRaceId;
      activeWatchRaceId = null;
      await refreshSelectedRace({ full: true });
    }
    if (job.status === "error") {
      clearInterval(raceDayJobTimer);
      $("race-day-message").textContent = `${text.loadFailed}\uff1a${job.error || ""}`;
    }
  }, 1000);
}

async function loadBackfill(event) {
  event.preventDefault();
  let start;
  let end;
  try {
    start = requiredDateValue("backfill-start");
    end = requiredDateValue("backfill-end");
  } catch (error) {
    $("backfill-message").textContent = error.message;
    return;
  }
  const venue = $("backfill-venue").value;
  const raceCount = Number($("backfill-races").value || 10);
  const epochs = Number($("backfill-epochs").value || 120);
  $("backfill-message").textContent = "回填中...";
  const job = await api(
    `/api/backfill?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&venue=${encodeURIComponent(venue)}&races=${raceCount}&epochs=${epochs}`,
    { method: "POST" },
  );
  watchBackfillJob(job.job_id);
}

function watchBackfillJob(jobId) {
  if (backfillJobTimer) clearInterval(backfillJobTimer);
  backfillJobTimer = setInterval(async () => {
    const job = await api(`/api/job?id=${encodeURIComponent(jobId)}`);
    const current = job.current || 0;
    const total = job.total || 0;
    $("backfill-message").textContent = `回填中... ${current}/${total} ${job.message || ""}`;
    if (job.status === "done") {
      clearInterval(backfillJobTimer);
      const result = job.result || {};
      const training = result.training || {};
      $("backfill-message").textContent = `完成：${result.imported_races || 0} 場，${result.imported_results || 0} 份賽果，重訓 ${training.training_races || 0} 場`;
      if (result.first_race_id) selectedRaceId = result.first_race_id;
      activeWatchRaceId = null;
      await refreshSelectedRace({ full: true });
    }
    if (job.status === "error") {
      clearInterval(backfillJobTimer);
      $("backfill-message").textContent = `回填失敗：${job.error || ""}`;
    }
  }, 1200);
}

async function boot() {
  document.querySelectorAll(".page-nav").forEach((button) => {
    button.addEventListener("click", () => {
      switchAppView(button.dataset.view || "race");
    });
  });
  $("refresh-now").addEventListener("click", manualRefreshOdds);
  $("refresh-results").addEventListener("click", refreshResults);
  $("lifecycle-step").addEventListener("click", lifecycleStep);
  $("mark-live").addEventListener("click", markLive);
  $("mark-scheduled").addEventListener("click", markScheduled);
  $("bankroll-input").addEventListener("change", refreshSelectedRace);
  $("risk-profile").addEventListener("change", refreshSelectedRace);
  $("run-gpt-iteration").addEventListener("click", runGptIteration);
  $("run-model-registry").addEventListener("click", runModelRegistry);
  $("reconcile-betting-ledger").addEventListener("click", reconcileBettingLedger);
  $("repair-data").addEventListener("click", repairData);
  $("complete-runners").addEventListener("click", completeRunners);
  $("refresh-error-taxonomy").addEventListener("click", refreshErrorTaxonomy);
  $("race-day-form").addEventListener("submit", loadRaceDay);
  $("backfill-form").addEventListener("submit", loadBackfill);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      sleepSelectedRace();
      $("system-status").textContent = "畫面休眠中，暫停即時刷新";
      return;
    }
    activeWatchRaceId = null;
    countdown = 1;
  });
  window.addEventListener("beforeunload", sleepSelectedRace);
  await loadState();
  await loadRaces();
  await refreshSelectedRace({ full: false });
  countdown = intervalSeconds;
  setInterval(async () => {
    if (document.hidden || currentView !== "race") {
      countdown = intervalSeconds;
      return;
    }
    countdown -= 1;
    if (countdown <= 0) {
      countdown = intervalSeconds;
      await loadState();
      await refreshSelectedRace({ full: false });
    }
    $("countdown").textContent = countdown;
  }, 1000);
}

boot().catch((error) => {
  $("system-status").textContent = `\u932f\u8aa4\uff1a${error.message}`;
});
