const state = {
  loaded: { home: false, results: false, upcoming: false, standings: false },
  loading: false
};

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function israelDate(iso, withTime = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  const date = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Jerusalem",
    year: "numeric", month: "2-digit", day: "2-digit"
  }).format(d).replaceAll("-", "/");
  if (!withTime) return date;
  const time = new Intl.DateTimeFormat("he-IL", {
    timeZone: "Asia/Jerusalem",
    hour: "2-digit", minute: "2-digit", hour12: false
  }).format(d);
  return `${date} · ${time}`;
}

function gameCard(game, upcoming = false) {
  const hs = game.home_score;
  const as = game.away_score;
  const score = hs == null || as == null ? "—" : `${hs} - ${as}`;
  return `
    <article class="game-card">
      <div class="game-meta">${israelDate(game.date, upcoming)}${game.tournament ? ` · ${esc(game.tournament)}` : ""}</div>
      <div class="teams">
        <div class="team">
          <img src="${esc(game.home.logo)}" alt="">
          <span>${esc(game.home.name)}</span>
        </div>
        <div class="score ${upcoming ? "future" : ""}">${score}</div>
        <div class="team">
          <span>${esc(game.away.name)}</span>
          <img src="${esc(game.away.logo)}" alt="">
        </div>
      </div>
      ${!upcoming && game.status_text ? `<div class="game-status">${esc(game.status_text)}</div>` : ""}
    </article>`;
}

function leagueSection(league, mode) {
  const isUpcoming = mode === "upcoming";
  const body = (league.events || []).map(e => gameCard(e, isUpcoming)).join("");
  return `
    <section class="section-card collapsible league-section">
      <div class="section-head">
        <h2>${esc(league.name_he)}</h2>
        <button class="collapse-btn" aria-label="הסתר">↓</button>
      </div>
      <div class="section-body">
        ${body || `<div class="empty">אין כרגע נתונים זמינים.</div>`}
      </div>
    </section>`;
}

function standingsTable(table) {
  const rows = (table.rows || []).map(r => `
    <tr>
      <td class="pos">${esc(r.position)}</td>
      <td class="stand-team"><img src="${esc(r.logo)}" alt=""><span>${esc(r.team)}</span></td>
      <td>${esc(r.played)}</td>
      <td>${esc(r.wins)}</td>
      <td>${esc(r.draws)}</td>
      <td>${esc(r.losses)}</td>
      <td><strong>${esc(r.points)}</strong></td>
    </tr>`).join("");
  return `
    <div class="table-wrap">
      <div class="subtable-title">${esc(table.name)}</div>
      <table>
        <thead><tr><th>#</th><th>קבוצה</th><th>מש'</th><th>נ'</th><th>ת'</th><th>ה'</th><th>נק'</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function standingsSection(league) {
  return `
    <section class="section-card collapsible league-section">
      <div class="section-head">
        <h2>${esc(league.name_he)}</h2>
        <button class="collapse-btn" aria-label="הסתר">↓</button>
      </div>
      <div class="section-body">
        ${(league.tables || []).map(standingsTable).join("") || `<div class="empty">אין כרגע טבלה זמינה.</div>`}
      </div>
    </section>`;
}

function newsCard(item) {
  return `
    <a class="news-card" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">
      <div class="news-badge">NEWS</div>
      <div class="news-info">
        <h3>${esc(item.title)}</h3>
        <div>${esc(item.source || "News")} · ${israelDate(item.published, true)}</div>
      </div>
      <div class="news-arrow">↗</div>
    </a>`;
}

async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function setUpdated(iso) {
  const el = $("#updatedAt");
  if (!el) return;
  el.textContent = `עודכן: ${israelDate(iso, true)}`;
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2500);
}

function attachCollapse(root = document) {
  $$(".collapse-btn", root).forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", () => {
      const section = btn.closest(".collapsible");
      section.classList.toggle("collapsed");
      btn.textContent = section.classList.contains("collapsed") ? "←" : "↓";
      btn.setAttribute("aria-label", section.classList.contains("collapsed") ? "הצג" : "הסתר");
    });
  });
}

async function loadHome(force = false) {
  if (state.loaded.home && !force) return;
  const [home] = await Promise.all([fetchJSON("/api/home")]);
  $("#homeResultsBody").innerHTML = home.results.length
    ? home.results.slice(0, 30).map(g => gameCard(g, false)).join("")
    : `<div class="empty">אין כרגע נתונים זמינים.</div>`;
  $("#homeUpcomingBody").innerHTML = home.upcoming.length
    ? home.upcoming.slice(0, 30).map(g => gameCard(g, true)).join("")
    : `<div class="empty">אין כרגע נתונים זמינים.</div>`;
  $("#homeNewsBody").innerHTML = home.news.length
    ? home.news.map(newsCard).join("")
    : `<div class="empty">לא נמצאו כתבות כרגע.</div>`;
  setUpdated(home.updated_at);
  attachCollapse();
  state.loaded.home = true;
}

async function loadResults(force = false) {
  if (state.loaded.results && !force) return;
  const data = await fetchJSON("/api/results");
  $("#resultsBody").innerHTML = data.leagues.map(l => leagueSection(l, "results")).join("");
  attachCollapse($("#resultsBody"));
  state.loaded.results = true;
}

async function loadUpcoming(force = false) {
  if (state.loaded.upcoming && !force) return;
  const data = await fetchJSON("/api/upcoming");
  $("#upcomingBody").innerHTML = data.leagues.map(l => leagueSection(l, "upcoming")).join("");
  attachCollapse($("#upcomingBody"));
  state.loaded.upcoming = true;
}

async function loadStandings(force = false) {
  if (state.loaded.standings && !force) return;
  const data = await fetchJSON("/api/standings");
  $("#standingsBody").innerHTML = data.leagues.map(standingsSection).join("");
  attachCollapse($("#standingsBody"));
  state.loaded.standings = true;
}

async function search() {
  const q = $("#searchInput").value.trim();
  if (!q) return;
  $("#searchBody").innerHTML = `<div class="loading">מחפש…</div>`;
  try {
    const data = await fetchJSON(`/api/search?q=${encodeURIComponent(q)}`);
    const has = data.players.length || data.teams.length;
    if (!has) {
      $("#searchBody").innerHTML = `<div class="not-found">לא מצאתי !</div>`;
      return;
    }

    let html = "";
    if (data.players.length) {
      html += `<div class="result-block"><div class="result-title">שחקנים</div>`;
      for (const p of data.players) {
        html += `
          <button class="search-result player-result" data-player-id="${esc(p.id)}">
            <div class="result-icon">♟</div>
            <div><strong>${esc(p.name)}</strong><span>${esc(p.country || p.sport || "")}</span></div>
            <span>→</span>
          </button>`;
      }
      html += `</div>`;
    }

    if (data.teams.length) {
      html += `<div class="result-block"><div class="result-title">קבוצות</div>`;
      for (const t of data.teams) {
        html += `
          <button class="search-result team-result" data-team-id="${esc(t.id)}">
            <div class="result-icon"><img src="https://api.sofascore.com/api/v1/team/${esc(t.id)}/image" alt=""></div>
            <div><strong>${esc(t.name)}</strong><span>${esc([t.country, t.sport].filter(Boolean).join(" · "))}</span></div>
            <span>→</span>
          </button>`;
      }
      html += `</div>`;
    }

    $("#searchBody").innerHTML = html;
    $$(".player-result", $("#searchBody")).forEach(b => b.addEventListener("click", () => showPlayer(b.dataset.playerId)));
    $$(".team-result", $("#searchBody")).forEach(b => b.addEventListener("click", () => showTeam(b.dataset.teamId)));
  } catch (e) {
    $("#searchBody").innerHTML = `<div class="not-found">לא מצאתי !</div>`;
  }
}

async function showPlayer(id) {
  $("#searchBody").innerHTML = `<div class="loading">טוען פרופיל…</div>`;
  try {
    const p = await fetchJSON(`/api/player/${id}`);
    if (!p.name) throw new Error();
    $("#searchBody").innerHTML = `
      <div class="profile-card">
        <img class="profile-photo" src="${esc(p.image)}" alt="">
        <div class="profile-main">
          <div class="eyebrow">PLAYER</div>
          <h2>${esc(p.name)}</h2>
          <div class="profile-grid">
            <div><span>קבוצה</span><strong>${esc(p.team || "—")}</strong></div>
            <div><span>גיל</span><strong>${esc(p.age ?? "—")}</strong></div>
            <div><span>מספר</span><strong>${esc(p.shirt_number ?? "—")}</strong></div>
            <div><span>מדינה</span><strong>${esc(p.country || "—")}</strong></div>
          </div>
        </div>
      </div>`;
  } catch {
    $("#searchBody").innerHTML = `<div class="not-found">לא מצאתי !</div>`;
  }
}

async function showTeam(id) {
  $("#searchBody").innerHTML = `<div class="loading">טוען קבוצה…</div>`;
  try {
    const t = await fetchJSON(`/api/team/${id}`);
    $("#searchBody").innerHTML = `
      <div class="team-profile">
        <div class="team-profile-top">
          <img src="${esc(t.logo)}" alt="">
          <div><div class="eyebrow">TEAM</div><h2>${esc(t.name)}</h2><p>${esc([t.country, t.city].filter(Boolean).join(" · "))}</p></div>
        </div>
        <div class="team-standings">${(t.standings || []).map(s => `<div><span>${esc(s.league)}</span><strong>${esc(s.position)}</strong></div>`).join("") || `<div class="empty">אין מיקום זמין</div>`}</div>
        <div class="team-games">
          <div><h3>המשחק הבא</h3>${t.next ? gameCard(t.next, true) : `<div class="empty">אין משחק קרוב</div>`}</div>
          <div><h3>המשחק הקודם</h3>${t.last ? gameCard(t.last, false) : `<div class="empty">אין תוצאה קודמת</div>`}</div>
        </div>
      </div>`;
  } catch {
    $("#searchBody").innerHTML = `<div class="not-found">לא מצאתי !</div>`;
  }
}

async function switchPage(page, force = false) {
  $$(".page").forEach(p => p.classList.remove("active"));
  $(`#page-${page}`).classList.add("active");
  $$(".menu-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  $("#menu").classList.remove("open");

  try {
    if (page === "home") await loadHome(force);
    if (page === "results") await loadResults(force);
    if (page === "upcoming") await loadUpcoming(force);
    if (page === "standings") await loadStandings(force);
  } catch (e) {
    toast("לא הצלחתי לטעון את הנתונים כרגע.");
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("#menuBtn").addEventListener("click", () => $("#menu").classList.toggle("open"));
$$(".menu-item").forEach(b => b.addEventListener("click", () => switchPage(b.dataset.page)));
$("#searchBtn").addEventListener("click", search);
$("#searchInput").addEventListener("keydown", e => { if (e.key === "Enter") search(); });

$("#refreshBtn").addEventListener("click", async () => {
  state.loaded = { home: false, results: false, upcoming: false, standings: false };
  const active = $(".menu-item.active")?.dataset.page || "home";
  try {
    await switchPage(active, true);
    toast("הנתונים עודכנו.");
  } catch {
    toast("הרענון נכשל.");
  }
});

let touchStartX = 0;
let touchStartY = 0;
document.addEventListener("touchstart", e => {
  touchStartX = e.changedTouches[0].screenX;
  touchStartY = e.changedTouches[0].screenY;
}, { passive: true });

document.addEventListener("touchend", e => {
  const dx = e.changedTouches[0].screenX - touchStartX;
  const dy = e.changedTouches[0].screenY - touchStartY;
  if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.2) return;

  const pages = ["home", "results", "upcoming", "standings", "search"];
  const active = $(".page.active")?.id.replace("page-", "");
  const index = pages.indexOf(active);
  const nextIndex = dx < 0 ? Math.min(index + 1, pages.length - 1) : Math.max(index - 1, 0);
  if (nextIndex !== index) switchPage(pages[nextIndex]);
}, { passive: true });

attachCollapse();
loadHome();
setInterval(() => {
  const active = $(".menu-item.active")?.dataset.page || "home";
  if (active === "home") { state.loaded.home = false; loadHome(true).catch(() => {}); }
  if (active === "results") { state.loaded.results = false; loadResults(true).catch(() => {}); }
  if (active === "upcoming") { state.loaded.upcoming = false; loadUpcoming(true).catch(() => {}); }
  if (active === "standings") { state.loaded.standings = false; loadStandings(true).catch(() => {}); }
}, 60_000);
