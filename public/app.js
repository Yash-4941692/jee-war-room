/* ============================================================
   JEE WAR ROOM — single-file frontend (no frameworks, no build)
   ============================================================ */
const App = (() => {
"use strict";

const SUBJECTS = ["Physics", "Chemistry", "Mathematics"];
const DISTRACTIONS = ["YouTube","Instagram","Gaming","Phone","Chatting","Random browsing","Other"];
const ERROR_TYPES = ["Concept","Calculation","Silly mistake","Misread","Guess","Time pressure","Formula","Other"];
const TEST_TYPES = ["Full JEE Main","Part Test","Coaching Test","Custom"];
const NAV = [
  {id:"home", ic:"🏠", l:"Home"}, {id:"today", ic:"🎯", l:"Today"},
  {id:"jee", ic:"📚", l:"JEE"}, {id:"focus", ic:"⏱️", l:"Focus"},
  {id:"duel", ic:"⚔️", l:"Duel"}, {id:"chat", ic:"💬", l:"Chat"},
  {id:"analytics", ic:"📈", l:"Analytics"}, {id:"settings", ic:"⚙️", l:"Settings"},
];
const AMOUNTS = {dpp:[10,25,40,50], homework:[10,15,25,40], pyq:[10,25,40,50,75,100]};
const DURATIONS = {revision:[15,30,45,60,90], study:[15,25,30,45,60,90], duration:[10,15,30,45,60,90], distraction:[5,15,30,60]};
const STATUS_LABEL = {not_started:"NOT STARTED", in_progress:"IN PROGRESS", completed:"COMPLETED", revision_needed:"REVISION"};
const STATUS_ORDER = ["not_started","in_progress","revision_needed","completed"];

const state = {
  me:null, friends:[], chapters:null, targets:[], announcements:[],
  view:"home", stats:{}, airDetail:null,
  wz:null, tmr:null, tmrTick:null,
  duelUid:null, chatOpen:null, chatMsgs:[], chatPoll:null, threads:[], chatPending:{}, chatCache:{},
};
const isAdmin = () => !!(state.me && state.me.user && state.me.user.role === "admin");
const friendByUid = uid => state.friends.find(f=>f.uid===uid) || null;
const unreadTotal = () => state.friends.reduce((a,f)=>a+(f.unread||0),0);
const fmtTime = iso => { try{ const d=new Date(iso); const todayS=new Date().toDateString();
  return d.toDateString()===todayS ? d.toLocaleTimeString([],{hour:"numeric",minute:"2-digit"})
    : d.toLocaleDateString([],{day:"numeric",month:"short"}); }catch(e){ return ""; } };

/* ---------------- utils ---------------- */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const nonce = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now())+Math.random());
const fmtMins = m => { m = Math.round(m||0); const h = Math.floor(m/60); return h ? `${h}h ${m%60}m` : `${m}m`; };
const fmtAIR = n => Number(n).toLocaleString("en-IN");
const fmtDate = d => new Date(d+"T00:00").toLocaleDateString(undefined,{weekday:"short",day:"numeric",month:"short"});
const today = () => new Date().toISOString().slice(0,10);
function toast(msg, kind=""){
  const t = document.createElement("div"); t.className = "toast "+kind; t.textContent = msg;
  $("#toast-root").appendChild(t); setTimeout(()=>t.remove(), 3200);
}
const TOKEN_KEY = "jwr_token";
const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch(e){ return ""; } };
const setToken = t => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch(e){} };
async function api(path, body){
  const opt = {method:"GET", headers:{}, credentials:"same-origin"};
  const tok = getToken(); if(tok) opt.headers["Authorization"] = "Bearer " + tok;
  if(body !== undefined){ opt.method="POST"; opt.headers["Content-Type"]="application/json"; opt.body=JSON.stringify(body); }
  // Never hang forever on a cold database wake; GETs transparently retry
  // (a cold Turso wake can take ~15-20s; by the retry it is warm). Retries
  // also absorb the 1-2 s window during an atomic deploy so users never see
  // a "Connection lost" banner while an update is swapping over.
  const isGet = body===undefined;
  let r, d={}, tries=0;
  while(true){
    opt.signal = AbortSignal.timeout(isGet ? 15000 : 20000);
    try{
      r = await fetch(path, opt);
      // a draining server during an atomic deploy gives brief 502/503/504 — retry, don't scare anyone
      if(isGet && r.status>=502 && r.status<=504 && tries<2){
        throw new Error("retryable");
      }
      break;
    }catch(e){
      tries++;
      if(isGet && tries<3){ await new Promise(res=>setTimeout(res, tries*1800)); continue; }
      bumpFails();
      if(consecutiveFails()>=2) setOffline(true);
      throw new Error(e.name==="TimeoutError" ? "Server is warming up — please retry in a few seconds."
                                              : "No connection — the server is temporarily unreachable.");
    }
  }
  try{ d = await r.json(); }catch(e){}
  resetFails(); setOffline(false);
  if(r.status === 401 && !path.includes("/auth/")){
    setToken(""); state.me = null; showAuth();
    throw new Error(d.error || "Please log in again.");
  }
  if(!r.ok){ throw new Error(d.error || ("Request failed ("+r.status+")")); }
  return d;
}
/* connectivity banner — only after repeated real failures, never a single blip */
let offlineUI=false, lastFailAt=0, _fails=0;
function bumpFails(){ _fails++; lastFailAt=Date.now(); }
function resetFails(){ _fails=0; }
function consecutiveFails(){ return _fails; }
function setOffline(off){
  if(off){ lastFailAt=Date.now(); }
  if(off===offlineUI) return; offlineUI=off;
  let b=document.getElementById("net-banner");
  if(!b){
    b=document.createElement("div"); b.id="net-banner";
    b.textContent="⚠️ Connection lost — reconnecting… messages won't send until this bar disappears.";
    document.body.appendChild(b);
  }
  b.classList.toggle("show",off);
}
window.addEventListener("online",()=>{
  setOffline(false);
  toast("Back online","good");
  if(state.view==="chat" && state.chatOpen){
    (state.chatPending[state.chatOpen]||[]).filter(p=>p.status==="failed")
      .forEach(p=>retryChat(p.temp));
  }
});
window.addEventListener("offline",()=>setOffline(true));
const fire = f => { try{ const r=f(); if(r&&r.catch) r.catch(e=>toast(e.message,"bad")); }catch(e){ toast(e.message,"bad"); } };
function av(name,color,size=38){ return `<div class="av" style="width:${size}px;height:${size}px;background:${esc(color||'#889')}">${esc((name||'?').trim()[0].toUpperCase())}</div>`; }
function pctBar(pct, color){ pct=Math.max(0,Math.min(100,pct||0)); return `<div class="bar"><i style="width:${pct}%;background:${color||''}"></i></div>`; }

/* ---------------- modal ---------------- */
function modal(html, onOpen){
  closeModal();
  const o = document.createElement("div"); o.className="overlay"; o.id="overlay";
  o.innerHTML = `<div class="modal">${html}</div>`;
  o.addEventListener("click", e=>{ if(e.target===o) closeModal(); });
  $("#modal-root").appendChild(o);
  if(onOpen) onOpen();
}
function closeModal(){ const o=$("#overlay"); if(o) o.remove(); if(state.tmrTick&&false){} }
function mHead(title){ return `<div class="m-head"><b>${title}</b><button class="m-x" onclick="App.closeModal()">✕</button></div>`; }
function chips(items, sel, fn, extra=""){
  return `<div class="chips">${items.map(x=>`<button class="chip ${extra} ${sel===x?'sel':''}" onclick="${fn}">${esc(x)}</button>`).join("")}</div>`;
}
function stepLabel(t){ return `<div class="m-step-lab">${t}</div>`; }

/* ---------------- charts ---------------- */
function lineSVG(vals, opts={}){
  const w=520, mini=(opts.h&&opts.h<90), h=opts.h||170, pad=mini?6:30;
  if(!vals.length) vals=[0];
  let mn=Math.min(...vals), mx=Math.max(...vals);
  if(mn===mx){ mn-=1; mx+=1; }
  const X=i=>pad+i*(w-pad*2)/Math.max(1,vals.length-1);
  const Y=v=>h-pad-(v-mn)*(h-pad*2)/(mx-mn);
  let path=vals.map((v,i)=>`${i?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  let area=`${path} L${X(vals.length-1)},${h-pad} L${X(0)},${h-pad} Z`;
  const col=opts.color||'#22d3ee';
  if(mini){
    const gid="g"+Math.random().toString(36).slice(2,8);
    return `<svg class="spark-svg" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${col}" stop-opacity=".28"/><stop offset="100%" stop-color="${col}" stop-opacity="0"/>
      </linearGradient></defs>
      <path d="${area}" fill="url(#${gid})" stroke="none"/>
      <path d="${path}" fill="none" stroke="${col}" stroke-width="2.5" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${X(vals.length-1)}" cy="${Y(vals[vals.length-1])}" r="4" fill="${col}"/>
    </svg>`;
  }
  const ylab=v=>opts.yfmt?opts.yfmt(v):(Math.abs(v)<10?Number(v).toFixed(1):Math.round(v));
  const grid=[0,.25,.5,.75,1].map(f=>{const v=mx-f*(mx-mn);return `<line x1="${pad}" x2="${w-pad}" y1="${Y(v)}" y2="${Y(v)}" stroke="rgba(255,255,255,.06)"/><text x="4" y="${Y(v)+3}">${ylab(v)}</text>`;}).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    ${grid}
    <path d="${area}" fill="${col}22" stroke="none"/>
    <path d="${path}" fill="none" stroke="${col}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${X(vals.length-1)}" cy="${Y(vals[vals.length-1])}" r="3.5" fill="${col}"/>
  </svg>`;
}
function barSVG(vals, opts={}){
  const w=520,h=opts.h||170,pad=26;
  const mx=Math.max(1,...vals);
  const bw=(w-pad*2)/vals.length*0.68;
  const gap=(w-pad*2)/vals.length;
  const bars=vals.map((v,i)=>{
    const bh=Math.max(v?2:0,(v/mx)*(h-pad*1.6)), x=pad+i*gap+gap*0.16, y=h-pad-bh;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="3" fill="${opts.color||'#ff7a1a'}${v?'':'33'}"/>`;
  }).join("");
  const labels=(opts.labels||[]).map((l,i)=>`<text x="${pad+i*gap+gap/2}" y="${h-8}" text-anchor="middle">${esc(l)}</text>`).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${bars}${labels}
    <text x="4" y="${h-pad-4}">0</text><text x="4" y="14">${Math.round(mx)}</text></svg>`;
}
function ringSVG(pct, size=96, color="#22d3ee", txt="", sub=""){
  const r=(size-12)/2, c=2*Math.PI*r, off=c*(1-Math.max(0,Math.min(100,pct))/100);
  return `<div class="score-ring" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}"><circle cx="${size/2}" cy="${size/2}" r="${r}" stroke="rgba(255,255,255,.08)" stroke-width="8" fill="none"/>
    <circle cx="${size/2}" cy="${size/2}" r="${r}" stroke="${color}" stroke-width="8" fill="none" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${off}"/></svg>
    <div class="rt"><b>${txt||Math.round(pct)+'%'}</b><span>${sub}</span></div></div>`;
}

/* ---------------- boot / auth ---------------- */
async function init(){
  try{
    await warmUpServer();
    await reloadMe(true);
    try{ await loadChapters(true); }catch(e){}
    enterApp();
  }catch(e){ showAuth(); }
}
/* A sleeping free-tier database takes ~15-20 s to wake. The public health
   endpoint itself triggers the wake, so we warm it ONCE up front instead of
   letting the login/app burst collide with the wake (that collision caused
   the cascading 500s / slow loads). */
async function warmUpServer(){
  if(state.warm) return true;
  for(let i=0;i<2;i++){
    try{
      const ctrl=new AbortController();
      const to=setTimeout(()=>ctrl.abort(),40000);
      const r=await fetch("/healthz?detail=1",{signal:ctrl.signal,cache:"no-store"});
      clearTimeout(to);
      if(r.ok){ state.warm=true; return true; }
    }catch(e){ /* waking; one more long-poll attempt */ }
    await new Promise(res=>setTimeout(res,1500));
  }
  return false; // let normal calls proceed; they have their own retries
}
function showAuth(){
  $("#boot").classList.add("hidden"); $("#app").classList.add("hidden"); $("#auth").classList.remove("hidden");
  authTab("login");
}
function authTab(t){
  $("#tab-login").classList.toggle("active",t==="login");
  $("#tab-signup").classList.toggle("active",t==="signup");
  $("#signup-extra").classList.toggle("hidden",t==="login");
  $("#auth-btn").textContent = t==="login" ? "LOGIN" : "CREATE ACCOUNT";
  $("#auth-err").textContent=""; state.authMode=t;
}
async function authSubmit(ev){
  ev.preventDefault();
  const name=$("#auth-name").value.trim(), pw=$("#auth-pass").value;
  const btn=$("#auth-btn"); btn.disabled=true; $("#auth-err").textContent="";
  const oldBtn=btn.textContent; btn.textContent="WARMING UP…";
  try{
    await warmUpServer();
    btn.textContent=oldBtn;
    if(state.authMode==="signup"){
      const examDate=$("#auth-date").value || undefined;
      const r = await api("/api/auth/signup",{name,password:pw,examDate});
      if(r.token) setToken(r.token);
      state.myCode=r.code;
      $("#auth-form").classList.add("hidden"); $("#auth-after").classList.remove("hidden");
      $("#after-code").textContent=r.code;
      return;
    }else{
      const r = await api("/api/auth/login",{name,password:pw});
      if(r.token) setToken(r.token);
      await reloadMe(true); enterApp();
    }
  }catch(e){ $("#auth-err").textContent=e.message; }
  finally{ btn.disabled=false; }
  return false;
}
async function connectAfter(){
  const code=$("#after-connect").value.trim();
  try{ const r=await api("/api/friend/connect",{nonce:nonce(),code}); toast("Connected with "+r.partner,"good"); await enterApp(); }
  catch(e){ toast(e.message,"bad"); }
}
async function enterApp(){
  try{
    if(!state.me){
      await Promise.all([
        reloadMe(true),
        loadChapters(true).catch(()=>{})
      ]);
    } else { try{ await loadChapters(true); }catch(e){} }
  }catch(e){ showAuth(); return; }
  $("#boot").classList.add("hidden"); $("#auth").classList.add("hidden");
  $("#auth-form").classList.remove("hidden"); $("#auth-after").classList.add("hidden");
  $("#app").classList.remove("hidden");
  if(!location.hash || location.hash==="#/") location.hash="#/home";
  route();
  startLiveSync();
}

/* automatic live sync — no manual reload needed */
let liveTimer=null, liveBusy=false;
function startLiveSync(){
  if(liveTimer) return;
  document.addEventListener("visibilitychange",()=>{ if(!document.hidden && state.me) liveTick(); });
  window.addEventListener("pageshow",()=>{ if(state.me) liveTick(); });
  liveTimer=setInterval(()=>{ if(!document.hidden && state.me && !document.querySelector(".overlay")) liveTick(); },90000);
}
async function liveTick(){
  if(liveBusy) return; liveBusy=true;
  try{
    await reloadMe(); buildNav();
    if(state.view==="chat"){ refreshThreads(); }
    else if(["home","today","jee","duel"].includes(state.view)){ render(true); }
  }catch(e){ /* silent background sync */ }
  finally{ liveBusy=false; }
}
async function logout(){ if(!confirm("Log out?"))return; try{ await api("/api/auth/logout",{}); }catch(e){} setToken(""); location.reload(); }

async function reloadMe(silent){
  // parallel: one round-trip instead of two sequential ones
  const [me] = await Promise.all([
    api("/api/me"),
    api("/api/friends").then(d=>{ state.friends=d.friends||[]; }).catch(()=>{ state.friends=[]; })
  ]);
  state.me = me;
}
async function refresh(renderAgain=true){
  // fire every startup load at once (me, friends, chapters, announcements…)
  const jobs=[
    reloadMe(),
    loadChapters(false).catch(()=>{}),
    api("/api/announcements").then(d=>{ state.announcements=d.announcements||[]; }).catch(()=>{}),
  ];
  if(state.view==="today") jobs.push(loadTargets().catch(()=>{}));
  await Promise.all(jobs);
  if(renderAgain) render();
}

/* ---------------- nav / router ---------------- */
function buildNav(){
  const u = state.me.user;
  const unread = unreadTotal();
  const badge = n => n.id==="chat" && unread ? `<span class="nav-badge">${unread}</span>` : "";
  $("#bottomnav").innerHTML = NAV.map(n=>`<button class="bn ${state.view===n.id?'active':''}" onclick="App.go('${n.id}')"><span class="ic">${n.ic}${badge(n)}</span>${n.l}</button>`).join("");
  const snLinks = NAV.map(n=>`<button class="sn ${state.view===n.id?'active':''}" onclick="App.go('${n.id}')"><span class="ic">${n.ic}</span><span class="sn-label">${n.l}</span>${badge(n)}</button>`).join("");
  $("#sidenav").innerHTML = `<div class="sn-logo">🎯 <b>JEE WAR ROOM</b></div>${snLinks}
    <div class="sn-spacer"></div>
    <div class="sn-credit">Made by <b>Yash Sharma</b> 🎯</div>
    <div class="sn-user">${av(u.name,u.avatar_color,30)}<div style="min-width:0"><div style="font-weight:700;color:var(--txt);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(u.name)}</div>
    <button class="ghostlink" onclick="App.logout()">Logout</button></div></div>`;
  const d = daysToExam();
  $("#countdown-chip").innerHTML = d===null ? "Set exam date in Settings" : `JEE MAIN in <b>${d}</b> day${d===1?'':'s'}`;
}
function daysToExam(){
  const d = state.me?.user?.exam_date; if(!d) return null;
  const diff = Math.ceil((new Date(d+"T00:00") - new Date(today()+"T00:00"))/86400000);
  return Math.max(0,diff);
}
function go(v){ location.hash="#/"+v; }
function route(){
  state.view = (location.hash||"#/home").slice(2).split("?")[0] || "home";
  if(!NAV.find(n=>n.id===state.view)) state.view="home";
  if(!state.me) return; // boot still loading; enterApp() will route once data is ready
  stopChatPoll();
  if(state.view!=="chat"){ state.chatOpen=null; }
  buildNav(); render();
}
window.addEventListener("hashchange", route);

function render(keepScroll){
  const v=$("#view"); if(!state.me){return;}
  const sy=window.scrollY;
  const restore=()=>{ if(keepScroll) requestAnimationFrame(()=>window.scrollTo({top:sy,behavior:"instant"})); };
  if(state.view==="home"){ v.innerHTML=viewHome(); restore(); }
  else if(state.view==="today"){ renderToday().then(restore); }
  else if(state.view==="jee"){ renderJee(); restore(); }
  else if(state.view==="focus"){ renderFocus(); restore(); }
  else if(state.view==="duel"){ v.innerHTML=viewDuel(); restore(); loadDuelWeek(); }
  else if(state.view==="chat") renderChat();
  else if(state.view==="analytics"){ renderAnalytics("overview"); restore(); }
  else if(state.view==="settings"){ renderSettings(); restore(); }
  buildNav();
}

/* ============================================================ HOME */
function viewHome(){
  const m=state.me, s=m.summary, u=m.user, air=m.air;
  const cards=(m.settings.cards||[]).filter(c=>c.enabled);
  const has=c=>cards.some(x=>x.key===c);
  let html=announcementsHTML();
  // hero
  const d=daysToExam();
  html+=`<div class="hero">
    <div class="hero-l">
      <div class="kick">JEE MAIN COUNTDOWN</div>
      <div class="days">${d===null?'—':d}</div>
      <div class="dsub">${d===1?'day to go':'days to go'}${u.exam_date?' · '+fmtDate(u.exam_date):''}
      <button class="hero-edited" onclick="App.editExam()">edit</button></div>
    </div>
    <div class="hero-right">
      <div class="hero-user">${av(u.name,u.avatar_color,44)}
      <div class="hero-id"><div class="hero-name">${esc(u.name)}</div>
      <div class="muted hero-lvl">Lvl ${m.xp.level} · ${esc(m.xp.title)}</div></div></div>
      <div class="hero-streak">🔥 <b>${m.streak.current}</b>&nbsp;day streak · best ${m.streak.best}</div>
    </div></div>`;

  if(has("air")){
    const t7=air.trend7, arrow=t7>0?"▲":(t7<0?"▼":"■"), cls=t7>0?"up":(t7<0?"down":"muted");
    const sparkVals=air.history.slice(-14).map(x=>600000-x.air);
    html+=`<div class="air-card">
      <div class="air-main">
        <div class="air-lab">🎯 PROJECTED AIR <span class="air-est">ESTIMATE</span></div>
        <div class="air-val">${fmtAIR(air.air)}</div>
        <div class="air-trend ${cls}">${arrow} ${t7?fmtAIR(Math.abs(t7))+' vs 7 days ago':'No movement yet — log a few days'}</div>
      </div>
      <div class="air-side">${ringSVG(air.score,104,"#22d3ee",Math.round(air.score),"PREP SCORE")}</div>
      <div class="air-spark">${lineSVG(sparkVals,{h:48,color:"#22d3ee"})}</div>
      <div class="air-disc">${air.label}</div>
    </div>`;
  }

  // stat cards in configured order
  const statHtml=[];
  for(const c of cards){
    if(c.key==="air") continue;
    if(c.key==="execution") statHtml.push(statCard("🎯","TODAY'S EXECUTION", s.execution+"%", `${s.done}/${s.planned} targets done`, ringMini(s.execution,"#ff7a1a")));
    if(c.key==="time") statHtml.push(statCard("⏱️","STUDY TIME", fmtMins(s.minutes), "today · focus + revision"));
    if(c.key==="questions") statHtml.push(statCard("🔢","QUESTIONS", s.questions.toLocaleString(), "PYQs + DPP + homework today"));
    if(c.key==="streak") statHtml.push(statCard("🔥","STREAK", m.streak.current+" day"+(m.streak.current===1?"":"s"), `best ${m.streak.best} days`));
    if(c.key==="xp"){
      const pct=Math.round(100*m.xp.into/Math.max(1,m.xp.span));
      statHtml.push(statCard("⚡","XP", m.xp.xp.toLocaleString(), `Lvl ${m.xp.level} · ${m.xp.title}`, pctBar(pct,"#a3e635")));
    }
  if(c.key==="syllabus") statHtml.push(syllabusMiniCard());
 }
  html+=`<div class="grid auto">${statHtml.join("")}</div>`;
  html+=metricsCard();
  if(has("friend")){
    html+=friendCard();
  }

  // emergency reset
  html+=`<div class="emg"><div class="e1">🚨 I'M OFF TRACK</div>
    <div class="muted">Stop. Reset. Start again.</div>
    <ol><li>Choose one subject.</li><li>Start a 25-minute session.</li><li>Complete one small task.</li><li>Log it.</li><li>Continue.</li></ol>
    <button class="btn red" onclick="App.emergency()">START 25 MIN NOW</button></div>`;
  return html;
}
function ringMini(pct,color){ return `<div style="margin-top:8px">${pctBar(pct,color)}</div>`; }
function statCard(ic,lab,val,sub,extra=""){
  return `<div class="stat"><div class="lab">${ic} ${lab}</div><div class="val">${val}</div><div class="sub">${sub}</div>${extra}</div>`;
}
function syllabusMiniCard(){
  const p = state.syllabusPct;
  return statCard("📚","SYLLABUS", (p===undefined?"—":p+"%"), "chapters completed/in progress", pctBar(p||0,"#818cf8"));
}
function metricsCard(){
  const custom=state.me.summary.custom||{};
  const ms=state.me.settings.metrics.filter(m=>!m.hidden);
  const cas=state.me.settings.customActivities.filter(a=>a.enabled);
  const rows=[];
  for(const md of ms){
    const v=custom[md.key]; if(!v) continue;
    const val=md.kind==="duration"?fmtMins(v.duration):(md.kind==="check"?(v.logs?"✓ done":""):v.amount+" "+(md.unit||""));
    rows.push(`<div class="kpi"><div class="k-l">${esc(md.name)}</div><div class="k-v" style="font-size:17px">${val}</div></div>`);
  }
  for(const a of cas){
    const v=custom["__"+a.key]; if(!v) continue;
    const val=a.kind==="duration"?fmtMins(v.duration):v.amount;
    rows.push(`<div class="kpi"><div class="k-l">${esc(a.emoji)} ${esc(a.label)}</div><div class="k-v" style="font-size:17px">${val}</div></div>`);
  }
  if(!rows.length) return "";
  return `<div class="card" style="margin-top:14px"><h3>📈 TODAY'S METRICS</h3><div class="kpi-strip" style="margin-bottom:0">${rows.join("")}</div></div>`;
}
function friendCard(){
  const m=state.me, fs2=state.friends;
  const head = `<h3>🤝 SQUAD (${fs2.length}/10)
    <span class="spacer"></span><button class="btn small" onclick="App.go('chat')">💬 CHAT</button>
    <button class="btn small" onclick="App.go('duel')">⚔️ DUEL</button></h3>`;
  if(!fs2.length){
    return `<div class="card">${head}
      <div class="empty"><span class="e">🔗</span>No friends yet. Share your code, then enter theirs.
      <div class="mycode" style="font-size:26px;margin:10px 0">${m.user.code}</div>
      <div class="code-row"><input id="fc-home" placeholder="Enter friend's code" style="flex:1;padding:11px;background:var(--bg2);border:1px solid var(--border2);border-radius:10px;color:var(--txt)">
      <button class="btn-primary" onclick="App.connectHome()">CONNECT</button></div></div></div>`;
  }
  const rows = fs2.map(f=>{
    const sm=f.summary||{}, stk=f.streak?f.streak.current:0;
    const stats = [
      `${sm.execution||0}% today`,
      fmtMins(sm.minutes||0),
      `${(sm.questions||0)} Qs`,
      `🔥 ${stk}`,
      f.airFormatted?`AIR ${f.airFormatted}`:"",
      f.score!==undefined?`Prep ${Math.round(f.score)}`:"",
    ].filter(Boolean).map(x=>`<span class="sq-chip">${x}</span>`).join("");
    return `<div class="squad-row">
      <button class="squad-id" onclick="App.duelWith(${f.uid})">${av(f.name,f.avatarColor,38)}
        <div style="text-align:left;min-width:0"><div class="squad-name">${esc(f.name)} ${f.unread?`<span class="nav-badge">${f.unread}</span>`:""}</div>
        <div class="squad-stats">${stats||'<span class="muted">metrics private</span>'}</div></div></button>
      <button class="tib" title="Chat" onclick="App.openChat(${f.uid})">💬</button>
      <button class="tib" title="Duel" onclick="App.duelWith(${f.uid})">⚔️</button>
    </div>`;
  }).join("");
  return `<div class="card">${head}${rows}
    <div class="code-row" style="margin-top:10px"><input id="fc-home" placeholder="Add another friend's code" style="flex:1;padding:11px;background:var(--bg2);border:1px solid var(--border2);border-radius:10px;color:var(--txt)">
    <button class="btn" onclick="App.connectHome()">＋ ADD</button></div>
    <div class="health-note">Consistency beats cramming. This is accountability, not a race to burn out.</div></div>`;
}
function vsRow(label, a, b, max, unit, color){
  max=Math.max(1,max);
  const pa=Math.min(100,a/max*100), pb=Math.min(100,b/max*100);
  const wa=a>b, wb=b>a;
  const disp=v=>unit==="m"?fmtMins(v):(v+(unit||""));
  return `<div class="vsrow">
    <div class="vsval" style="font-weight:${wa?'800':'400'};color:${wa?'var(--yellow)':''}">${disp(a)}</div>
    <div class="vsbar"><i style="width:${pa}%;background:${color};margin-left:auto" class="${wa?'':''}"></i></div>
    <div class="vsval">${label}</div>
    <div class="vsbar"><i style="width:${pb}%;background:${color}88"></i></div>
    <div class="vsval" style="font-weight:${wb?'800':'400'};color:${wb?'var(--yellow)':''}">${disp(b)}</div></div>`;
}
async function connectHome(){
  const code=$("#fc-home").value.trim();
  try{ await api("/api/friend/connect",{nonce:nonce(),code}); toast("Connected 🤝","good"); await refresh(); }
  catch(e){ toast(e.message,"bad"); }
}
function emergency(){
  modal(`${mHead("🚨 RESET NOW")}
    <div class="summary-box"><span class="big-e">🧘</span>Stop. Reset. Start again.</div>
    <ol style="line-height:2">
      <li>Choose <b>one</b> subject — the one staring at you.</li>
      <li>Start a <b>25-minute</b> focus session.</li>
      <li>Complete <b>one small task</b> (one DPP / 10 PYQs / one revision).</li>
      <li><b>Log it.</b> Momentum restarts here.</li>
      <li>Then continue with the next smallest task.</li></ol>
    <button class="btn red full" onclick="App.startEmergency()">▶ START 25 MIN</button>`);
}
function startEmergency(){ closeModal(); go("focus"); setTimeout(()=>beginTimer(25*60,""),250); }

function editExam(){
  const d=state.me.user.exam_date||"";
  modal(`${mHead("JEE Main exam date")}
    <input type="date" id="set-exam" value="${esc(d)}">
    <div class="muted" style="margin:8px 0">Used for the countdown. Change it whenever NTA announces dates.</div>
    <button class="btn-primary full" onclick="App.saveExam()">SAVE</button>`);
}
async function saveExam(){
  const v=$("#set-exam").value;
  try{ await api("/api/me",{examDate:v||null}); toast("Saved","good"); closeModal(); await refresh(); }
  catch(e){ toast(e.message,"bad"); }
}

/* ============================================================ QUICK LOG */
function openLog(type){
  const acts=state.me.settings.activities.filter(a=>a.enabled);
  const customs=state.me.settings.customActivities.filter(a=>a.enabled);
  const metrics=state.me.settings.metrics.filter(m=>!m.hidden);
  state.wz={step:"type"};
  const tiles=acts.map(a=>`<button class="act-tile" onclick="App.wzPick('${a.key}')"><span class="e">${a.emoji}</span>${esc(a.label)}</button>`).join("");
  const ct=customs.map(a=>`<button class="act-tile" onclick="App.wzPick('${a.key}')"><span class="e">${esc(a.emoji||'✨')}</span>${esc(a.label)}</button>`).join("");
  const mt=metrics.map(m=>`<button class="act-tile" onclick="App.wzMetric('${m.key}')"><span class="e">📈</span>${esc(m.name)}</button>`).join("");
  modal(`${mHead("⚡ LOG ACTIVITY")}
    <div class="act-grid">${tiles}${ct}</div>
    ${mt?stepLabel("YOUR CUSTOM METRICS")+`<div class="act-grid">${mt}</div>`:""}
    <div class="muted" style="margin-top:14px;text-align:center">2–4 taps. No typing unless unavoidable.</div>`);
}
function actDef(key){
  const all=[...state.me.settings.activities, ...state.me.settings.customActivities];
  return all.find(a=>a.key===key) || {key,label:key,emoji:"✨",kind:"count"};
}
function wzPick(key){
  if(key==="mock"){ openMockForm(); return; }
  const d=actDef(key);
  state.wz={step:"subject", type:key, def:d, subject:"", chapter:"", amount:0, duration:0, distType:"", errType:"", note:""};
  if(key==="distraction"){ state.wz.step="distraction"; }
  wzRender();
}
function openLogDistraction(){ wzPick("distraction"); }
function wzMetric(key){
  const md=state.me.settings.metrics.find(m=>m.key===key);
  state.wz={step:"metric", type:"metric", def:{key:"metric",label:md.name,kind:md.kind,emoji:"📈"}, metric:md, amount:md.kind==="check"?1:0,duration:0};
  if(md.kind==="check"){ state.wz.step="confirm"; }
  wzRender();
}
function chapterSelect(wz, subjectAny=false){
  const opts=['<option value="">— No chapter —</option>'];
  if(subjectAny){ opts.push('<optgroup label="Recently used chapters">'); }
  const subs = (wz.subject? [wz.subject] : SUBJECTS);
  for(const sub of subs){
    const list=(state.chapters?.chapters?.[sub]||[]).filter(c=>!c.hidden);
    if(!list.length) continue;
    opts.push(`<optgroup label="${sub}">`);
    list.forEach(c=>opts.push(`<option ${wz.chapter===c.name?'selected':''}>${esc(c.name)}</option>`));
  }
  return `<select id="wz-chapter" onchange="App.wzSet('chapter',this.value)">${opts.join("")}</select>`;
}
function wzRender(){
  const wz=state.wz; if(!wz) return;
  if(wz.step==="subject"){
    const needChapter=["lecture","revision","dpp","pyq","homework","error"].includes(wz.type)||wz.def.kind==="duration"||wz.def.kind==="count";
    modal(`${mHead(wz.def.emoji+" "+wz.def.label.toUpperCase())}
      ${stepLabel("SUBJECT")}
      <div class="chips">
        ${SUBJECTS.map(s=>`<button class="chip cyan ${wz.subject===s?'sel':''}" onclick="App.wzSet('subject','${s}')">${s}</button>`).join("")}
        <button class="chip cyan ${wz.subject===''?'sel':''}" onclick="App.wzSet('subject','')">Mixed / None</button>
      </div>
      ${needChapter?stepLabel("CHAPTER (optional)")+chapterSelect(wz,true):""}
      <div style="margin-top:18px"><button class="btn-primary full" onclick="App.wzNext()">NEXT →</button></div>`);
  }
  else if(wz.step==="amount"){
    const presets=AMOUNTS[wz.type]||[5,10,20,40];
    modal(`${mHead(wz.def.emoji+" "+wz.def.label.toUpperCase())}
      ${stepLabel("HOW MANY?")}
      <div class="chips">${presets.map(n=>`<button class="chip ${wz.amount===n?'sel':''}" onclick="App.wzSet('amount',${n})">${n}</button>`).join("")}
        <button class="chip ${wz.custom?'sel':''}" onclick="App.wzCustomAmount()">Custom</button></div>
      ${wz.custom?`<div style="margin-top:10px"><input id="wz-camt" type="number" min="1" max="1000" placeholder="Enter number" inputmode="numeric"></div>`:""}
      <div class="flex" style="margin-top:18px"><button class="btn sec" onclick="App.wzBack()">← Back</button>
      <button class="btn-primary" style="flex:1" onclick="App.wzNext()">NEXT →</button></div>`);
  }
  else if(wz.step==="duration"){
    const presets=DURATIONS[wz.type]||DURATIONS.duration;
    modal(`${mHead(wz.def.emoji+" "+wz.def.label.toUpperCase())}
      ${stepLabel("DURATION")}
      <div class="chips">${presets.map(n=>`<button class="chip ${wz.duration===n?'sel':''}" onclick="App.wzSet('duration',${n})">${n}m</button>`).join("")}
        <button class="chip ${wz.custom?'sel':''}" onclick="App.wzCustomDur()">Custom</button></div>
      ${wz.custom?`<div style="margin-top:10px"><input id="wz-cdur" type="number" min="1" max="600" placeholder="Minutes" inputmode="numeric"></div>`:""}
      <div class="flex" style="margin-top:18px"><button class="btn sec" onclick="App.wzBack()">← Back</button>
      <button class="btn-primary" style="flex:1" onclick="App.wzNext()">NEXT →</button></div>`);
  }
  else if(wz.step==="distraction"){
    modal(`${mHead("📱 DISTRACTION")}
      ${stepLabel("WHAT WAS IT?")}
      <div class="chips">${DISTRACTIONS.map(x=>`<button class="chip red ${wz.distType===x?'sel':''}" onclick="App.wzSet('distType','${x}')">${x}</button>`).join("")}</div>
      ${stepLabel("HOW LONG?")}
      <div class="chips">${DURATIONS.distraction.map(n=>`<button class="chip red ${wz.duration===n?'sel':''}" onclick="App.wzSet('duration',${n})">${n}m</button>`).join("")}</div>
      <div class="flex" style="margin-top:18px"><button class="btn sec" onclick="App.wzBack()">← Back</button>
      <button class="btn red" style="flex:1" onclick="App.wzNext()">NEXT →</button></div>`);
  }
  else if(wz.step==="error"){
    modal(`${mHead("🧠 ERROR ANALYSIS")}
      ${stepLabel("ERROR TYPE")}
      <div class="chips">${ERROR_TYPES.map(x=>`<button class="chip ${wz.errType===x?'sel':''}" onclick="App.wzSet('errType','${x}')">${x}</button>`).join("")}</div>
      ${stepLabel("QUESTION NUMBER / SHORT NOTE (optional)")}
      <input id="wz-note" maxlength="300" placeholder="e.g. Q23, sign error in integration" value="${esc(wz.note)}" oninput="App.wzSet('note',this.value)">
      <div class="flex" style="margin-top:18px"><button class="btn sec" onclick="App.wzBack()">← Back</button>
      <button class="btn-primary" style="flex:1" onclick="App.wzNext()">NEXT →</button></div>`);
  }
  else if(wz.step==="metric"){
    const md=wz.metric;
    if(md.kind==="check"){
      wzConfirm(); return;
    }
    const presets = md.kind==="duration" ? DURATIONS.duration : [1,2,3,5,10];
    modal(`${mHead("📈 "+md.name.toUpperCase())}
      ${stepLabel(md.kind==="duration"?"DURATION ("+esc(md.unit||"min")+")":"AMOUNT ("+esc(md.unit||"units")+")")}
      <div class="chips">${presets.map(n=>md.kind==="duration"
        ? `<button class="chip ${wz.duration===n?'sel':''}" onclick="App.wzSet('duration',${n})">${n}m</button>`
        : `<button class="chip ${wz.amount===n?'sel':''}" onclick="App.wzSet('amount',${n})">${n}</button>`).join("")}
        <button class="chip ${wz.custom?'sel':''}" onclick="App.wzCustomMetric()">Custom</button></div>
      ${wz.custom?`<div style="margin-top:10px"><input id="wz-cmet" type="number" min="1" oninput="App.wzSet('${md.kind==='duration'?'duration':'amount'}',+this.value)" placeholder="Value"></div>`:""}
      <button class="btn-primary full" style="margin-top:18px" onclick="App.wzConfirm()">LOG IT ✓</button>`);
  }
  else if(wz.step==="confirm"){ wzConfirmRender(); }
}
function wzSet(k,v){ state.wz[k]=v; wzRender(); }
function wzCustomAmount(){ state.wz.custom=true; wzRender(); setTimeout(()=>{$("#wz-camt")?.focus();},0); }
function wzCustomDur(){ state.wz.custom=true; wzRender(); setTimeout(()=>{$("#wz-cdur")?.focus();},0); }
function wzCustomMetric(){ state.wz.custom=true; wzRender(); setTimeout(()=>{$("#wz-cmet")?.focus();},0); }
function wzBack(){
  const wz=state.wz;
  wz.custom=false;
  if(["amount","duration","distraction","error"].includes(wz.step)) wz.step="subject";
  wzRender();
}
function wzNext(){
  const wz=state.wz, d=wz.def;
  if(wz.step==="subject"){
    wz.chapter=$("#wz-chapter")?$("#wz-chapter").value:"";
    if(wz.type==="distraction"){ wz.step="distraction"; }
    else if(wz.type==="error"){
      if(!wz.subject){ toast("Pick a subject","bad"); return; }
      wz.step="error"; wz.errType="";
    }
    else if(d.kind==="count"){ wz.step="amount"; }
    else if(d.kind==="duration"){ wz.step="duration"; }
    else if(d.kind==="lecture"){ wz.step="confirm"; }
    else wz.step="confirm";
    wzRender(); return;
  }
  if(wz.step==="amount"){
    if(wz.custom){ wz.amount=Math.max(1,Math.min(1000,parseInt($("#wz-camt").value)||0)); }
    if(!wz.amount){ toast("Pick or enter an amount","bad"); return; }
    wz.step="confirm"; wzRender(); return;
  }
  if(wz.step==="duration"){
    if(wz.custom){ wz.duration=Math.max(1,Math.min(600,parseInt($("#wz-cdur").value)||0)); }
    if(!wz.duration){ toast("Pick a duration","bad"); return; }
    wz.step="confirm"; wzRender(); return;
  }
  if(wz.step==="distraction"){
    if(!wz.distType||!wz.duration){ toast("Pick type and duration","bad"); return; }
    wz.step="confirm"; wzRender(); return;
  }
  if(wz.step==="error"){
    wz.note=$("#wz-note")?$("#wz-note").value:"";
    if(!wz.errType){ toast("Pick an error type","bad"); return; }
    wz.step="confirm"; wzRender(); return;
  }
}
function wzSummary(){
  const wz=state.wz, d=wz.def;
  const sub=wz.subject?esc(wz.subject)+(wz.chapter?" — "+esc(wz.chapter):""):"General";
  let amt="";
  if(wz.type==="distraction") amt=`${wz.distType} · ${wz.duration}m`;
  else if(wz.type==="error") amt=`${wz.errType}${wz.note?' · '+esc(wz.note):''}`;
  else if(d.kind==="count") amt=`${wz.amount} questions`;
  else if(d.kind==="duration") amt=`${wz.duration} minutes`;
  else if(d.kind==="lecture") amt="Lecture completed";
  else if(wz.step==="metric"){ amt = wz.metric.kind==="duration"?`${wz.duration} ${wz.metric.unit||'min'}`:`${wz.amount} ${wz.metric.unit||''}`; }
  return `<span class="big-e">${d.emoji||"✨"}</span><b>${esc(d.label)}</b><div class="muted" style="margin-top:6px">${sub}</div><div style="margin-top:4px">${amt}</div>`;
}
function wzConfirmRender(){
  modal(`${mHead("CONFIRM LOG")}
    <div class="summary-box">${wzSummary()}</div>
    <div class="flex"><button class="btn sec" onclick="App.wzBack()">← Back</button>
    <button class="btn-green btn-primary" style="flex:1;background:var(--green);color:#052018" onclick="App.wzSave()">DONE ✓</button></div>`);
}
async function wzConfirm(){ state.wz.step="confirm"; wzConfirmRender(); }
async function wzSave(){
  const wz=state.wz;
  const body={nonce:nonce()};
  try{
    let r;
    if(wz.type==="error"){
      r=await api("/api/errors",{nonce:nonce(),subject:wz.subject,chapter:wz.chapter,errorType:wz.errType,note:wz.note});
    }else if(wz.type==="metric"){
      const md=wz.metric;
      r=await api("/api/activities",{nonce:nonce(),type:"metric",customKey:md.key,
        amount:md.kind==="duration"?0:Math.max(1,wz.amount), duration:md.kind==="duration"?Math.max(1,wz.duration):0});
    }else{
      body.type=wz.type; body.subject=wz.subject; body.chapter=wz.chapter;
      body.amount=wz.amount; body.duration=wz.duration;
      if(wz.type==="distraction"){ body.distractionType=wz.distType; body.duration=wz.duration; }
      r=await api("/api/activities",body);
    }
    closeModal();
    const xp=r.xpGained||0;
    toast(xp>0?`Logged ✓  +${xp} XP`:(xp<0?`Logged · ${xp} XP`:"Logged ✓"), xp>=0?"good":"bad");
    await autoComplete(wz.type, wz);
    await refresh();
  }catch(e){ toast(e.message,"bad"); }
}
async function autoComplete(type, wz){
  // auto tick off a matching open target from today
  const kindMap={lecture:"Lecture",dpp:"DPP",homework:"Homework",pyq:"PYQs",revision:"Revision",mock:"Mock Test",error:"Error Analysis"};
  const kind=kindMap[type]; if(!kind) return;
  try{
    const d=await api("/api/targets?date="+today());
    const t=d.targets.find(t=>t.status!=="done" && t.kind===kind &&
      (!t.subject || !wz.subject || t.subject===wz.subject) &&
      (!t.amount || (wz.amount||0)>=t.amount) &&
      (!t.duration || (wz.duration||0)>=t.duration));
    if(t){ await api("/api/targets/"+t.id,{status:"done"}); toast('Target auto-completed: '+t.title,"good"); }
  }catch(e){}
}

/* ============================================================ ANNOUNCEMENTS */
function announcementsHTML(){
  const list=state.announcements||[];
  if(!list.length) return "";
  const unread=list.filter(a=>!a.read), read=list.filter(a=>a.read);
  let h=unread.map(a=>`<div class="ann-card unread">
    <div class="ann-top"><span>📢 ANNOUNCEMENT${isAdmin()?' · from you':''}</span><span class="muted">${esc(fmtTime(a.created_at))}</span></div>
    <div class="ann-body">${esc(a.body).replace(/\n/g,"<br>")}</div>
    <button class="btn small" onclick="App.dismissAnnouncement(${a.id})">Mark as read</button>
  </div>`).join("");
  if(read.length) h+=`<details class="ann-old"><summary>📢 Earlier announcements (${read.length})</summary>`+
    read.map(a=>`<div class="ann-old-item"><div class="muted">${esc(fmtTime(a.created_at))}</div><div>${esc(a.body).replace(/\n/g,"<br>")}</div></div>`).join("")+
    `</details>`;
  return h?`<div class="ann-wrap">${h}</div>`:"";
}
async function dismissAnnouncement(id){
  await api("/api/announcements/read",{id});
  const a=state.announcements.find(x=>x.id===id); if(a) a.read=true;
  render();
}
async function postAnnouncement(){
  const ta=$("#ann-body"), body=(ta?.value||"").trim();
  if(!body){ toast("Write something first","bad"); return; }
  try{ await api("/api/announcements",{nonce:nonce(),body}); ta.value="";
    toast("Announcement posted to everyone","good");
    try{ state.announcements=(await api("/api/announcements")).announcements||[]; }catch(e){}
    renderSettings();
  }catch(e){ toast(e.message,"bad"); }
}
async function deleteAnnouncement(id){
  if(!confirm("Delete this announcement for everyone?")) return;
  await api(`/api/announcements/${id}/delete`,{});
  state.announcements=state.announcements.filter(a=>a.id!==id);
  renderSettings();
}
async function loadAdminUsers(){
  const box=document.querySelector("#adm-loading"); if(!box) return;
  try{
    const us=(await api("/api/admin/users")).users||[];
    const cc=document.querySelector("#adm-count"); if(cc) cc.textContent=`(${us.length})`;
    box.outerHTML=us.map(x=>{
      const pct=x.ch_total?Math.round(x.ch_done/x.ch_total*100):0;
      const js=JSON.stringify(x.name).replace(/'/g,"&#39;");
      return `<div class="adm-u">
        <div class="adm-u-l"><b>${esc(x.name)}</b> <span class="muted sm">@${esc(x.code)}</span>${x.role==="admin"?' <span class="adm-tag">ADMIN</span>':""}
        <div class="muted sm">${pct}% syllabus · ${x.act7} logs in last 7d · joined ${esc((x.created_at||"").slice(0,10))}${x.last_active?" · last study "+esc(x.last_active):(x.last_login?" · seen "+esc(fmtTime(x.last_login)):"")}</div></div>
        ${x.role!=="admin"?`<button class="btn small" onclick='App.adminResetPw(${x.id},"${js}")'>Reset password</button>`:'<span class="muted sm">you</span>'}
      </div>`;
    }).join("");
  }catch(e){ box.outerHTML=`<div class="muted sm">Couldn't load users: ${esc(e.message)}</div>`; }
}
async function sendReport(){
  const ta=$("#report-body"), text=(ta?.value||"").trim(), note=$("#report-sent");
  if(text.length<5){ if(note) note.textContent="Please write at least 5 characters."; return; }
  try{
    await api("/api/report",{nonce:nonce(),body:text});
    ta.value=""; if(note){ note.textContent="✅ Sent — thank you. The admin will see it."; }
    setTimeout(()=>{ if(note) note.textContent=""; },8000);
  }catch(e){ if(note) note.textContent="Couldn't send: "+e.message; }
}
async function loadAdminReports(){
  const box=document.querySelector("#rep-list"); if(!box) return;
  try{
    const d=await api("/api/admin/reports"); const rs=d.reports||[];
    const open=rs.filter(r=>!r.resolved);
    const cc=document.querySelector("#rep-count"); if(cc) cc.textContent=open.length?`(${open.length} new)`:"";
    if(!rs.length){ box.innerHTML='<div class="muted sm">No reports yet.</div>'; return; }
    box.innerHTML=rs.slice(0,30).map(r=>`<div class="adm-u ${r.resolved?"rep-done":""}">
      <div class="adm-u-l"><b>${esc(r.user_name||"User")}</b> <span class="muted sm">${esc(fmtTime(r.created_at))}</span>
      <div>${esc(r.body).replace(/\n/g,"<br>")}</div></div>
      ${r.resolved?'<span class="muted sm">✓ resolved</span>':`<button class="btn small" onclick="App.resolveReport(${r.id})">Resolve</button>`}
    </div>`).join("");
  }catch(e){ box.innerHTML=`<div class="muted sm">Couldn't load: ${esc(e.message)}</div>`; }
}
async function resolveReport(id){
  try{ await api("/api/admin/report/resolve",{id}); loadAdminReports(); }catch(e){ toast(e.message,"bad"); }
}
async function adminResetPw(id,name){
  const pw=prompt(`Set a NEW password for "${name}" (min 4 characters).\nFor security, nobody — not even the admin — can view their current password.`);
  if(pw===null) return;
  if(pw.trim().length<4){ toast("Password too short","bad"); return; }
  await api("/api/admin/set-password",{userId:id,password:pw.trim()});
  toast("New password set for "+name,"good");
}

/* ============================================================ TODAY */
async function loadTargets(){
  const d=await api("/api/targets?date="+today()); state.targets=d.targets;
}
async function renderToday(){
  await loadTargets();
  const m=state.me, s=m.summary;
  const order={open:0,partial:1,done:2,missed:3};
  const list=[...state.targets].sort((a,b)=>order[a.status]-order[b.status]||a.id-b.id);
  const tpls=m.settings.templates;
  let html=announcementsHTML();
  html+=`<div class="t-head">
    <div class="t-ring">${ringSVG(s.execution,70,"#ff7a1a",s.execution+"%","DONE")}
      <div><div style="font-weight:800;font-size:16px">TODAY'S TARGETS</div>
      <div class="muted">${s.done} done · ${s.partial} partial · ${s.planned-s.done-Math.floor(s.partial)-s.missed>0?(s.planned-s.done-s.partial-s.missed)+' open · ':''}${s.missed} missed</div></div></div>
    <button class="btn-primary" onclick="App.targetModal()">＋ ADD TARGET</button></div>`;
  html+=`<div class="tpl-row">${tpls.map(t=>`<button class="tpl" onclick="App.useTemplate('${t.id}')">⚡ ${esc(t.name)}</button>`).join("")}</div>`;
  html+=`<div class="section-gap"></div><div class="tlist">`;
  if(!list.length){
    html+=`<div class="empty"><span class="e">🎯</span>No targets for today yet.<br>Tap a day template above or add your own.</div>`;
  }
  for(const t of list){
    const meta=[t.subject?`<span class="tag ${esc(t.subject)}">${esc(t.subject)}</span>`:"",
      t.chapter?`<span class="tag">${esc(t.chapter)}</span>`:"",
      t.amount?`<span class="tag">${t.amount} Qs</span>`:"",
      t.duration?`<span class="tag">${t.duration}m</span>`:"",
      t.kind&&t.kind!=="Other"?`<span class="tag">${esc(t.kind)}</span>`:""].filter(Boolean).join("");
    const ic = t.status==="done"?"✓":(t.status==="partial"?"◐":(t.status==="missed"?"✕":""));
    html+=`<div class="titem ${t.status}">
      <button class="tcheck" onclick="App.targetStatus(${t.id},'${t.status==='done'?'open':'done'}')">${ic}</button>
      <div class="tt"><div class="t1">${esc(t.title)}</div><div class="t2">${meta}</div></div>
      <div class="tactions">
        <button class="tib" title="Partial" onclick="App.targetStatus(${t.id},'partial')">◐</button>
        <button class="tib" title="Missed" onclick="App.targetStatus(${t.id},'missed')">✕</button>
        <button class="tib" title="Duplicate" onclick="App.dupTarget(${t.id})">⧉</button>
        <button class="tib" title="Edit" onclick="App.targetModal(${t.id})">✎</button>
        <button class="tib" title="Delete" onclick="App.delTarget(${t.id})">🗑</button>
      </div></div>`;
  }
  html+=`</div>`;
  $("#view").innerHTML=html;
}
async function targetStatus(id,st){
  try{ await api("/api/targets/"+id,{status:st}); await renderToday(); await refresh(false); }catch(e){toast(e.message,"bad");}
}
async function dupTarget(id){
  try{ await api("/api/targets/"+id+"/duplicate",{nonce:nonce(),day:today()}); toast("Duplicated","good"); await renderToday(); }catch(e){toast(e.message,"bad");}
}
async function delTarget(id){
  if(!confirm("Delete this target?"))return;
  await api("/api/targets/"+id+"/delete",{}); await renderToday(); await refresh(false);
}
async function useTemplate(id){
  try{ await api("/api/targets/template",{nonce:nonce(),templateId:id,day:today()}); toast("Targets loaded — go finish them","good"); await renderToday(); await refresh(false); }
  catch(e){toast(e.message,"bad");}
}
async function targetModal(id){
  let t={kind:"Task",subject:"",chapter:"",title:"",amount:0,duration:0};
  if(id){ const all=(await api("/api/targets?date="+today())).targets; t=all.find(x=>x.id===id)||t; }
  const kinds=["Lecture","DPP","PYQs","Homework","Revision","Mock Test","Error Analysis","Other"];
  const editId=id||"null";
  modal(`${mHead(id?"EDIT TARGET":"ADD TARGET")}
    ${stepLabel("TYPE")}
    <div class="chips">${kinds.map(k=>`<button class="chip ${t.kind===k?'sel':''}" onclick="App.tmField('kind','${k}')">${k}</button>`).join("")}</div>
    ${stepLabel("SUBJECT (optional)")}
    <div class="chips">${SUBJECTS.map(s=>`<button class="chip cyan ${t.subject===s?'sel':''}" onclick="App.tmField('subject','${s}')">${s}</button>`).join("")}
      <button class="chip cyan ${!t.subject?'sel':''}" onclick="App.tmField('subject','')">None</button></div>
    ${stepLabel("CHAPTER (optional)")}
    <select id="tm-chapter"><option value="">— None —</option>${
      SUBJECTS.flatMap(sub=>(state.chapters?.chapters?.[sub]||[]).filter(c=>!c.hidden).map(c=>
        `<option ${t.chapter===c.name?'selected':''}>${esc(c.name)}</option>`)).join("")}
    </select>
    ${stepLabel("TITLE (optional — auto-filled if left blank)")}
    <input id="tm-title" maxlength="120" placeholder="e.g. Kinematics — lecture + notes" value="${esc(t.title)}">
    <div id="tm-extras"></div>
    <button class="btn-primary full" style="margin-top:16px" onclick="App.tmSave(${editId})">${id?'SAVE CHANGES':'ADD TARGET'}</button>`,
    ()=>{ window._tm=t; tmExtras(); });
}
function tmField(f,v){ window._tm[f]=v; if(f==="subject"){ const sel=$("#tm-chapter"); if(sel){ /* keep list */ } } tmExtras(); modalRerenderChips(); }
function modalRerenderChips(){
  // re-render chip selection classes cheaply: easiest is full re-render keeping title
  const t=window._tm; const title=$("#tm-title")?.value||""; t.title=title;
  // preserve chapter
  if($("#tm-chapter")) t.chapter=$("#tm-chapter").value;
  const id=t._id;
  targetModal._rerender=true;
  // simpler: update via reopening only on kind/subject click
}
function tmExtras(){
  const t=window._tm; const box=$("#tm-extras"); if(!box) return;
  let h="";
  if(t.kind==="PYQs"||t.kind==="DPP"||t.kind==="Homework"){
    h=stepLabel("QUESTION COUNT (optional)")+`<div class="chips">${[10,25,40,50,75,100].map(n=>
      `<button class="chip ${t.amount===n?'sel':''}" onclick="App.tmNum('amount',${n})">${n}</button>`).join("")}</div>`;
  }
  if(t.kind==="Revision"){
    h=stepLabel("DURATION (optional)")+`<div class="chips">${[15,30,45,60,90].map(n=>
      `<button class="chip ${t.duration===n?'sel':''}" onclick="App.tmNum('duration',${n})">${n}m</button>`).join("")}</div>`;
  }
  box.innerHTML=h;
  // update selected chips in the modal for kind/subject
  document.querySelectorAll(".modal .chip").forEach(ch=>{
    const txt=ch.textContent.trim();
    if([...["Lecture","DPP","PYQs","Homework","Revision","Mock Test","Error Analysis","Other"]].includes(txt)) ch.classList.toggle("sel",txt===t.kind);
    if(SUBJECTS.includes(txt)) ch.classList.toggle("sel",txt===t.subject);
  });
}
function tmNum(f,n){ window._tm[f]=window._tm[f]===n?0:n; tmExtras(); }
async function tmSave(id){
  const t=window._tm;
  t.title=$("#tm-title").value.trim();
  if($("#tm-chapter")) t.chapter=$("#tm-chapter").value;
  if(!t.title){ t.title=[t.subject,t.kind,t.amount?t.amount+" Qs":"",t.duration?t.duration+"m":""].filter(Boolean).join(" — "); }
  try{
    if(id){ await api("/api/targets/"+id,{title:t.title,kind:t.kind,subject:t.subject,chapter:t.chapter,amount:t.amount,duration:t.duration}); }
    else{ await api("/api/targets",{nonce:nonce(),day:today(),...t}); }
    closeModal(); toast("Saved","good"); await renderToday(); await refresh(false);
  }catch(e){ toast(e.message,"bad"); }
}

/* ============================================================ JEE CHAPTERS */
let jeeSub="Physics";
async function loadChapters(force){
  if(!state.chapters||force) state.chapters=await api("/api/chapters");
  // compute syllabus %
  let total=0,done=0,prog=0;
  for(const sub of SUBJECTS){ for(const c of (state.chapters.chapters[sub]||[])){ if(c.hidden)continue; total++;
    if(c.status==="completed"){done++;prog++;} else if(c.status==="in_progress"||c.status==="revision_needed") prog+=0.5; } }
  state.syllabusPct=total?Math.round(100*prog/total):0; state.syllabusDone=done; state.syllabusTotal=total;
}
async function renderJee(){
  await loadChapters();
  const ch=state.chapters;
  let html=`<div class="card"><h3>📚 JEE MAIN SYLLABUS</h3>
    <div class="prog-row"><span>Overall progress</span><b>${state.syllabusDone}/${state.syllabusTotal} completed · ${state.syllabusPct}%</b></div>
    ${pctBar(state.syllabusPct,"#818cf8")}
    <div class="sub-tabs" style="margin-top:14px">${SUBJECTS.map(s=>{
      const list=(ch.chapters[s]||[]).filter(c=>!c.hidden);
      const d=list.filter(c=>c.status==="completed").length;
      return `<button class="chip ${jeeSub===s?'sel cyan':''}" onclick="App.jeeTab('${s}')">${s} ${d}/${list.length}</button>`;}).join("")}</div>`;
  for(const sub of SUBJECTS){
    if(sub!==jeeSub) continue;
    const list=(ch.chapters[sub]||[]).filter(c=>!c.hidden);
    const comp=list.filter(c=>c.status==="completed").length;
    const ip=list.filter(c=>c.status==="in_progress").length;
    const p=list.length?Math.round(100*(comp+0.5*ip)/list.length):0;
    const lt=list.reduce((a,c)=>a+(c.lectures_total||0),0), ld=list.reduce((a,c)=>a+(c.lectures_done||0),0);
    html+=`<div class="prog-row" style="margin-top:6px"><span>${sub}${lt?` · 🎞 lectures ${ld}/${lt}`:""}</span><b>${p}%</b></div>${pctBar(p,sub==='Physics'?'#7dd3fc':sub==='Chemistry'?'#86efac':'#c4b5fd')}`;
  }
  html+=`<div class="flex wrap" style="margin-top:12px;gap:8px">
    <button class="btn small green" onclick="App.chBulk('completed')">✓ Mark all ${esc(jeeSub)} chapters completed</button>
    <button class="btn small" onclick="App.chBulk('in_progress')">Mark all in progress</button>
    <button class="btn small" onclick="App.chBulk('not_started')">Reset ${esc(jeeSub)} to not started</button>
  </div>
  <div class="muted" style="margin-top:6px">Tap a chapter name to set its lecture count or edit details. Tap the status pill for quick one-tap marking.</div>
  </div><div class="card">`;
  const list=(ch.chapters[jeeSub]||[]).filter(c=>!c.hidden);
  list.forEach((c)=>{
    const lec = (c.lectures_total>0)
      ? `<div class="lec-line ${c.lectures_done>=c.lectures_total?'done':''}">🎞 Lectures ${c.lectures_done}/${c.lectures_total}</div>` : "";
    html+=`<div class="ch-row">
      <button class="ch-name ch-name-btn" onclick="App.chModal(${c.id})">${esc(c.name)}${c.custom?' <span class="pill">custom</span>':''}${lec}</button>
      <div class="ch-menu">
        <button title="Move up" onclick="App.chMove(${c.id},'up')">↑</button>
        <button title="Move down" onclick="App.chMove(${c.id},'down')">↓</button>
        <button title="Edit / lectures" onclick="App.chModal(${c.id})">🎞</button>
        <button title="${c.custom?'Remove':'Hide'}" onclick="App.chHide(${c.id},${c.custom?1:0})">${c.custom?'🗑':'🚫'}</button>
      </div>
      <button class="ch-status cs-${c.status}" title="Tap to change" onclick="App.chCycle(${c.id},'${c.status}')">${STATUS_LABEL[c.status]}</button>
    </div>`;
  });
  const hidden=(ch.chapters[jeeSub]||[]).filter(c=>c.hidden);
  if(hidden.length){
    html+=`<div class="sec-title">HIDDEN (${hidden.length}) — data kept</div>`;
    hidden.forEach(c=>html+=`<div class="ch-row" style="opacity:.55"><div class="ch-name">${esc(c.name)}</div>
      <button class="btn small" onclick="App.chUnhide(${c.id})">UNHIDE</button></div>`);
  }
  html+=`<div class="flex" style="margin-top:14px">
    <input id="new-ch" class="addinput" style="flex:1" maxlength="80" placeholder="Add custom chapter/topic to ${esc(jeeSub)}">
    <button class="btn-primary" onclick="App.chAdd()">ADD</button></div>`;
  html+=`</div>`;
  $("#view").innerHTML=html;
}
const CH_CYCLE = {not_started:"completed", completed:"in_progress", in_progress:"revision_needed", revision_needed:"not_started"};
async function chBulk(status){
  const labels={completed:`mark EVERY ${jeeSub} chapter as completed`,in_progress:`mark all ${jeeSub} chapters in progress`,not_started:`reset all ${jeeSub} chapters to not started`};
  if(!confirm(`This will ${labels[status]}. Continue?`))return;
  try{ await api("/api/chapters/bulk",{nonce:nonce(),subject:jeeSub,status}); toast("Updated","good"); await renderJee(); await refresh(false); }
  catch(e){ toast(e.message,"bad"); }
}
function chModal(id){
  const c = SUBJECTS.flatMap(s=>state.chapters.chapters[s]).find(x=>x.id===id);
  if(!c) return;
  window._ch = {...c};
  const ch = window._ch;
  modal(`${mHead("📖 "+c.subject)}
    <input id="chm-name" class="addinput" maxlength="80" value="${esc(c.name)}" style="margin-bottom:12px">
    ${stepLabel("STATUS")}
    <div class="chips" id="chm-status">${(["not_started","in_progress","revision_needed","completed"]).map(st=>
      `<button class="chip ${ch.status===st?'sel':''}" onclick="App.chmField('status','${st}')">${STATUS_LABEL[st]}</button>`).join("")}</div>
    ${stepLabel("LECTURES IN THIS CHAPTER")}
    <div class="stepper-row"><span class="muted">Total lectures</span>
      <button onclick="App.chmStep('lectures_total',-1)">−</button>
      <input id="chm-total" inputmode="numeric" readonly value="${ch.lectures_total||0}">
      <button onclick="App.chmStep('lectures_total',1)">+</button></div>
    <div class="chips" style="margin:6px 0">${[5,8,10,12,15,20].map(n=>
      `<button class="chip ${(ch.lectures_total||0)===n?'sel':''}" onclick="App.chmSetTotal(${n})">${n}</button>`).join("")}</div>
    <div class="stepper-row ${ch.lectures_total?'':'hidden'}" id="chm-done-row"><span class="muted">Lectures done</span>
      <button onclick="App.chmStep('lectures_done',-1)">−</button>
      <input id="chm-done" inputmode="numeric" readonly value="${ch.lectures_done||0}">
      <button onclick="App.chmStep('lectures_done',1)">+</button></div>
    <div class="chips ${ch.lectures_total?'':'hidden'}" id="chm-done-presets">
      <button class="chip" onclick="App.chmSetDone(0)">0%</button>
      <button class="chip" onclick="App.chmSetDone(50)">50%</button>
      <button class="chip" onclick="App.chmSetDone(100)">100%</button>
    </div>
    <div class="muted" id="chm-hint">Logging a 📚 Lecture for this chapter automatically adds one done lecture.</div>
    <button class="btn-primary full" style="margin-top:12px" onclick="App.chmSave(${id})">SAVE CHAPTER</button>
    <button class="btn small danger full" style="margin-top:8px" onclick="App.chHideModal(${id},${c.custom?1:0})">${c.custom?'REMOVE':'HIDE'} CHAPTER</button>`);
}
function chmField(f,v){ window._ch[f]=v;
  document.querySelectorAll("#chm-status .chip").forEach(b=>b.classList.toggle("sel",b.textContent===STATUS_LABEL[v])); }
function chmStep(f,d){
  const ch=window._ch; ch[f]=Math.max(0,Math.min(500,(ch[f]||0)+d));
  if(f==="lectures_total" && ch.lectures_done>ch.lectures_total) ch.lectures_done=ch.lectures_total;
  if(ch.lectures_total>0){ document.getElementById("chm-done-row").classList.remove("hidden"); document.getElementById("chm-done-presets").classList.remove("hidden"); }
  document.getElementById("chm-total").value=ch.lectures_total;
  const de=document.getElementById("chm-done"); if(de) de.value=ch.lectures_done;
}
function chmSetTotal(n){ window._ch.lectures_total=n; if(window._ch.lectures_done>n)window._ch.lectures_done=n;
  document.getElementById("chm-total").value=n;
  document.getElementById("chm-done-row").classList.remove("hidden"); document.getElementById("chm-done-presets").classList.remove("hidden");
  document.getElementById("chm-done").value=window._ch.lectures_done;
  document.querySelectorAll("#chm-total")&&0; }
function chmSetDone(pct){ const ch=window._ch; if(!ch.lectures_total)return;
  ch.lectures_done=Math.round(ch.lectures_total*pct/100); document.getElementById("chm-done").value=ch.lectures_done; }
async function chmSave(id){
  const ch=window._ch; ch.name=document.getElementById("chm-name").value.trim()||ch.name;
  try{ await api("/api/chapters/"+id,{name:ch.name,status:ch.status,lecturesTotal:ch.lectures_total,lecturesDone:ch.lectures_done});
    closeModal(); toast("Chapter saved","good"); await renderJee(); await refresh(false); }
  catch(e){ toast(e.message,"bad"); }
}
async function chHideModal(id,permanent){ closeModal(); await chHide(id,permanent); }
function jeeTab(s){ jeeSub=s; renderJee(); }
async function chCycle(id,st){
  const next=CH_CYCLE[st]||"in_progress";
  try{ await api("/api/chapters/"+id,{status:next}); await renderJee(); await refresh(false); }catch(e){toast(e.message,"bad");}
}
async function chMove(id,dir){ await api("/api/chapters/"+id+"/move",{dir}); await renderJee(); }
async function chHide(id,permanent){
  if(permanent){ if(!confirm("Remove this custom chapter permanently?"))return; await api("/api/chapters/"+id,{remove:true}); }
  else await api("/api/chapters/"+id,{hidden:true});
  await renderJee();
}
async function chUnhide(id){ await api("/api/chapters/"+id,{hidden:false}); await renderJee(); }
async function chRename(id){
  const name=prompt("Rename chapter:"); if(!name)return;
  await api("/api/chapters/"+id,{name:name.slice(0,80)}); await renderJee();
}
async function chAdd(){
  const name=$("#new-ch").value.trim(); if(!name)return;
  try{ await api("/api/chapters/add",{nonce:nonce(),subject:jeeSub,name}); $("#new-ch").value=""; await renderJee(); }
  catch(e){ toast(e.message,"bad"); }
}

/* ============================================================ FOCUS TIMER */
let focusCfg={mode:25,sub:"",chap:"",custom:0};
function renderFocus(){
  const t=state.tmr;
  if(t && t.running!==undefined){ return renderFocusRunning(); }
  const modes=[25,50,90];
  $("#view").innerHTML=`<div class="card"><h3>⏱️ FOCUS TIMER</h3>
  <div class="focus-wrap">
    <div class="chips">${modes.map(m=>`<button class="chip cyan ${focusCfg.mode===m?'sel':''}" onclick="App.fMode(${m})">${m} min</button>`).join("")}
      <button class="chip cyan ${focusCfg.custom?'sel':''}" onclick="App.fCustom()">Custom</button></div>
    ${focusCfg.custom?`<input type="number" min="5" max="180" value="${focusCfg.mode}" oninput="App.fCustomSet(+this.value)" style="width:140px;text-align:center;padding:11px;background:var(--bg2);border:1px solid var(--border2);border-radius:10px;color:var(--txt)">`:''}
    ${stepLabel("SUBJECT")}
    <div class="chips">${SUBJECTS.map(s=>`<button class="chip cyan ${focusCfg.sub===s?'sel':''}" onclick="App.fSub('${s}')">${s}</button>`).join("")}
      <button class="chip cyan ${!focusCfg.sub?'sel':''}" onclick="App.fSub('')">General</button></div>
    ${stepLabel("CHAPTER (optional)")}
    <select id="f-chapter" style="width:100%;max-width:320px;padding:11px;background:var(--bg2);border:1px solid var(--border2);border-radius:10px;color:var(--txt)">
      <option value="">— None —</option>${SUBJECTS.flatMap(sub=>(state.chapters?.chapters?.[sub]||[]).filter(c=>!c.hidden).map(c=>`<option ${focusCfg.chap===c.name?'selected':''}>${esc(c.name)}</option>`)).join("")}
    </select>
    <div class="timer-dial">${dialFace(focusCfg.mode*60,focusCfg.mode*60)}
      <div class="td-center"><div class="td-time">${mmss(focusCfg.mode*60)}</div><div class="td-sub">ready</div></div></div>
    <div class="ctl-row"><button class="ctl go" onclick="App.fStart()">▶ START</button></div>
    <div class="muted">Sessions under 1 minute cannot be logged. The server verifies duration — no fake logs.</div>
  </div></div>`;
  // offer resume
  api("/api/timer/latest").then(d=>{
    if(d.timer && !d.timer.logged && (Date.now()/1000-d.timer.started) < 3*3600){
      state.tmr={id:d.timer.id,startedAt:d.timer.started,goal:25*60,paused:0,pauseAt:null,subject:d.timer.subject,chapter:d.timer.chapter,recovered:true};
      renderFocusRunning();
    }
  }).catch(()=>{});
}
function dialFace(elapsed,goal){
  const size=300,r=130,c=2*Math.PI*r;
  const frac=Math.min(1,elapsed/goal);
  return `<svg width="100%" height="100%" viewBox="0 0 ${size} ${size}">
    <circle cx="${size/2}" cy="${size/2}" r="${r}" stroke="rgba(255,255,255,.07)" stroke-width="12" fill="none"/>
    <circle id="dial-prog" cx="${size/2}" cy="${size/2}" r="${r}" stroke="#34d399" stroke-width="12" fill="none" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${c*(1-frac)}"/></svg>`;
}
function mmss(sec){ sec=Math.max(0,Math.round(sec)); return String(Math.floor(sec/60)).padStart(2,"0")+":"+String(sec%60).padStart(2,"0"); }
function fMode(m,custom){ focusCfg.custom=!!custom; focusCfg.mode=Math.max(5,Math.min(180,m|0)); renderFocus(); }
function fCustomSet(m){ focusCfg.mode=Math.max(5,Math.min(180,m|0)); }
function fCustom(){ focusCfg.custom=true; renderFocus(); }
function fSub(s){ focusCfg.sub=s; focusCfg.chap=""; renderFocus(); }
async function beginTimer(goal,sub,chap){
  const chapter=chap!==undefined?chap:(document.querySelector("#f-chapter")?.value||"");
  focusCfg.mode=Math.round(goal/60); focusCfg.sub=sub||""; focusCfg.chap=chapter;
  try{
    const d=await api("/api/timer/start",{subject:sub||"",chapter});
    state.tmr={id:d.id,startedAt:d.started,goal,paused:0,pauseAt:null,subject:sub||"",chapter};
    renderFocusRunning();
  }catch(e){ toast(e.message,"bad"); }
}
async function fStart(){
  const chap=document.querySelector("#f-chapter")?.value||"";
  await beginTimer(focusCfg.mode*60, focusCfg.sub, chap);
}
function elapsedRun(){
  const t=state.tmr; if(!t) return 0;
  const now=Date.now()/1000;
  const wall=now-t.startedAt;
  const extra=t.pauseAt? (now-t.pauseAt):0;
  return Math.max(0,wall-t.paused-extra);
}
function renderFocusRunning(){
  const t=state.tmr; if(!t){ renderFocus(); return; }
  const elapsed=elapsedRun();
  const rem=Math.max(0,t.goal-elapsed);
  const done=rem<=0;
  $("#view").innerHTML=`<div class="card"><h3>⏱️ FOCUS SESSION</h3>
    <div class="focus-wrap">
      <div class="muted">${esc(t.subject||"General")}${t.chapter?' — '+esc(t.chapter):''}</div>
      <div class="timer-dial">${dialFace(Math.min(elapsed,t.goal),t.goal)}
        <div class="td-center"><div class="td-time">${mmss(done?0:rem)}</div>
        <div class="td-sub">${done?'TIME UP':(t.pauseAt?'PAUSED':'LOCKED IN')}</div></div></div>
      <div class="ctl-row">
        ${done?`<button class="ctl go" onclick="App.fFinish()">📝 LOG SESSION</button>
                <button class="ctl sec" onclick="App.fCancel()">DISCARD</button>`
          : (t.pauseAt
            ? `<button class="ctl go" onclick="App.fResume()">▶ RESUME</button>
               <button class="ctl sec" onclick="App.fFinish()">END & LOG</button>`
            : `<button class="ctl sec" onclick="App.fPause()">⏸ PAUSE</button>
               <button class="ctl stop" onclick="App.fFinish()">END & LOG</button>`)}
      </div>
      ${t.recovered?'<div class="muted">Recovered session — pause time before refresh is counted as running.</div>':''}
    </div></div>`;
  if(done){ beep(); stopTick(); showComplete(); return; }
  stopTick();
  state.tmrTick=setInterval(()=>{
    const el=elapsedRun(), r2=Math.max(0,t.goal-el);
    const timeEl=document.querySelector(".td-time");
    if(timeEl) timeEl.textContent=mmss(r2);
    const p=document.querySelector("#dial-prog");
    if(p){ const size=300,r=130,c=2*Math.PI*r; p.setAttribute("stroke-dashoffset",c*(1-Math.min(1,el/t.goal))); }
    if(r2<=0){ stopTick(); beep(); showComplete(); }
  },250);
}
function stopTick(){ if(state.tmrTick){ clearInterval(state.tmrTick); state.tmrTick=null; } }
function fPause(){ const t=state.tmr; t.pauseAt=Date.now()/1000; renderFocusRunning(); }
function fResume(){ const t=state.tmr; t.paused+=(Date.now()/1000-t.pauseAt); t.pauseAt=null; renderFocusRunning(); }
function fCancel(){
  if(!confirm("Discard this session? It will not be logged."))return;
  stopTick(); api("/api/timer/cancel",{id:state.tmr.id}).finally(()=>{state.tmr=null;renderFocus();});
}
async function fFinish(){
  const t=state.tmr; const running=Math.round(elapsedRun());
  stopTick();
  if(running<55){ if(!confirm("Shorter than 1 minute — it can't be logged. Discard?")){renderFocusRunning();return;}
    await api("/api/timer/cancel",{id:t.id}); state.tmr=null; renderFocus(); return; }
  try{
    const r=await api("/api/timer/complete",{nonce:nonce(),id:t.id,runningSeconds:running});
    state.tmr=null;
    modal(`${mHead("SESSION COMPLETE 🎯")}
      <div class="summary-box"><span class="big-e">🎯</span><b>${r.minutes} minutes locked in</b>
      <div class="muted" style="margin-top:6px">${esc(t.subject||"General")}${t.chapter?' — '+esc(t.chapter):''}</div>
      <div style="margin-top:8px;font-weight:800;color:var(--lime)">+${r.xpGained} XP</div></div>
      <button class="btn-primary full" onclick="App.closeModal()">DONE</button>`);
    await refresh(); renderFocus();
  }catch(e){ toast(e.message,"bad"); renderFocusRunning(); }
}
function showComplete(){ renderFocusRunning(); }
function beep(){ try{ const ac=new (window.AudioContext||window.webkitAudioContext)();
  const o=ac.createOscillator(), g=ac.createGain(); o.connect(g); g.connect(ac.destination);
  o.frequency.value=880; g.gain.setValueAtTime(.15,ac.currentTime);
  g.gain.exponentialRampToValueAtTime(.001,ac.currentTime+.6); o.start(); o.stop(ac.currentTime+.6); }catch(e){} }

/* ============================================================ DUEL */
function duelWith(uid){ state.duelUid=uid; go("duel"); }
function viewDuel(){
  const m=state.me, list=state.friends;
  if(!list.length){
    return `<div class="card"><h3>⚔️ DUEL</h3><div class="empty"><span class="e">🔗</span>
      Add a friend to unlock the duel.<br>
      Your code: <b style="color:var(--accent2);letter-spacing:3px">${m.user.code}</b>
      <div class="code-row" style="margin-top:12px"><input id="fc-duel" placeholder="Friend's code" style="flex:1;padding:11px;background:var(--bg2);border:1px solid var(--border2);border-radius:10px;color:var(--txt)">
      <button class="btn-primary" onclick="App.connectDuel()">CONNECT</button></div></div></div>`;
  }
  if(!state.duelUid || !friendByUid(state.duelUid)) state.duelUid=list[0].uid;
  const f=friendByUid(state.duelUid);
  const selector = `<div class="chips" style="margin-bottom:14px">${list.map(x=>
    `<button class="chip ${x.uid===state.duelUid?'sel':''}" onclick="App.duelWith(${x.uid})">${esc(x.name)}</button>`).join("")}</div>`;
  const s=m.summary, fs=f.summary||{};
  const rows=[
    ["Today's execution",s.execution,fs.execution||0,100,"%","#ff7a1a"],
    ["Study today",s.minutes,fs.minutes||0,Math.max(60,s.minutes,fs.minutes||0),"m","#22d3ee"],
    ["Questions today",s.questions,fs.questions||0,Math.max(20,s.questions,fs.questions||0),"","#a3e635"],
    ["Streak 🔥",m.streak.current,(f.streak?f.streak.current:0),Math.max(3,m.streak.current,(f.streak?f.streak.current:0)),"d","#f472b6"],
    ["XP ⚡",m.xp.xp,(f.xp?f.xp.xp:0),Math.max(100,m.xp.xp,(f.xp?f.xp.xp:0)),"","#facc15"],
    ["Prep score",Math.round(m.air.score),Math.round(f.score||0),100,"","#818cf8"],
  ];
  let winsA=0,winsB=0;
  const body=rows.map(r=>{
    const [lab,a,b,mx,u,col]=r; const wa=a>b, wb=b>a; if(wa)winsA++; if(wb)winsB++;
    const disp=(v)=>u==="m"?fmtMins(v):v+u;
    return `<div class="vsrow">
      <div class="vsval ${wa?'winL':''}" style="font-weight:${wa?'800':'400'}">${disp(a)}</div>
      <div class="vsbar"><i style="width:${Math.min(100,a/mx*100)}%;background:${col};margin-left:auto"></i></div>
      <div class="vsval" style="font-size:11px">${lab}</div>
      <div class="vsbar"><i style="width:${Math.min(100,b/mx*100)}%;background:${col}66"></i></div>
      <div class="vsval ${wb?'winR':''}" style="font-weight:${wb?'800':'400'}">${disp(b)}</div></div>`;
  }).join("");
  // AIR: lower wins
  const airA=m.air.air, airB=f.air||600000;
  if(f.air){ if(airA<airB)winsA++; else if(airB<airA)winsB++; }
  const leader = winsA===winsB ? "TIED" : (winsA>winsB ? esc(m.user.name)+" LEADS" : esc(f.name)+" LEADS");
  return `<div class="card"><h3>⚔️ DUEL — ${leader} ${winsA===winsB?'🤝':(winsA>winsB?'👑':'')}</h3>
    ${selector}
    <div class="code-row" style="margin-bottom:12px"><input id="fc-duel" placeholder="Add another friend's code" style="flex:1;padding:10px;background:var(--bg2);border:1px solid var(--border2);border-radius:10px;color:var(--txt)">
      <button class="btn small" onclick="App.connectDuel()">＋ ADD</button></div>
    <div style="display:flex;gap:10px;justify-content:space-between;align-items:center;margin:6px 0 14px">
      <div class="vshead">${av(m.user.name,m.user.avatar_color,52)}<div class="nm">${esc(m.user.name)}</div><div class="duel-score">${winsA} rounds ${winsA>winsB?'<span class="crown">👑</span>':''}</div></div>
      <div style="font-size:26px;font-weight:900;color:var(--muted)">VS</div>
      <div class="vshead">${av(f.name,f.avatarColor,52)}<div class="nm">${esc(f.name)}</div><div class="duel-score">${winsB} rounds ${winsB>winsA?'<span class="crown">👑</span>':''}</div></div>
    </div>
    ${body}
    ${f.air?`<div class="vsrow">
      <div class="vsval ${airA<airB?'winL':''}" style="font-weight:${airA<=airB?'800':'400'}">${fmtAIR(airA)}</div>
      <div></div><div class="vsval" style="font-size:11px">Projected AIR<br><span style="font-size:9px">lower is better</span></div><div></div>
      <div class="vsval ${airB<airA?'winR':''}" style="font-weight:${airB<=airA?'800':'400'}">${fmtAIR(airB)}</div></div>`:''}
    ${f.latestMock?`<div class="muted" style="margin-top:10px">Latest mock — ${esc(f.name)}: ${f.latestMock.percent}% (${f.latestMock.accuracy}% accuracy)</div>`:''}
    <div id="duel-week"></div>
    ${!f.summary?'<div class="muted" style="margin:8px 0">Your friend keeps their daily metrics private.</div>':""}
    <div class="health-note">Scored on consistency and completion — not on who sleeps less. Recovery days protect your rank trajectory.</div></div>`;
}
async function loadDuelWeek(){
  const f=friendByUid(state.duelUid); if(!f||!f.week) return;
  try{
    if(!state.stats["7"]) state.stats["7"]=await api("/api/stats?range=7");
    const w=state.stats["7"].totals;
    const fw=f.week;
    const rows=[
      ["Week's execution",w.execution,fw.execution,100,"%","#ff7a1a"],
      ["Study this week",w.minutes,fw.minutes,Math.max(60,w.minutes,fw.minutes),"m","#22d3ee"],
      ["Questions this week",w.questions,fw.questions,Math.max(20,w.questions,fw.questions),"","#a3e635"],
    ];
    $("#duel-week").innerHTML=`<div class="sec-title">LAST 7 DAYS</div>`+rows.map(r=>{
      const [lab,a,b,mx,u,col]=r, wa=a>b, wb=b>a, disp=v=>u==="m"?fmtMins(v):v+u;
      return `<div class="vsrow">
        <div class="vsval" style="font-weight:${wa?'800':'400'};color:${wa?'var(--yellow)':''}">${disp(a)}</div>
        <div class="vsbar"><i style="width:${Math.min(100,a/mx*100)}%;background:${col};margin-left:auto"></i></div>
        <div class="vsval" style="font-size:11px">${lab}</div>
        <div class="vsbar"><i style="width:${Math.min(100,b/mx*100)}%;background:${col}66"></i></div>
        <div class="vsval" style="font-weight:${wb?'800':'400'};color:${wb?'var(--yellow)':''}">${disp(b)}</div></div>`;
    }).join("");
  }catch(e){}
}
async function connectDuel(){
  const code=$("#fc-duel").value.trim();
  try{ await api("/api/friend/connect",{nonce:nonce(),code}); toast("Connected 🤝","good"); await refresh(); }
  catch(e){ toast(e.message,"bad"); }
}

/* ============================================================ CHAT */
function openChat(uid){
  if(state.chatOpen!==uid){ state.chatMsgs=[]; }
  state.chatOpen=uid;
  if(state.view!=="chat"){ go("chat"); } else { renderChat(); }
}
function chatBack(){ state.chatOpen=null; renderChat(); }
async function clearChat(uid){
  const f=friendByUid(uid); const nm=f?f.name:"this chat";
  if(!confirm(`Delete your copy of the chat with ${nm}?\n\nThis only clears it on YOUR device — ${nm} keeps their messages.`)) return;
  try{
    await api("/api/messages/clear",{with:uid});
    if(state.chatOpen===uid){ state.chatMsgs=[]; state.chatPending[uid]=[]; }
    delete state.chatCache[uid];
    const th=(state.threads||[]).find(t=>t.uid===uid); if(th){ th.last=null; th.unread=0; }
    if(state.view==="chat") renderChat();
    toast("Chat deleted from your device","good");
  }catch(e){ toast(e.message||"Couldn't delete chat","bad"); }
}
async function renderChat(){
  stopChatPoll();
  const v=$("#view"), list=state.friends;
  if(!list.length){
    v.innerHTML=`<div class="card"><h3>💬 CHAT</h3>
      <div class="empty"><span class="e">🤝</span>Add your friend by code to start chatting.<br>
      Your code: <b style="color:var(--accent2);letter-spacing:3px">${state.me.user.code}</b>
      <div class="code-row" style="margin-top:12px"><input id="fc-chat" placeholder="Friend's code" style="flex:1;padding:11px;background:var(--bg2);border:1px solid var(--border2);border-radius:10px;color:var(--txt)">
      <button class="btn-primary" onclick="App.chatConnect()">CONNECT</button></div></div></div>`;
    return;
  }
  if(!state.chatOpen || !friendByUid(state.chatOpen)) state.chatOpen=null;
  v.innerHTML=`<div class="chat-shell">
    <div class="chat-list ${state.chatOpen?'hidden-mobile':''}" id="chat-list">${chatListHTML()}</div>
    <div class="chat-pane ${state.chatOpen?'':'hidden-mobile'}" id="chat-pane">${state.chatOpen?"<div class='empty'>Loading…</div>":`<div class="empty"><span class="e">💬</span>Pick a buddy to message.</div>`}</div>
  </div>`;
  // shell is already on screen; show cached conversation INSTANTLY, then refresh in background
  if(state.chatOpen){
    const cached=state.chatCache[state.chatOpen];
    if(cached && cached.length){
      state.chatMsgs=cached.slice();
      const pane0=document.getElementById("chat-pane");
      if(pane0){ pane0.innerHTML=chatPaneHTML(friendByUid(state.chatOpen),state.chatMsgs);
        const bd=pane0.querySelector(".msg-body"); if(bd) bd.scrollTop=bd.scrollHeight; }
    }
    loadThread(true).then(ok=>{ if(ok!==false){ state.chatCache[state.chatOpen]=state.chatMsgs; startChatPoll(); } });
    refreshThreads();
  } else { refreshThreads(); }
}
function chatListHTML(){
  const list=state.friends;
  const rows=list.map(f=>{
    const th=(state.threads||[]).find(t=>t.uid===f.uid);
    const last=th&&th.last?th.last.body:"Tap to chat";
    return `<button class="thread ${state.chatOpen===f.uid?'active':''}" onclick="App.openChat(${f.uid})">
      ${av(f.name,f.avatarColor,42)}
      <div class="thread-grow"><div class="thread-top"><b>${esc(f.name)}</b>${f.unread?`<span class="nav-badge">${f.unread}</span>`:""}</div>
      <div class="thread-last">${esc(last.slice(0,46))}${last.length>46?"…":""}</div></div></button>`;
  }).join("");
  return `<div class="chat-list-head"><h3 style="margin:0">💬 CHATS</h3>
    <button class="btn small" onclick="App.chatConnectModal()">＋</button></div>${rows}`;
}
async function refreshThreads(){
  try{
    const d=await api("/api/messages");   // one lightweight call (server joins everything)
    state.threads=d.threads||[];
    // unread badges from the thread payload (no heavy /api/friends refetch)
    for(const t of state.threads){ const f=state.friends.find(x=>x.uid===t.uid); if(f) f.unread=t.unread; }
    if(!state.chatOpen){
      const box=document.querySelector("#chat-list"); if(box) box.innerHTML=chatListHTML();
      buildNav();
    }
  }catch(e){}
}
function chatBubbleHTML(m){
  return `<div class="msg ${m.mine?'mine':'theirs'}">
    <div class="bubble">${esc(m.body).replace(/\n/g,"<br>")}<span class="msg-time">${fmtTime(m.at)}</span></div></div>`;
}
async function loadThread(mark){
  const uid=state.chatOpen; if(!uid) return true;
  const f=friendByUid(uid); if(!f) return true;
  const after = state.chatMsgs.length?state.chatMsgs[state.chatMsgs.length-1].id:0;
  let d;
  try{ d=await api(`/api/messages?with=${uid}&after=${after}`); }
  catch(e){ if(mark===true){ const p0=document.getElementById("chat-pane");
    if(p0 && state.chatOpen===uid) p0.innerHTML=`<div class="empty"><span class="e">📡</span>Slow connection — messages didn't load.<br><button class="btn-primary" style="margin-top:10px" onclick="App.loadThread(true)">RETRY</button></div>`; }
    return false; }
  const incoming=d.messages||[];
  const full=(mark===true);
  if(full) state.chatMsgs=incoming;
  else state.chatMsgs=state.chatMsgs.concat(incoming);
  if(mark || incoming.some(m=>!m.mine)) api("/api/messages/read",{with:uid}).catch(()=>{});
  f.unread=0;
  const pane=document.getElementById("chat-pane");
  if(!pane||state.chatOpen!==uid) return;
  if(full){
    pane.innerHTML=chatPaneHTML(f,state.chatMsgs);
    const body=pane.querySelector(".msg-body"); if(body) body.scrollTop=body.scrollHeight;
    const inp=pane.querySelector("#chat-text"); if(inp && mark) inp.focus({preventScroll:true});
    state.chatCache[uid]=state.chatMsgs;
  }else if(incoming.length){
    let body=pane.querySelector(".msg-body");
    if(!body || body.querySelector(".empty")){ pane.innerHTML=chatPaneHTML(f,state.chatMsgs); body=pane.querySelector(".msg-body"); }
    else{ const nearBottom=body.scrollHeight-body.scrollTop-body.clientHeight<90;
      incoming.forEach(m=>body.insertAdjacentHTML("beforeend",chatBubbleHTML(m)));
      if(nearBottom) body.scrollTop=body.scrollHeight; }
  }
  state.chatCache[uid]=state.chatMsgs;
  buildNav();
  return true;
}
function pendingBubbleHTML(uid,p){
  const click=p.status==='failed'?` onclick="App.retryChat('${p.temp}')" style="cursor:pointer"`:"";
  return `<div class="msg mine ${p.status==='failed'?'send-failed':'send-pending'}" data-pending="${p.temp}"${click}>
    <div class="bubble">${esc(p.body).replace(/\n/g,"<br>")}
      <span class="msg-time">${p.status==='failed'?'⚠️ tap to retry':'sending…'}</span></div></div>`;
}
function chatPaneHTML(f,msgs){
  const pend=(state.chatPending[f.uid]||[]).map(p=>pendingBubbleHTML(f.uid,p)).join("");
  const bubbles=(msgs.length?msgs.map(chatBubbleHTML).join(""):"")+pend
    || `<div class="empty"><span class="e">👋</span>Say hi to ${esc(f.name)}.<br>Keep each other on track.</div>`;
  return `<div class="chat-head">
      <button class="m-x" onclick="App.chatBack()">←</button>
      ${av(f.name,f.avatarColor,36)}<b>${esc(f.name)}</b>
      <span class="spacer"></span>
      <button class="btn small" title="Delete this chat on MY device only" onclick="App.clearChat(${f.uid})">🗑️</button>
      <button class="btn small" onclick="App.duelWith(${f.uid})">⚔️</button>
    </div>
    <div class="msg-body">${bubbles}</div>
    <form class="chat-input" onsubmit="return App.sendChat(event)">
      <input id="chat-text" maxlength="1000" autocomplete="off" placeholder="Message ${esc(f.name)}…">
      <button class="btn-primary" type="submit">SEND</button>
    </form>`;
}
function startChatPoll(){
  stopChatPoll();
  let ticks=0, fails=0;
  const tick=async()=>{
    if(state.view!=="chat"||!state.chatOpen){ stopChatPoll(); return; }
    const ok=await loadThread(false);
    ticks++;
    if(ok && ticks%3===0) await refreshThreads();
    // While the server/database is waking, back off 4s→8→16→30s so an open
    // chat tab doesn't pile requests onto a cold server (which delays wake).
    fails = ok ? 0 : Math.min(fails+1, 4);
    const delay = ok ? 4000 : [8000,16000,30000,30000][fails-1] || 30000;
    state.chatPoll=setTimeout(tick, delay);
  };
  state.chatPoll=setTimeout(tick, 4000);
}
function stopChatPoll(){ if(state.chatPoll){ clearTimeout(state.chatPoll); state.chatPoll=null; } }
function nowISO(){ const d=new Date(); return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,19); }
function chatDeliver(to,item){
  const node=document.querySelector(`[data-pending="${item.temp}"]`);
  return api("/api/messages",{nonce:nonce(),to,body:item.body}).then(r=>{
    state.chatMsgs.push({id:r.id,from:state.me.user.id,to,to,body:item.body,at:r.at,mine:true});
    state.chatPending[to]=(state.chatPending[to]||[]).filter(x=>x.temp!==item.temp);
    if(node) node.outerHTML=chatBubbleHTML({body:item.body,at:r.at,mine:true});
    const b=document.querySelector(".msg-body"); if(b) b.scrollTop=b.scrollHeight;
    refreshThreads();
    return true;
  }).catch(e=>{
    item.status="failed"; item.error=e.message;
    if(node){
      node.outerHTML=pendingBubbleHTML(to,{...item,status:"failed"});
      const n2=document.querySelector(`[data-pending="${item.temp}"]`);
      if(n2) n2.scrollIntoView({block:"nearest"});
    }
    toast(e.message,"bad");
    return false;
  });
}
async function sendChat(ev){
  ev.preventDefault();
  const el=document.getElementById("chat-text");
  const text=(el.value||"").trim();
  if(!text||!state.chatOpen) return false;
  const to=state.chatOpen;
  el.value=""; el.focus({preventScroll:true});
  const item={temp:"p"+Date.now()+Math.floor(Math.random()*999),body:text,at:nowISO(),status:"sending"};
  (state.chatPending[to]=state.chatPending[to]||[]).push(item);
  let body=document.querySelector(".msg-body");
  if(body && !body.querySelector(".empty")){
    body.insertAdjacentHTML("beforeend",pendingBubbleHTML(to,item)); body.scrollTop=body.scrollHeight;
  }else{ const pane=document.getElementById("chat-pane"); if(pane) pane.innerHTML=chatPaneHTML(friendByUid(to),state.chatMsgs); }
  chatDeliver(to,item);
  return false;
}
async function retryChat(temp){
  const to=state.chatOpen;
  const item=(state.chatPending[to]||[]).find(x=>x.temp===temp);
  if(!item) return;
  item.status="sending";
  const node=document.querySelector(`[data-pending="${temp}"]`);
  if(node){ node.className="msg mine send-pending"; node.querySelector(".msg-time").textContent="sending…"; }
  await chatDeliver(to,item);
}
function chatConnectModal(){
  modal(`${mHead("ADD A FRIEND")}
    <div class="muted" style="margin-bottom:8px">Your code: <b style="color:var(--accent2);letter-spacing:3px">${state.me.user.code}</b> (${state.friends.length}/10 friends)</div>
    <input id="fc-modal" placeholder="Enter their friend code" maxlength="10">
    <button class="btn-primary full" style="margin-top:12px" onclick="App.chatConnect()">CONNECT</button>`);
}
async function chatConnect(){
  const el=document.getElementById("fc-modal")||document.getElementById("fc-chat");
  const code=(el?.value||"").trim();
  try{ const r=await api("/api/friend/connect",{nonce:nonce(),code});
    toast((r.already?"Already connected with ":"Connected with ")+r.partner,"good");
    closeModal(); await refresh();
    const ff=state.friends.find(x=>x.name===r.partner);
    if(ff) openChat(ff.uid);
  }catch(e){ toast(e.message,"bad"); }
}

/* ============================================================ ANALYTICS */
let aTab="overview", aRng="30";
async function renderAnalytics(tab){
  if(tab) aTab=tab;
  const v=$("#view");
  v.innerHTML=`<div class="atabs">
    ${[["overview","Overview"],["mocks","Mock Tests"],["errors","Error Book"],["distractions","Distractions"],["weekly","Weekly"],["coach","AI Coach"]].map(
      ([id,l])=>`<button class="chip ${aTab===id?'sel':''}" onclick="App.aGo('${id}')">${l}</button>`).join("")}</div>
    <div id="a-body" class="empty">Loading…</div>`;
  const range = aTab==="overview"?aRng:"30";
  if(!state.stats[range]){ try{ state.stats[range]=await api("/api/stats?range="+range); }catch(e){ $("#a-body").innerHTML='<div class="empty">Failed to load</div>'; return; } }
  const d=state.stats[range];
  let h="";
  if(aTab==="overview"){
    h=`<div class="chips" style="margin-bottom:10px">${["7","30","all"].map(r=>
      `<button class="chip ${aRng===r?'sel cyan':''}" onclick="App.aRange('${r}')">${r==='all'?'ALL TIME':r+' DAYS'}</button>`).join("")}</div>`;
    const t=d.totals;
    h+=`<div class="kpi-strip">
      <div class="kpi"><div class="k-l">Study time</div><div class="k-v">${fmtMins(t.minutes)}</div></div>
      <div class="kpi"><div class="k-l">Questions</div><div class="k-v">${t.questions.toLocaleString()}</div></div>
      <div class="kpi"><div class="k-l">Target execution</div><div class="k-v">${t.execution}%</div></div>
      <div class="kpi"><div class="k-l">Revision</div><div class="k-v">${fmtMins(t.revision)}</div></div></div>`;
    h+=chartCard("STUDY TIME (minutes/day)", barSVG(d.series.map(x=>x.minutes),{color:"#22d3ee",labels:d.series.map((x,i)=>i%Math.ceil(d.series.length/8)===0?new Date(x.day+"T00:00").toLocaleDateString(undefined,{day:"numeric",month:"short"}):"")}));
    h+=chartCard("QUESTIONS / DAY", barSVG(d.series.map(x=>x.questions),{color:"#a3e635",labels:[]}));
    const airVals=d.airHistory.map(x=>x.score);
    if(airVals.length>1) h+=chartCard("PREPARATION SCORE (higher = better, 0–100)", lineSVG(airVals,{color:"#818cf8",yfmt:v=>Math.round(v)}));
    if(airVals.length>1){
      h+=chartCard("PROJECTED AIR (lower = better) · "+d.airHistory[0].air+" → "+d.airHistory[d.airHistory.length-1].air, lineSVG(d.airHistory.map(x=>600000-x.air),{color:"#ffb347",yfmt:v=>fmtAIR(Math.round(600000-v))}));
    }
    if(d.mocks.length){
      h+=chartCard("MOCK SCORE %", lineSVG(d.mocks.map(x=>x.percent),{color:"#f472b6"}));
    }
    if(d.subjects && Object.keys(d.subjects).length){
      h+=`<div class="card"><h3>📊 SUBJECT BREAKDOWN</h3>${Object.entries(d.subjects).map(([k,v])=>
        `<div class="prog-row"><span class="tag ${k}">${k}</span><b>${fmtMins(v.minutes)} · ${v.questions} Qs</b></div>${pctBar(Math.min(100,v.minutes/600*100),k==='Physics'?'#7dd3fc':k==='Chemistry'?'#86efac':'#c4b5fd')}`).join("")}</div>`;
    }
  }
  else if(aTab==="mocks"){ h=mocksTab(d); }
  else if(aTab==="errors"){ h=errorsTab(d); }
  else if(aTab==="distractions"){ h=distractTab(d); }
  else if(aTab==="weekly"){ h=weeklyTab(d.weekly); }
  else if(aTab==="coach"){ h=`<div class="card"><h3>🤖 AI COACH</h3><div class="muted" style="margin-bottom:8px">Rule-based observations from your own data. No motivational fluff, no rank guesses.</div>
    ${d.coach.map(c=>`<div class="insight">${esc(c)}</div>`).join("") || '<div class="empty">Log a few days of work and check back.</div>'}</div>`; }
  $("#a-body").innerHTML=h||'<div class="empty">Nothing here yet.</div>';
}
function chartCard(title,svg){ return `<div class="card"><h3>${title}</h3>${svg}</div>`; }
function aGo(t){ renderAnalytics(t); }
async function aRange(r){ aRng=r; state.stats[r]=null; renderAnalytics("overview"); }

function mocksTab(d){
  let h=`<div class="card"><h3>🧪 LOG MOCK TEST</h3>
    ${stepLabel("TEST TYPE")}<div class="chips" id="mk-types">${TEST_TYPES.map((t,i)=>`<button class="chip ${i===0?'sel':''}" onclick="App.mkChip('testType','${t}')">${t}</button>`).join("")}</div>
    <div class="field2" style="margin-top:10px">
      ${stepper("Attempted Qs","mk-att",0)}${stepper("Correct","mk-cor",0)}
      ${stepper("Incorrect","mk-inc",0)}
    </div>
    <div class="muted">Score auto-calculates as correct × 4 − incorrect × 1 (JEE Main marking). Or enter subject scores to override.</div>
    <div class="field2" style="margin-top:10px">
      <div><label class="muted">Physics /100</label><input id="mk-p" type="number" class="addinput" placeholder="auto"></div>
      <div><label class="muted">Chemistry /100</label><input id="mk-c" type="number" class="addinput" placeholder="auto"></div>
      <div><label class="muted">Maths /100</label><input id="mk-m" type="number" class="addinput" placeholder="auto"></div>
      <div><label class="muted">Date</label><input id="mk-day" type="date" class="addinput" value="${today()}"></div>
    </div>
    <button class="btn-primary full" style="margin-top:14px" onclick="App.mkSave()">LOG MOCK +50 XP</button></div>`;
  if(d.mocks.length){
    h+=`<div class="card"><h3>MOCK TREND</h3>${lineSVG(d.mocks.map(x=>x.percent),{color:"#f472b6"})}</div>`;
    h+=`<div class="card"><h3>PAST TESTS (${d.mocks.length})</h3>`;
    [...d.mocks].reverse().slice(0,20).forEach(x=>{
      h+=`<div class="mock-row"><div class="mr-top"><span>${esc(x.test_type)} · ${fmtDate(x.day)}</span>
        <span>${x.percent}% · ${x.scoreCalc}/${x.total}</span></div>
        <div class="muted">🎯 ${x.correct}/${x.attempted} correct · ${x.accuracy}% accuracy
        ${x.phys!==null?` · P ${x.phys} · C ${x.chem} · M ${x.math}`:''}
        <button class="ghostlink" style="margin-left:8px" onclick="App.delMock(${x.id})">delete</button></div></div>`;
    });
    h+=`</div>`;
  }else h+=`<div class="empty"><span class="e">🧪</span>No mocks logged yet.</div>`;
  return h;
}
function stepper(label,id,val){
  return `<div class="stepper-row"><span class="muted">${label}</span>
    <button onclick="App.step('${id}',-1)">−</button><input id="${id}" inputmode="numeric" value="${val}" readonly>
    <button onclick="App.step('${id}',1)">+</button></div>`;
}
function step(id,d){
  const el=$("#"+id); const max=id==="mk-att"?90:90;
  el.value=Math.max(0,Math.min(max,parseInt(el.value||0)+d));
  if(id==="mk-cor"||id==="mk-inc"){ const att=parseInt($("#mk-att").value||0), cor=parseInt($("#mk-cor").value||0), inc=parseInt($("#mk-inc").value||0);
    if(cor+inc>att) $("#mk-att").value=cor+inc; }
}
function mkChip(f,v){ window._mk=window._mk||{}; window._mk[f]=v;
  document.querySelectorAll("#mk-types .chip").forEach(c=>c.classList.toggle("sel",c.textContent===v)); }
async function mkSave(){
  const attempted=parseInt($("#mk-att").value||0), correct=parseInt($("#mk-cor").value||0), incorrect=parseInt($("#mk-inc").value||0);
  const p=$("#mk-p").value,c=$("#mk-c").value,m=$("#mk-m").value;
  let score=null;
  if(p!==""||c!==""||m!=="") score=(+p||0)+(+c||0)+(+m||0);
  else if(attempted) score=correct*4-incorrect;
  const tt=window._mk?.testType||"Full JEE Main";
  try{
    await api("/api/mocks",{nonce:nonce(),testType:tt,attempted,correct,incorrect,score,
      phys:p===""?null:+p,chem:c===""?null:+c,math:m===""?null:+m,day:$("#mk-day").value||today()});
    toast("Mock logged 🧪 +50 XP","good"); state.stats={}; await refresh(false); renderAnalytics("mocks");
  }catch(e){ toast(e.message,"bad"); }
}
async function delMock(id){
  if(!confirm("Delete this mock test?"))return;
  await api("/api/mocks/"+id+"/delete",{}); state.stats={}; renderAnalytics("mocks"); await refresh(false);
}

function errorsTab(d){
  const tot=Object.values(d.errorPatterns).reduce((a,b)=>a+b,0);
  let h=`<div class="card"><h3>🧠 LOG ERROR</h3>
    ${stepLabel("SUBJECT")}<div class="chips" id="er-subs">${SUBJECTS.map(s=>`<button class="chip cyan" onclick="App.erChip('subject','${s}')">${s}</button>`).join("")}</div>
    ${stepLabel("CHAPTER (optional)")}
    <select id="er-ch" class="addinput"><option value="">— None —</option>${SUBJECTS.flatMap(sub=>(state.chapters?.chapters?.[sub]||[]).map(c=>`<option>${esc(c.name)}</option>`)).join("")}</select>
    ${stepLabel("ERROR TYPE")}<div class="chips" id="er-types">${ERROR_TYPES.map(t=>`<button class="chip" onclick="App.erChip('etype','${t}')">${t}</button>`).join("")}</div>
    <input id="er-note" class="addinput" style="margin-top:10px" maxlength="300" placeholder="Q number / short note (optional)">
    <button class="btn-primary full" style="margin-top:12px" onclick="App.erSave()">LOG ERROR +25 XP</button></div>`;
  if(tot){
    h+=`<div class="card"><h3>ERROR PATTERNS</h3>`;
    Object.entries(d.errorPatterns).sort((a,b)=>b[1]-a[1]).forEach(([k,v])=>{
      const p=Math.round(100*v/tot);
      h+=`<div class="prog-row"><span>${k}</span><b>${p}% (${v})</b></div>${pctBar(p,"#fb7185")}`;
    });
    h+=`</div>`;
  }
  if(Object.keys(d.errorSubjects).length){
    h+=`<div class="card"><h3>BY SUBJECT</h3>${Object.entries(d.errorSubjects).sort((a,b)=>b[1]-a[1]).map(([k,v])=>
      `<div class="prog-row"><span class="tag ${k}">${k}</span><b>${v}</b></div>`).join("")}</div>`;
  }
  h+=`<div class="card"><h3>RECENT ERRORS</h3><div id="err-recent"><div class="muted">Loading…</div></div></div>`;
  setTimeout(loadRecentErrors,0);
  return h;
}
async function loadRecentErrors(){
  const box=$("#err-recent"); if(!box)return;
  try{
    const d=await api("/api/errors?days=60");
    if(!d.errors.length){ box.innerHTML='<div class="empty"><span class="e">🧠</span>No errors logged.</div>'; return; }
    box.innerHTML=d.errors.slice(0,30).map(e=>`<div class="err-row">
      <div class="flex" style="justify-content:space-between"><b>${esc(e.etype)}</b><span class="muted">${fmtDate(e.day)}</span></div>
      <div class="muted">${esc(e.subject||"")}${e.chapter?" — "+esc(e.chapter):""}${e.qno?" · Q"+esc(e.qno):""}
      ${e.note?" · "+esc(e.note):""}
      <button class="ghostlink" style="margin-left:6px" onclick="App.delError(${e.id})">delete</button></div></div>`).join("");
  }catch(e){ box.innerHTML='<div class="muted">Could not load.</div>'; }
}
function erChip(f,v){ window._er=window._er||{}; window._er[f]=v;
  const gid=f==="subject"?"#er-subs":"#er-types";
  document.querySelectorAll(gid+" .chip").forEach(c=>c.classList.toggle("sel",c.textContent===v)); }
async function erSave(){
  const e=window._er||{};
  if(!e.subject||!e.etype){ toast("Pick subject and error type","bad"); return; }
  try{
    await api("/api/errors",{nonce:nonce(),subject:e.subject,errorType:e.etype,
      chapter:$("#er-ch").value,note:$("#er-note").value});
    toast("Error logged 🧠 +25 XP","good"); window._er={}; state.stats={}; renderAnalytics("errors"); await refresh(false);
  }catch(err){ toast(err.message,"bad"); }
}
async function delError(id){ await api("/api/errors/"+id+"/delete",{}); state.stats={}; renderAnalytics("errors"); }

function distractTab(d){
  const x=d.distract;
  let h=`<div class="kpi-strip">
    <div class="kpi"><div class="k-l">Today</div><div class="k-v">${fmtMins(x.today)}</div></div>
    <div class="kpi"><div class="k-l">This week</div><div class="k-v">${fmtMins(x.week)}</div></div>
    <div class="kpi"><div class="k-l">This month</div><div class="k-v">${fmtMins(x.month)}</div></div>
    <div class="kpi"><div class="k-l">Most common</div><div class="k-v" style="font-size:16px">${esc(x.top||"—")}</div></div></div>`;
  h+=`<div class="weak" style="margin-bottom:14px">You lost <b>${fmtMins(x.week)}</b> to distractions this week${x.top?`, mostly <b>${esc(x.top)}</b>.`:`. One logged distraction costs 10 XP.`}</div>`;
  h+=`<button class="btn-primary full" style="margin-bottom:14px" onclick="App.openLogDistraction()">📱 LOG DISTRACTION (2 taps)</button>`;
  if(Object.keys(x.byCategory).length){
    h+=`<div class="card"><h3>BREAKDOWN (WEEK)</h3>`;
    const mx=Math.max(1,...Object.values(x.byCategory));
    Object.entries(x.byCategory).sort((a,b)=>b[1]-a[1]).forEach(([k,v])=>{
      h+=`<div class="prog-row"><span>${esc(k)}</span><b>${fmtMins(v)}</b></div>${pctBar(v/mx*100,"#fb7185")}`;
    });
    h+=`</div>`;
  }
  h+=chartCard("DISTRACTION MINUTES / DAY", barSVG(d.series.map(z=>z.distract),{color:"#fb7185",labels:d.series.map((x,i)=>i%Math.ceil(d.series.length/8)===0?new Date(x.day+"T00:00").toLocaleDateString(undefined,{day:"numeric",month:"short"}):"")}));
  return h;
}
function weeklyTab(w){
  const c=w.current,p=w.previous;
  const row=(k,l,f)=>`<tr><td>${l}</td><td>${f(c[k])}</td><td>${f(p[k])}</td></tr>`;
  const num=v=>(v||0).toLocaleString();
  return `<div class="card"><h3>📅 WEEKLY REPORT</h3>
    <div class="weak" style="margin-bottom:14px"><b>ONE major weakness:</b><br>${esc(w.weakness)}</div>
    <table class="wt"><tr><th></th><th>This week</th><th>Last week</th></tr>
      <tr><td>Targets planned</td><td>${c.targetsPlanned}</td><td>${p.targetsPlanned}</td></tr>
      <tr><td>Targets completed</td><td>${Math.round(c.targetsDone*10)/10}</td><td>${Math.round(p.targetsDone*10)/10}</td></tr>
      <tr><td>Completion</td><td><b>${c.completion}%</b></td><td>${p.completion}%</td></tr>
      <tr><td>Study time</td><td>${fmtMins(c.minutes)}</td><td>${fmtMins(p.minutes)}</td></tr>
      <tr><td>Questions</td><td>${num(c.questions)}</td><td>${num(p.questions)}</td></tr>
      <tr><td>Revision</td><td>${fmtMins(c.revision)}</td><td>${fmtMins(p.revision)}</td></tr>
      <tr><td>Mock tests</td><td>${c.mocks}</td><td>${p.mocks}</td></tr>
      <tr><td>Errors analysed</td><td>${c.errors}</td><td>${p.errors}</td></tr>
      <tr><td>Distraction time</td><td style="color:var(--red)">${fmtMins(c.distract)}</td><td>${fmtMins(p.distract)}</td></tr>
      <tr><td>Projected AIR movement</td><td colspan="2"><b class="${w.airMovement>0?'up':w.airMovement<0?'down':''}">${w.airMovement>0?'▲ improved by '+fmtAIR(w.airMovement):w.airMovement<0?'▼ worsened by '+fmtAIR(-w.airMovement):'— no change'}</b></td></tr>
    </table></div>`;
}

/* ============================================================ MOCK FORM MODAL (from quick log) */
function openMockForm(){
  state.quickMock=true;
  modal(`${mHead("🧪 MOCK TEST")}
    ${stepLabel("TEST TYPE")}<div class="chips" id="mq-types">${TEST_TYPES.map((t,i)=>`<button class="chip ${i===0?'sel':''}" onclick="App.mkChip('testType','${t}')">${t}</button>`).join("")}</div>
    <div class="field2" style="margin-top:10px">
      ${stepper("Attempted","mk-att",0)}${stepper("Correct","mk-cor",0)}${stepper("Incorrect","mk-inc",0)}
    </div>
    <div class="muted">Score auto = correct × 4 − incorrect × 1. Or fill subject scores.</div>
    <div class="field2" style="margin-top:10px">
      <div><label class="muted">Physics /100</label><input id="mk-p" type="number" class="addinput" placeholder="auto"></div>
      <div><label class="muted">Chemistry /100</label><input id="mk-c" type="number" class="addinput" placeholder="auto"></div>
      <div><label class="muted">Maths /100</label><input id="mk-m" type="number" class="addinput" placeholder="auto"></div>
      <div><label class="muted">Date</label><input id="mk-day" type="date" class="addinput" value="${today()}"></div>
    </div>
    <button class="btn-primary full" style="margin-top:14px" onclick="App.mkSaveQuick()">LOG MOCK +50 XP</button>`);
  window._mk={testType:"Full JEE Main"};
}
async function mkSaveQuick(){
  const attempted=parseInt($("#mk-att").value||0), correct=parseInt($("#mk-cor").value||0), incorrect=parseInt($("#mk-inc").value||0);
  const p=$("#mk-p").value,c=$("#mk-c").value,m=$("#mk-m").value;
  let score=null;
  if(p!==""||c!==""||m!=="") score=(+p||0)+(+c||0)+(+m||0);
  else if(attempted) score=correct*4-incorrect;
  try{
    await api("/api/mocks",{nonce:nonce(),testType:window._mk?.testType||"Full JEE Main",attempted,correct,incorrect,score,
      phys:p===""?null:+p,chem:c===""?null:+c,math:m===""?null:+m,day:$("#mk-day").value||today()});
    closeModal(); toast("Mock logged 🧪 +50 XP","good");
    await autoComplete("mock",{}); await refresh();
  }catch(e){ toast(e.message,"bad"); }
}

/* ============================================================ SETTINGS */
async function renderSettings(){
  const m=state.me, set=m.settings, u=m.user;
  let h=`<div class="card set-card"><h3>👤 PROFILE ${isAdmin()?'<span class="admin-badge">👑 ADMIN</span>':""}</h3>
    <div class="set-row"><div class="grow"><div class="muted">Name</div><b>${esc(u.name)}</b>${isAdmin()?'<div class="muted">You control the global rulebook for everyone.</div>':""}</div>
      <button class="btn small" onclick="App.setName()">EDIT</button></div>
    <div class="set-row"><div class="grow"><div class="muted">Avatar color</div>
      <div class="flex">${["#f97316","#22d3ee","#a3e635","#f472b6","#facc15","#818cf8","#34d399","#fb7185"].map(c=>
        `<button class="color-dot ${u.avatar_color===c?'sel':''}" style="background:${c}" onclick="App.setColor('${c}')"></button>`).join("")}</div></div></div>
    <div class="set-row"><div class="grow"><div class="muted">JEE Main exam date</div><b>${u.exam_date?fmtDate(u.exam_date):'not set'}</b></div>
      <button class="btn small" onclick="App.editExam()">EDIT</button></div>
    <div class="set-row"><div class="grow"><button class="btn danger btn" onclick="App.logout()">LOGOUT</button></div></div></div>`;

  // admin only: announcements + user management (user list fills in AFTER
  // the screen paints so Settings opens instantly)
  if(isAdmin()){
    const usersRows=`<div class="muted sm" id="adm-loading">Loading users…</div>`;
    const anns=(state.announcements||[]).slice(0,10).map(a=>`<div class="adm-ann"><div>${esc(a.body).replace(/\n/g,"<br>")}</div>
      <div class="muted sm">${esc(fmtTime(a.created_at))} · <a class="linklike" onclick="App.deleteAnnouncement(${a.id})">delete</a></div></div>`).join("")
      ||'<div class="muted sm">No announcements yet.</div>';
    h+=`<div class="card set-card"><h3>📢 ANNOUNCE</h3>
      <div class="muted sm" style="margin:6px 0">Goes to <b>every</b> user instantly as a single action — no matter how big the user count grows.</div>
      <textarea id="ann-body" class="addinput" rows="3" placeholder="Write an announcement for every user…" maxlength="600" style="width:100%;padding:10px;min-height:70px"></textarea>
      <button class="btn-primary" style="margin-top:8px" onclick="App.postAnnouncement()">📢 POST TO EVERYONE</button>
      <div class="adm-anns">${anns}</div></div>
    <div class="card set-card"><h3>👥 REGISTERED USERS <span class="muted" id="adm-count" style="font-weight:400;font-size:11px"></span></h3>
      <div class="muted sm" style="margin:6px 0">🔐 Passwords are stored one-way encrypted — <b>nobody, not even you, can view a password</b>. If someone forgets theirs, set a new one and tell them.</div>
      <div class="adm-ul">${usersRows}</div></div>`;
  }

  // friends
  const fl=state.friends;
  h+=`<div class="card set-card"><h3>🤝 FRIENDS (${fl.length}/10)</h3>
    <div class="set-row"><div class="grow"><div class="muted">Your invite code — share it</div><div style="font-size:24px;letter-spacing:5px;font-weight:800;color:var(--accent2)">${u.code}</div></div>
      <button class="btn small" onclick="App.copyCode()">COPY</button></div>`;
  fl.forEach(f=>{
    h+=`<div class="set-row"><div class="grow"><div class="muted">Connected</div>
      <b style="display:flex;align-items:center;gap:8px">${av(f.name,f.avatarColor,26)} ${esc(f.name)} ${f.unread?`<span class="nav-badge">${f.unread}</span>`:""}</b></div>
      <button class="btn small" onclick="App.openChat(${f.uid})">💬</button>
      <button class="btn small danger" onclick="App.unlink(${f.uid})">UNLINK</button></div>`;
  });
  if(fl.length<10){
    h+=`<div class="set-row"><input id="set-connect" class="addinput" style="flex:1" placeholder="Enter a friend's code">
      <button class="btn-primary" onclick="App.connectSettings()">CONNECT</button></div>
    <div class="muted" style="padding:8px 0">Both accounts must exist first. Either person enters the other's code — up to 10 friends.</div>`;
  }
  h+=`</div>`;

  // sharing
  const labels={studyTime:"Study time",targets:"Target completion",questions:"Questions solved",streak:"Streak",
    xp:"XP & level",score:"Preparation score",air:"Projected AIR",mocks:"Mock scores",
    distractions:"Distraction time",notes:"Personal notes",errors:"Error-book notes",reflections:"Private reflections"};
  h+=`<div class="card set-card"><h3>🔒 SHARING SETTINGS</h3>
    <div class="muted" style="margin-bottom:6px">Enforced by the server — hidden data is never sent to your friend's device.</div>
    ${Object.keys(labels).map(k=>`<div class="tog"><div class="tl">${labels[k]}<div class="td2">${set.sharing[k]?'Shared with friend':'Private'}</div></div>
      <button class="sw ${set.sharing[k]?'on':''}" onclick="App.toggle('sharing','${k}')"><i></i></button></div>`).join("")}</div>`;

  // dashboard
  h+=`<div class="card set-card"><h3>🎛️ DASHBOARD CARDS</h3>
    ${set.cards.map((c,i)=>`<div class="set-row"><div class="grow">${c.label}</div>
      <button class="btn small" onclick="App.moveCard(${i},-1)" ${i===0?'disabled':''}>↑</button>
      <button class="btn small" onclick="App.moveCard(${i},1)" ${i===set.cards.length-1?'disabled':''}>↓</button>
      <button class="sw ${c.enabled?'on':''}" onclick="App.toggleCard('${c.key}')"><i></i></button></div>`).join("")}</div>`;

  // quick actions — built-ins are global (admin only); custom activities stay personal
  const builtIns = set.activities.map(a=>`<div class="tog"><div class="tl">${a.emoji} ${a.label}</div>
      ${isAdmin()
        ? `<button class="sw ${a.enabled?'on':''}" onclick="App.toggleActivity('${a.key}')"><i></i></button>`
        : `<span class="lock-note">${a.enabled?'on':'off'} · admin</span>`}</div>`).join("");
  h+=`<div class="card set-card"><h3>⚡ QUICK ACTION TYPES ${isAdmin()?'<span class="admin-badge gold">GLOBAL</span>':'<span class="lock-note">set by admin</span>'}</h3>
    ${builtIns}
    <div class="sec-title">YOUR OWN CUSTOM ACTIVITY</div>
    <div class="flex wrap" style="gap:8px">
      <input id="ca-label" class="addinput" style="flex:1;min-width:130px" maxlength="24" placeholder="Name (e.g. Formula recall)">
      <select id="ca-kind" class="addinput"><option value="count">Counted</option><option value="duration">Timed</option></select>
      <input id="ca-xp" class="numinput" type="number" min="0" placeholder="XP">
      <button class="btn-primary" onclick="App.addActivity()">ADD</button></div>
    ${set.customActivities.length?`<div class="sec-title">YOUR ACTIVITIES</div>`+set.customActivities.map(a=>
      `<div class="set-row"><div class="grow">${esc(a.emoji)} ${esc(a.label)} <span class="pill">${a.kind}</span></div>
      <button class="btn small danger" onclick="App.removeActivity('${a.key}')">REMOVE</button></div>`).join(""):""}</div>`;

  // metrics
  h+=`<div class="card set-card"><h3>📈 CUSTOM METRICS</h3><div class="muted">Sleep, exercise, reading — track anything.</div>
    <div class="flex wrap" style="gap:8px;margin-top:8px">
      <input id="cm-name" class="addinput" style="flex:1;min-width:120px" maxlength="24" placeholder="Name (e.g. Sleep)">
      <input id="cm-unit" class="addinput" style="width:90px" maxlength="12" placeholder="Unit (hrs)">
      <select id="cm-kind" class="addinput"><option value="count">Number</option><option value="duration">Minutes</option><option value="check">Checklist</option></select>
      <input id="cm-xp" class="numinput" type="number" min="0" placeholder="XP/unit">
    </div>
    <div class="flex wrap" style="gap:14px;margin:10px 0">
      <label class="flex" style="gap:6px;font-size:13px"><input type="checkbox" id="cm-stats" checked> Stats</label>
      <label class="flex" style="gap:6px;font-size:13px"><input type="checkbox" id="cm-xpc" checked> Gives XP</label>
      <label class="flex" style="gap:6px;font-size:13px"><input type="checkbox" id="cm-air"> Affects AIR</label>
      <button class="btn-primary" onclick="App.addMetric()">ADD METRIC</button></div>
    ${set.metrics.map((mm,i)=>`<div class="set-row"><div class="grow">${esc(mm.name)} <span class="pill">${esc(mm.unit||mm.kind)}</span></div>
      <button class="btn small" onclick="App.moveMetric(${i},-1)">↑</button>
      <button class="btn small" onclick="App.moveMetric(${i},1)">↓</button>
      <button class="sw ${!mm.hidden?'on':''}" onclick="App.toggleMetric('${mm.key}')"><i></i></button>
      <button class="btn small danger" onclick="App.removeMetric('${mm.key}')">✕</button></div>`).join("")}</div>`;

  // templates
  h+=`<div class="card set-card"><h3>📋 TARGET TEMPLATES</h3>
    ${set.templates.map(t=>`<div class="set-row"><div class="grow"><b>${esc(t.name)}</b><div class="muted">${t.items.length} tasks</div></div>
      <button class="btn small" onclick="App.editTemplate('${t.id}')">EDIT</button>
      <button class="btn small danger" onclick="App.removeTemplate('${t.id}')">DEL</button></div>`).join("")}
    <button class="btn full" style="margin-top:10px" onclick="App.addTemplate()">＋ NEW TEMPLATE</button></div>`;

  // XP values — global rulebook (admin edits; everyone else sees read-only)
  const xpRows = [["lecture","Lecture completed"],["dpp","DPP completed"],["pyq25","Per 25 PYQs"],["homework","Homework set"],
       ["revision30","Per 30 min revision"],["mock","Mock test"],["error","Error analysis"],["focus25","Per 25 focus min"],
       ["day100","100% day bonus"],["distraction","Distraction penalty"],["missed","Missed target penalty"]];
  h+=`<div class="card set-card"><h3>⚡ XP VALUES ${isAdmin()?'<span class="admin-badge gold">GLOBAL</span>':'<span class="lock-note">🔒 admin-controlled</span>'}</h3>
    ${xpRows.map(([k,l])=>
      `<div class="set-row"><div class="grow">${l}</div>
        ${isAdmin()
          ? `<input class="numinput" type="number" id="xp-${k}" value="${set.xp[k]}">`
          : `<b class="ro-val">${set.xp[k]}</b>`}</div>`).join("")}
    ${isAdmin()?'<button class="btn-primary full" style="margin-top:10px" onclick="App.saveXP()">SAVE GLOBAL XP VALUES — APPLIES TO EVERYONE</button>':''}</div>`;

  // AIR formula — global rulebook
  const ro = isAdmin();
  const inp = (id,v,suffix="")=> ro ? `<input class="numinput" type="number" ${suffix?('step="'+suffix+'"'):""} id="${id}" value="${v}">` : `<b class="ro-val">${v}</b>`;
  h+=`<div class="card set-card"><h3>🎯 PROJECTED AIR — HOW IT WORKS ${isAdmin()?'<span class="admin-badge gold">GLOBAL</span>':'<span class="lock-note">🔒 admin-controlled</span>'}</h3>
    <div class="muted" style="line-height:1.7">Every day a raw 0–100 preparation score is computed from your rolling last 14 days:
    consistency, target completion, question practice, revision and mock performance — minus distraction/missed penalties.
    The daily score uses momentum: <b>new = ${Math.round((1-set.airEMA)*100)}% × today + ${Math.round(set.airEMA*100)}% × yesterday</b>,
    so one good day cannot jump your rank. AIR = <b>6,00,000 × e^(−0.064 × score)</b>, so gains get harder near the top.
    Everyone starts at AIR 6,00,000. <b>It is a preparation trajectory, not an actual JEE rank prediction.</b></div>
    <div id="air-explain" class="muted">Loading live numbers…</div>
    <div class="sec-title">WEIGHTS (must total 100 for simplest reading)</div>
    ${[["consistency","Consistency"],["targets","Target completion"],["practice","Practice"],["revision","Revision"],["mock","Mock performance"]].map(([k,l])=>
      `<div class="set-row"><div class="grow">${l}</div>${inp("w-"+k,set.weights[k])} %</div>`).join("")}
    <div class="set-row"><div class="grow">Streak target completion threshold</div>${inp("streak-thr",set.streakThreshold)} %</div>
    <div class="set-row"><div class="grow">Momentum smoothing (higher = slower AIR changes)</div>${inp("air-ema",set.airEMA,"0.01")}</div>
    ${isAdmin()?'<button class="btn-primary full" style="margin-top:10px" onclick="App.saveWeights()">SAVE GLOBAL FORMULA — APPLIES TO EVERYONE</button>':''}</div>`;

  // report a problem
  h+=`<div class="card set-card"><h3>🐞 REPORT A PROBLEM</h3>
    <div class="muted sm" style="margin:6px 0">Spotted a bug, a slow screen, or anything broken? Write it here — it goes straight to the admin${isAdmin()?" (you)":" Yash"}.</div>
    <textarea id="report-body" class="addinput" rows="3" maxlength="600" style="width:100%;min-height:74px" placeholder="What happened, and on which screen?"></textarea>
    <button class="btn-primary" style="margin-top:8px" onclick="App.sendReport()">SEND REPORT</button>
    <div id="report-sent" class="muted sm" style="margin-top:6px"></div></div>`;
  if(isAdmin()){
    h+=`<div class="card set-card"><h3>🚩 PROBLEM REPORTS <span class="muted" id="rep-count" style="font-weight:400;font-size:11px"></span></h3>
      <div id="rep-list" class="adm-ul"><div class="muted sm">Loading…</div></div></div>`;
  }

  // data
  h+=`<div class="card set-card"><h3>🗄️ DATA</h3>
    <div class="set-row"><div class="grow">Download all your data (JSON backup)</div><button class="btn small" onclick="App.exportData()">EXPORT</button></div>
    <div class="set-row"><div class="grow"><span class="danger-note">Reset everything (chapters reload, all logs deleted)</span></div>
      <button class="btn small danger" onclick="App.wipeData()">RESET</button></div></div>`;

  h+=`<div class="about-credit"><div class="about-mark">🎯 JEE WAR ROOM</div><div>Designed &amp; built by <b>Yash Sharma</b></div><div class="muted" style="font-size:11px">Two aspirants. One mission. No excuses.</div></div>`;

  $("#view").innerHTML=h;
  if(isAdmin()){ loadAdminUsers(); loadAdminReports(); }
  api("/api/air").then(a=>{
    const cc=a.components, dt=a.detail;
    $("#air-explain").innerHTML=`<div class="sec-title">YOUR LIVE COMPONENTS (last 14 days)</div>
      ${Object.entries(cc).map(([k,v])=>{const lbl={consistency:"Consistency",targets:"Targets",practice:"Practice",revision:"Revision",mock:"Mock performance"}[k];
        return `<div class="prog-row"><span>${lbl}</span><b>${Math.round(v*100)}%</b></div>${pctBar(v*100,"#22d3ee")}`;}).join("")}
      <div class="muted" style="margin-top:8px">Active days ${dt.activeDays}/14 · ${dt.questions} questions · ${dt.revMin} revision min · ${dt.distractMin} distraction min · ${dt.planned} targets planned.<br>
      Raw today: <b>${a.rawToday}</b> · Smoothed score: <b>${Math.round(a.score)}</b> · Projected AIR: <b>${a.airFormatted}</b></div>`;
  }).catch(()=>{});
}
async function setName(){
  const name=prompt("New name:",state.me.user.name); if(!name)return;
  try{ await api("/api/me",{name:name.trim()}); await refresh(); }catch(e){toast(e.message,"bad");}
}
async function setColor(c){ await api("/api/me",{avatarColor:c}); await refresh(); }
function copyCode(){ navigator.clipboard?.writeText(state.me.user.code); toast("Code copied","good"); }
async function unlink(uid){ uid=parseInt(uid); if(!uid)return;
  if(!confirm("Unlink this buddy? Your saved messages stay private; you can reconnect later with a code."))return;
  try{ await api("/api/friend/unlink",{nonce:nonce(),uid}); toast("Buddy unlinked","good");
    if(state.chatOpen==uid){ state.chatOpen=null; stopChatPoll(); }
    await refresh(); if(state.view==="chat") renderChat();
  }catch(e){ toast(e.message,"bad"); }
}
async function connectSettings(){ const code=$("#set-connect").value.trim(); try{ const r=await api("/api/friend/connect",{nonce:nonce(),code}); toast("Connected with "+r.partner,"good"); await refresh(); }catch(e){toast(e.message,"bad");} }

async function saveSetting(section,data){
  const r=await api("/api/settings",{section,data});
  if(r.settings){ state.me.settings=r.settings; state.me.global=r.global||state.me.global; }
  return r;
}
async function toggle(section,key){
  const s=state.me.settings.sharing; s[key]=!s[key];
  await saveSetting("sharing",{[key]:s[key]}); renderSettings();
  if(key==="studyTime"||key==="targets"||key==="streak"||key==="xp"||key==="score"||key==="air"||key==="mocks"){ await reloadMe(false); }
}
async function toggleCard(key){
  const cards=state.me.settings.cards; const c=cards.find(x=>x.key===key); c.enabled=!c.enabled;
  await saveSetting("cards",{enabled:Object.fromEntries(cards.map(x=>[x.key,x.enabled]))}); renderSettings();
}
async function moveCard(i,d){
  const cards=state.me.settings.cards; const j=i+d; if(j<0||j>=cards.length)return;
  [cards[i],cards[j]]=[cards[j],cards[i]];
  await saveSetting("cards",{order:cards.map(x=>x.key)}); renderSettings();
}
async function toggleActivity(key){
  const acts=state.me.settings.activities; const a=acts.find(x=>x.key===key); a.enabled=!a.enabled;
  await saveSetting("activities",{enabled:Object.fromEntries(acts.map(x=>[x.key,x.enabled]))}); renderSettings();
}
async function addActivity(){
  const label=$("#ca-label").value.trim(), kind=$("#ca-kind").value, xp=parseInt($("#ca-xp").value||"0");
  if(!label)return toast("Name it first","bad");
  await saveSetting("activities",{add:{label,kind,xp,emoji:"✨"}}); toast("Added","good"); renderSettings();
}
async function removeActivity(key){ if(!confirm("Remove this custom activity?"))return;
  await saveSetting("activities",{remove:key}); renderSettings(); }
async function addMetric(){
  const md={name:$("#cm-name").value.trim(),unit:$("#cm-unit").value.trim(),kind:$("#cm-kind").value,
    stats:$("#cm-stats").checked,xp:$("#cm-xpc").checked,air:$("#cm-air").checked,xpPer:parseInt($("#cm-xp").value||"0")};
  if(!md.name)return toast("Name it first","bad");
  await saveSetting("metrics",{act:"add",metric:md}); toast("Metric added","good"); renderSettings();
}
async function toggleMetric(key){
  const m=state.me.settings.metrics.find(x=>x.key===key); m.hidden=!m.hidden;
  await saveSetting("metrics",{act:"update",metric:{key,hidden:m.hidden}}); renderSettings();
}
async function moveMetric(i,d){
  const arr=state.me.settings.metrics, j=i+d; if(j<0||j>=arr.length)return;
  [arr[i],arr[j]]=[arr[j],arr[i]];
  await saveSetting("metrics",{act:"reorder",order:arr.map(m=>m.key)}); renderSettings();
}
async function removeMetric(key){ if(!confirm("Remove metric?"))return;
  await saveSetting("metrics",{act:"remove",metric:{key}}); renderSettings(); }

function addTemplate(){
  modal(`${mHead("NEW TEMPLATE")}
    <input id="tpl-name" class="addinput" placeholder="Template name (e.g. Light Day)">
    <div class="muted" style="margin:8px 0">One task per line, format: <b>Subject | Title | Kind | count or minutes</b><br>
    e.g. <i>Physics | Kinematics revision | Revision | 45</i></div>
    <textarea id="tpl-lines" rows="6" class="addinput" placeholder="Physics | Mechanics DPP | DPP | 25&#10;| 50 mixed PYQs | PYQs | 50"></textarea>
    <button class="btn-primary full" style="margin-top:10px" onclick="App.saveTemplate(null)">SAVE TEMPLATE</button>`);
}
async function editTemplate(id){
  const t=state.me.settings.templates.find(x=>x.id===id);
  const lines=t.items.map(i=>[i.subject||"",i.title,i.kind||"",i.amount||i.duration||""].join(" | ")).join("\n");
  modal(`${mHead("EDIT TEMPLATE")}
    <input id="tpl-name" class="addinput" value="${esc(t.name)}">
    <div class="muted" style="margin:8px 0">One task per line: <b>Subject | Title | Kind | count/minutes</b></div>
    <textarea id="tpl-lines" rows="8" class="addinput">${esc(lines)}</textarea>
    <button class="btn-primary full" style="margin-top:10px" onclick="App.saveTemplate('${id}')">SAVE</button>`);
}
async function saveTemplate(id){
  const name=$("#tpl-name").value.trim(); const lines=$("#tpl-lines").value.split("\n").map(x=>x.trim()).filter(Boolean);
  if(!name||!lines.length)return toast("Name and at least one task required","bad");
  const items=lines.map(l=>{
    const parts=l.split("|").map(x=>x.trim());
    const [sub,title,kind,q]=parts;
    const n=parseInt(q)||0;
    return {subject:SUBJECTS.includes(sub)?sub:"",title:title||kind||"Task",kind:kind||"Other",
      amount:["PYQs","DPP","Homework"].includes(kind)?n:0,duration:kind==="Revision"?n:0};
  });
  await saveSetting("templates",{act:id?"update":"add",template:{id,name,items}});
  closeModal(); toast("Template saved","good"); renderSettings();
}
async function removeTemplate(id){ if(!confirm("Delete template?"))return;
  await saveSetting("templates",{act:"remove",template:{id}}); renderSettings(); }
async function saveXP(){
  const data={}; state.me.settings.xp;
  for(const k of Object.keys(state.me.settings.xp)){ const el=$("#xp-"+k); if(el) data[k]=parseInt(el.value)||0; }
  await saveSetting("xp",data); toast("XP values saved","good"); renderSettings();
}
async function saveWeights(){
  const w={}; for(const k of Object.keys(state.me.settings.weights)) w[k]=parseInt($("#w-"+k).value)||0;
  const thr=parseInt($("#streak-thr").value)||70, ema=parseFloat($("#air-ema").value)||0.88;
  await saveSetting("weights",w); await saveSetting("general",{streakThreshold:thr,airEMA:ema});
  toast("Saved — AIR recalculates from today","good"); renderSettings();
}
async function exportData(){
  const d=await api("/api/export");
  const blob=new Blob([JSON.stringify(d,null,2)],{type:"application/json"});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="jee-war-room-backup-"+today()+".json"; a.click();
}
async function wipeData(){
  if(!confirm("This resets EVERYTHING for your account: all activities, targets, XP, streaks, AIR history AND syllabus chapter marks (61 chapters back to 'not started'). Friends and chat stay connected. Sure?"))return;
  if(!confirm("Really reset? This cannot be undone."))return;
  await api("/api/account/wipe",{}); toast("Reset complete");
  // clear every client-side cache so wiped data (incl. syllabus) re-renders fresh
  state.stats={}; state.chapters=null; state.targets=null; state.timer=null;
  await refresh();
  toast("Everything reset — syllabus included","good");
}

/* ============================================================ PUBLIC */
return {
  init, authTab, authSubmit, connectAfter, enterApp, logout, go, closeModal,
  openLog, openLogDistraction, wzPick, wzMetric, wzSet, wzCustomAmount, wzCustomDur, wzCustomMetric, wzBack, wzNext, wzConfirm, wzSave,
  targetModal, targetStatus, dupTarget, delTarget, useTemplate, tmField, tmNum, tmSave,
  jeeTab, chCycle, chMove, chHide, chUnhide, chRename, chAdd, chBulk, chModal,
  chmField, chmStep, chmSetTotal, chmSetDone, chmSave, chHideModal,
  fMode, fCustom, fCustomSet, fSub, fStart, fPause, fResume, fFinish, fCancel, emergency, startEmergency,
  editExam, saveExam, connectHome, connectDuel,
  aGo, aRange, mkChip, step, mkSave, delMock, mkSaveQuick, erChip, erSave, delError,
  duelWith, openChat, chatBack, clearChat, loadThread, sendChat, retryChat, chatConnect, chatConnectModal,
  setName, setColor, copyCode, unlink, connectSettings, toggle, toggleCard, moveCard,
  toggleActivity, addActivity, removeActivity, addMetric, toggleMetric, moveMetric, removeMetric,
  addTemplate, editTemplate, saveTemplate, removeTemplate, saveXP, saveWeights, exportData, wipeData,
  dismissAnnouncement, postAnnouncement, deleteAnnouncement, adminResetPw,
  sendReport, resolveReport,
  beginTimer,
};
})();
App.init();
