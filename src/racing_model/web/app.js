let races = [];
let selectedRaceId = null;
let intervalSeconds = 30;
let countdown = 30;
let raceDayJobTimer = null;
let backfillJobTimer = null;
let currentPredictions = [];
let selectedHorseId = null;
let autoFollowRace = true;

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

async function loadState() {
  const state = await api("/api/state");
  intervalSeconds = Number(state.odds_interval_seconds || 30);
  $("system-status").textContent = `\u6bcf ${intervalSeconds} \u79d2\u81ea\u52d5\u5237\u65b0\u8ce0\u7387`;
  if (!selectedRaceId && state.current_race_id) selectedRaceId = state.current_race_id;
  const lifecycle = await api("/api/lifecycle");
  renderLifecycle(lifecycle);
  if (autoFollowRace && selectedRaceId && lifecycle.refreshable_race_id) {
    const selected = races.find((race) => race.race_id === selectedRaceId);
    if (selected && selected.status === "resulted" && lifecycle.refreshable_race_id !== selectedRaceId) {
      selectedRaceId = lifecycle.refreshable_race_id;
      selectedHorseId = null;
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
  for (const race of races) {
    const item = document.createElement("button");
    item.className = `race-item ${race.race_id === selectedRaceId ? "active" : ""}`;
    item.innerHTML = `<strong>${race.race_id}</strong><span>${localTrack(race.track)} ${race.distance_m}\u7c73 | ${localStatus(race.status)}</span>`;
    item.addEventListener("click", () => {
      selectedRaceId = race.race_id;
      autoFollowRace = false;
      countdown = intervalSeconds;
      renderRaceList();
      refreshSelectedRace();
    });
    list.appendChild(item);
  }
}

function renderLifecycle(data) {
  const counts = data.counts || {};
  $("lifecycle-mode").textContent = data.mode === "auto_refresh_scheduled_only" ? "只更新未開跑" : "-";
  $("lifecycle-current").textContent = data.refreshable_race_id || "-";
  $("lifecycle-frozen").textContent = data.frozen_race_id || "-";
  $("lifecycle-resulted").textContent = counts.resulted || 0;
}

function selectedRace() {
  return races.find((race) => race.race_id === selectedRaceId);
}

function renderRaceHeader() {
  const race = selectedRace();
  if (!race) return;
  $("race-status").textContent = localStatus(race.status);
  $("race-title").textContent = `${race.race_id}`;
  $("race-meta").textContent = formatRaceMeta(race);
  $("runner-count").textContent = race.runners || 0;
  $("odds-count").textContent = race.odds_ticks || 0;
  $("last-refresh").textContent = race.last_odds_refresh_at || "-";
  $("feed-status").textContent = race.notes ? `賠率來源：${race.notes}` : "";
  $("feed-source").textContent = race.notes || "-";
  const isResulted = race.status === "resulted";
  $("mark-live").disabled = isResulted;
  $("mark-scheduled").disabled = isResulted;
  $("refresh-now").disabled = isResulted || race.status === "live";
}

async function refreshSelectedRace() {
  if (!selectedRaceId) return;
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
  const backtest = await api("/api/backtest");
  renderBacktest(backtest);
  const evolution = await api("/api/evolution");
  renderEvolution(evolution);
  const modelVersions = await api("/api/model-versions");
  renderModelVersions(modelVersions);
  const quality = await api("/api/data-quality");
  renderDataQuality(quality);
  const coverage = await api("/api/coverage");
  renderCoverage(coverage);
  const history = await api(`/api/odds-history?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderOddsHistory(history);
  const results = await api(`/api/results?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderResults(results.results || []);
  const weather = await api(`/api/weather?race_id=${encodeURIComponent(selectedRaceId)}`);
  renderWeather(weather);
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
  summary.innerHTML = `
    <div><label>本場上限</label><strong>${formatMoney(data.max_race_stake)}</strong></div>
    <div><label>建議總注</label><strong>${formatMoney(data.total_recommended_stake)}</strong></div>
    <div><label>Kelly</label><strong>${formatPct(settings.fractional_kelly)}</strong></div>
    <div><label>EV門檻</label><strong>${formatPct(settings.min_expected_value)}</strong></div>
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
        <strong>${row.horse_no || "-"} ${row.horse_name || row.horse_id}</strong>
        <small>模型第 ${row.model_rank} ｜ ${row.reason}</small>
      </div>
      <div class="ticket-metrics">
        <div><label>機率</label><strong>${formatPct(row.probability)}</strong></div>
        <div><label>賠率</label><strong>${formatNum(row.odds, 2)}</strong></div>
        <div><label>公允</label><strong>${formatNum(row.fair_odds, 2)}</strong></div>
        <div><label>Edge</label><strong class="${evClass(row.edge)}">${formatPct(row.edge)}</strong></div>
        <div><label>EV</label><strong class="${evClass(row.expected_value)}">${row.expected_value === null ? "-" : Number(row.expected_value).toFixed(3)}</strong></div>
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
        <span>先睇模型機率同打和派彩，等官方組合彩池賠率接入後再計EV</span>
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
      </div>
    </div>
  `;
}

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `$${number.toFixed(1)}`;
}

function formatRaceMeta(race) {
  return `${race.date}｜${localTrack(race.track)}｜${localCourse(race.course)}｜${race.distance_m}米｜${localGoing(race.going)}｜${localClass(race.class_rating)}`;
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
      <td><strong>${row.display_name || row.horse_name}</strong><br><span>${row.horse_id}</span></td>
      <td>${row.draw || "-"}</td>
      <td>${row.display_jockey || row.jockey || "-"}</td>
      <td>${row.display_trainer || row.trainer || "-"}</td>
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

function renderResults(results) {
  const body = $("results-body");
  const summary = $("results-summary");
  body.innerHTML = "";
  if (!results.length) {
    summary.textContent = "未有賽果";
    body.innerHTML = `<tr><td colspan="13" class="empty-cell">未有賽果</td></tr>`;
    return;
  }
  summary.textContent = `${results.length} 匹完成`;
  results.forEach((row) => {
    const tr = document.createElement("tr");
    const position = Number(row.finish_position);
    tr.className = position === 1 ? "winner" : position <= 3 ? "placed" : "";
    tr.innerHTML = `
      <td>${row.finish_position || "-"}</td>
      <td>${row.horse_no || "-"}</td>
      <td><strong>${row.display_name || row.horse_name || row.horse_id}</strong><br><span>${row.horse_id}</span></td>
      <td>${row.draw || "-"}</td>
      <td>${row.display_jockey || row.jockey || "-"}</td>
      <td>${row.display_trainer || row.trainer || "-"}</td>
      <td>${formatRaceTime(row.finish_time_sec)}</td>
      <td>${formatMargin(row.margin_lengths)}</td>
      <td>${formatNum(row.win_odds, 2)}</td>
      <td>${formatNum(row.final_place_odds, 2)}</td>
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
    <p class="runner-title">${row.display_name || row.horse_name}</p>
    <p class="runner-subtitle">
      \u99ac\u865f ${row.horse_no || "-"} | \u6a94\u4f4d ${row.draw || "-"} | ${row.horse_id}<br>
      \u9a0e\u5e2b\uff1a${row.display_jockey || "-"} | \u7df4\u99ac\u5e2b\uff1a${row.display_trainer || "-"}<br>
      \u7368\u8d0f\u52dd\u7387\uff1a${formatPct(row.win_probability)} | \u5165\u4e09\u7532\uff1a${formatPct(row.top3_probability)} | \u4f4d\u7f6eEV\uff1a${row.top3_expected_value === null ? "-" : Number(row.top3_expected_value).toFixed(3)}<br>
      \u4e09\u7532\u4f86\u6e90\uff1a${top3SourceLabel(row.top3_model_source)}
    </p>
    <div class="stat"><label>\u5165\u4e09\u7532\u6a5f\u7387</label><strong>${formatPct(row.top3_probability)}</strong></div>
    <div class="stat"><label>\u7368\u8d0f\u8ce0\u7387</label><strong>${formatNum(row.latest_win_odds, 2)}</strong></div>
    <div class="stat"><label>\u7368\u8d0fEV</label><strong class="${evClass(row.expected_value)}">${row.expected_value === null ? "-" : Number(row.expected_value).toFixed(3)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u8ce0\u7387</label><strong>${formatNum(row.place_odds, 2)} ${placeOddsSourceLabel(row.place_odds_source)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u5e02\u5834</label><strong>${formatPct(row.place_market_probability)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6eEV</label><strong class="${evClass(row.top3_expected_value)}">${row.top3_expected_value === null ? "-" : Number(row.top3_expected_value).toFixed(3)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u50f9\u503c\u5dee</label><strong class="${evClass(row.top3_value_gap)}">${formatPct(row.top3_value_gap)}</strong></div>
    <div class="stat"><label>5分鐘賠率流</label><strong class="${evClass(row.odds_delta_5m)}">${formatSigned(row.odds_delta_5m)}</strong></div>
    <div class="stat"><label>2分鐘賠率流</label><strong class="${evClass(row.odds_delta_2m)}">${formatSigned(row.odds_delta_2m)}</strong></div>
    <div class="stat"><label>30秒賠率流</label><strong class="${evClass(row.odds_delta_30s)}">${formatSigned(row.odds_delta_30s)}</strong></div>
    <div class="stat"><label>熱捧/轉冷</label><strong>${formatSigned(row.late_steam)} / ${formatSigned(row.late_drift)}</strong></div>
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
  return "-";
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
    <div class="stat"><label>\u7368\u8d0fROI</label><strong class="${evClass(data.roi)}">${formatPct(data.roi)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u6ce8\u6578</label><strong>${data.place_bets}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u547d\u4e2d</label><strong>${data.place_wins}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6e\u547d\u4e2d\u7387</label><strong>${formatPct(data.place_hit_rate)}</strong></div>
    <div class="stat"><label>\u4f4d\u7f6eROI</label><strong class="${evClass(data.place_roi)}">${formatPct(data.place_roi)}</strong></div>
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
    <div class="stat"><label>Log Loss</label><strong>${formatNum(metrics.log_loss, 3)}</strong></div>
    <div class="stat"><label>Brier Score</label><strong>${formatNum(metrics.brier_score, 3)}</strong></div>
    <div class="stat"><label>Value ROI</label><strong class="${evClass(metrics.value_roi)}">${formatPct(metrics.value_roi)}</strong></div>
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
          <div><label>Fold</label><b>${metrics.races || 0}</b></div>
          <div><label>首選</label><b>${formatPct(metrics.top_pick_hit_rate)}</b></div>
          <div><label>Log Loss</label><b>${formatNum(metrics.log_loss, 3)}</b></div>
          <div><label>Brier</label><b>${formatNum(metrics.brier_score, 3)}</b></div>
          <div><label>頭馬排名</label><b>${formatNum(metrics.avg_winner_rank, 1)}</b></div>
          <div><label>Value ROI</label><b class="${evClass(metrics.value_roi)}">${formatPct(metrics.value_roi)}</b></div>
        </div>
        <span class="version-verdict">${version.verdict}</span>
      </div>
    `;
  }).join("");
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
    <div class="coverage-card status-${item.status_key}">
      <div class="coverage-card-head">
        <strong>${item.id}. ${item.name}</strong>
        <span class="coverage-tag">${item.status}</span>
      </div>
      <p>${item.current_support}</p>
      <div class="coverage-meta">
        <span><b>類別</b>：${item.category_label}</span>
        <span><b>資料來源</b>：${listText(item.data_sources)}</span>
        <span><b>支援檔案</b>：${listText(item.supported_files)}</span>
        <span><b>缺口</b>：${item.gaps}</span>
        <span><b>下一步</b>：${item.next_steps}</span>
      </div>
    </div>
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
  await refreshSelectedRace();
}

async function completeRunners() {
  $("completion-message").textContent = "補完中...";
  const result = await api("/api/complete-runners", { method: "POST" });
  const completion = result.completion || {};
  $("completion-message").textContent = `已檢查 ${completion.races_checked || 0} 場，更新 ${completion.updated_runners || 0} 匹馬。`;
  renderDataQuality(result.quality || {});
  if (result.model_versions) renderModelVersions(result.model_versions);
  await refreshSelectedRace();
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
      <span><b>${row.display_name || row.horse_name_zh || row.horse_name || row.horse_id}</b><br>馬號 ${row.horse_no || "-"} | ${formatTimestamp(row.timestamp)}</span>
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
      labels.set(row.horse_id, row.display_name || row.horse_name_zh || row.horse_name || row.horse_id);
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
  const result = await api(`/api/refresh-odds?race_id=${encodeURIComponent(selectedRaceId)}`, { method: "POST" });
  if (result.status === "frozen") {
    $("system-status").textContent = "賽事已開跑或完場，保留最後實時賠率";
  } else if (result.status === "error") {
    $("system-status").textContent = `賠率未更新：${result.error || ""}`;
  }
  countdown = intervalSeconds;
  await refreshSelectedRace();
}

async function lifecycleStep() {
  const result = await api("/api/lifecycle-step", { method: "POST" });
  if (result.next_race_id && result.next_race_id !== selectedRaceId) {
    selectedRaceId = result.next_race_id;
    selectedHorseId = null;
    autoFollowRace = true;
  }
  $("system-status").textContent = result.race_id ? `流程已處理：${result.race_id}` : "未有未開跑場次";
  countdown = intervalSeconds;
  await loadState();
  await refreshSelectedRace();
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
    if (autoFollowRace && lifecycle.refreshable_race_id) {
      selectedRaceId = lifecycle.refreshable_race_id;
      selectedHorseId = null;
    }
  }
  await refreshSelectedRace();
}

async function loadRaceDay(event) {
  event.preventDefault();
  const date = $("race-date").value.trim();
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
      await refreshSelectedRace();
    }
    if (job.status === "error") {
      clearInterval(raceDayJobTimer);
      $("race-day-message").textContent = `${text.loadFailed}\uff1a${job.error || ""}`;
    }
  }, 1000);
}

async function loadBackfill(event) {
  event.preventDefault();
  const start = $("backfill-start").value.trim();
  const end = $("backfill-end").value.trim();
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
      await refreshSelectedRace();
    }
    if (job.status === "error") {
      clearInterval(backfillJobTimer);
      $("backfill-message").textContent = `回填失敗：${job.error || ""}`;
    }
  }, 1200);
}

async function boot() {
  $("refresh-now").addEventListener("click", manualRefreshOdds);
  $("refresh-results").addEventListener("click", refreshResults);
  $("lifecycle-step").addEventListener("click", lifecycleStep);
  $("mark-live").addEventListener("click", markLive);
  $("mark-scheduled").addEventListener("click", markScheduled);
  $("bankroll-input").addEventListener("change", refreshSelectedRace);
  $("risk-profile").addEventListener("change", refreshSelectedRace);
  $("run-gpt-iteration").addEventListener("click", runGptIteration);
  $("repair-data").addEventListener("click", repairData);
  $("complete-runners").addEventListener("click", completeRunners);
  $("race-day-form").addEventListener("submit", loadRaceDay);
  $("backfill-form").addEventListener("submit", loadBackfill);
  await loadState();
  await loadRaces();
  await refreshCoverage();
  await refreshSelectedRace();
  countdown = intervalSeconds;
  setInterval(async () => {
    countdown -= 1;
    if (countdown <= 0) {
      countdown = intervalSeconds;
      await loadState();
      await refreshSelectedRace();
    }
    $("countdown").textContent = countdown;
  }, 1000);
}

boot().catch((error) => {
  $("system-status").textContent = `\u932f\u8aa4\uff1a${error.message}`;
});
