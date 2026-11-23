/* Planned-starter and week-specific scenarios. Never writes to Sleeper. */
function validateStarterPlan(players,ids,cfg) {
  if(ids.length!==cfg.slots.length||new Set(ids).size!==ids.length)throw Error('Choose one different player for every starting slot.');
  ids.forEach((id,i)=>{const p=players.find(p=>p.id===id);if(!p||!eligibleSlots(p,cfg).includes(cfg.slots[i]))throw Error('Invalid planned starter for '+cfg.slots[i]);});
  return ids;
}
function plannedTradeImpact(before,after,cfg,ids,leader,exact=false) {
  const c={...cfg,plannedStarterIds:ids,exactAvailability:exact};
  const value=ps=>jointExpectation(ps,c);
  const healthy=ps=>optimalLineup(ps,c).total;
  const out=ps=>ps.filter(p=>p.id!==leader);
  const healthy_gain=healthy(after)-healthy(before),leader_out_gain=healthy(out(after))-healthy(out(before));
  const flagged=ps=>ps.map(p=>({...p,availability:healthRisk(p)?0:p.availability}));
  const outgoing=new Set(before.filter(p=>!after.some(q=>q.id===p.id)).map(p=>p.id));
  const incoming=new Set(after.filter(p=>!before.some(q=>q.id===p.id)).map(p=>p.id));
  const stressedBefore=before.map(p=>outgoing.has(p.id)?scalePlayer(p,1+cfg.stress):p);
  const stressedAfter=after.map(p=>incoming.has(p.id)?scalePlayer(p,1-cfg.stress):p);
  return {before:value(before),after:value(after),gain:value(after)-value(before),healthy_gain,leader_out_gain,
    pessimistic:value(stressedAfter)-value(stressedBefore),health_pessimistic:value(flagged(stressedAfter))-value(flagged(stressedBefore)),
    insurance_gain:leader_out_gain-healthy_gain,health_gain:value(flagged(after))-value(flagged(before)),
    missing_starters:ids.filter(id=>!after.some(p=>p.id===id))};
}
/** Today's fantasy week on the same Monday anchor as the Lock-In tab (1 if unknown). */
function currentTradeWeek(advisor,today=new Date()) {
  const dates=(advisor?.schedule?.games||[]).map(g=>g.date).sort();
  if(!dates.length)return 1;
  const anchor=new Date(dates[0]+'T00:00:00Z');anchor.setUTCDate(anchor.getUTCDate()-(anchor.getUTCDay()+6)%7);
  const week=s=>Math.floor((s-anchor)/604800000)+1;
  return Math.max(1,Math.min(week(new Date(dates[dates.length-1]+'T00:00:00Z')),week(today)));
}
function tradeWeekBounds(advisor,week) {
  if(!Number.isInteger(week)||week<1)throw Error('Choose a valid fantasy week.');
  const first=advisor?.schedule?.games?.map(g=>g.date).sort()[0];
  if(!first)return null;
  const start=new Date(first+'T00:00:00Z');start.setUTCDate(start.getUTCDate()-(start.getUTCDay()+6)%7+7*(week-1));
  const end=new Date(start);end.setUTCDate(end.getUTCDate()+6);
  return {start:start.toISOString().slice(0,10),end:end.toISOString().slice(0,10)};
}
function weekRosterScenario(players,advisor,week,cfg,ids=[]) {
  const bounds=tradeWeekBounds(advisor,week),rows=[],missing=[];
  if(!bounds)return {available:false,reason:'No loaded schedule.'};
  for(const p of players){
    const d=advisor.players[p.id];
    const fixtures=d?advisor.schedule.games.filter(g=>g.date>=bounds.start&&g.date<=bounds.end&&(g.home===d.team||g.away===d.team)).sort((a,b)=>a.date.localeCompare(b.date)):[];
    if(!d?.sample?.length||!fixtures.length){missing.push(p.name);continue;}
    const factors=fixtures.map(g=>gameContextFactor(g,d.team,advisor.schedule.games,advisor.game_context).factor);
    // Game appearance risk is included here; do not multiply weekly availability again.
    const value=lockContinuation(d.sample,fixtures.length,d.play_probability??1,factors);
    rows.push({...p,value,availability:1,game_count:fixtures.length});
  }
  if(missing.length)return {available:false,bounds,reason:'Missing forecast or unconfirmed zero-game schedule: '+missing.join(', ')};
  const lineup=optimalLineup(rows,{...cfg,plannedStarterIds:ids});
  return {available:true,bounds,total:lineup.total,lineup,players:rows};
}
function starterSnapshot(team,week=1) {
  const snap=DATA.matchups?.[String(week)],row=snap?.rows.find(r=>String(r.roster_id)===team.team_id);
  const ids=row?.starters?.map(id=>'sleeper:'+id)||[];
  try{validateStarterPlan(roster(team),ids,DATA.cfg);return {ids,source:'Sleeper Week '+week+' starters · snapshot '+snap.retrieved_at};}
  catch{return {ids:[],source:'No valid current-roster starter snapshot for this week. Choose the starters below.'};}
}
function planControls(team) {
  const seed=starterSnapshot(team,currentTradeWeek(DATA.lock_advisor));
  return '<details open><summary>Your planned starters (local scenario only)</summary><p class="muted">'+esc(seed.source)+'. Flexible slot rearrangement is allowed when somebody is absent. This does not change your Sleeper lineup.</p><div class="toolbar">'+DATA.cfg.slots.map((slot,i)=>'<label>'+slot+(slot==='UTIL'?' '+(i-6):'')+'<select class="planned-starter" data-slot="'+i+'"><option value="">Choose player</option>'+roster(team).filter(p=>p&&eligibleSlots(p,DATA.cfg).includes(slot)).map(p=>'<option value="'+esc(p.id)+'" '+(seed.ids[i]===p.id?'selected':'')+'>'+esc(p.name)+'</option>').join('')+'</select></label>').join('')+'</div></details>';
}
function readStarterPlan(team,cfg) {
  return validateStarterPlan(roster(team),[...document.querySelectorAll('.planned-starter')].map(el=>el.value),cfg);
}
function weekTradeTable(beforeA,afterA,beforeB,afterB,cfg,ids,other,week) {
  const otherIds=starterSnapshot(other,week).ids;
  const calc=(before,after,plan)=>{
    const b=weekRosterScenario(before,DATA.lock_advisor,week,cfg,plan),a=weekRosterScenario(after,DATA.lock_advisor,week,cfg,plan);
    return a.available&&b.available?[fmt(b.total),fmt(a.total),fmt(a.total-b.total)]:['Unavailable','Unavailable',a.reason||b.reason];
  };
  return '<h4>Week '+week+' · pre-week lineup scenario</h4>'+table(['Side','Before FP','After FP','Change FP'],[['Your '+(ids.length?'planned':'optimized')+' starters',...calc(beforeA,afterA,ids)],[other.manager+' '+(otherIds.length?'API starters':'optimized starters'),...calc(beforeB,afterB,otherIds)]])+'<p class="muted">Choose starters before games, then use chronological lock/wait decisions. Appearance risk is included once. This is separate from season-average trade value, not live lock eligibility or win probability. Missing fixtures withhold the estimate. September injury flags do not establish October availability.</p>';
}
function tradeResearchStatus(players,reviews={},now=new Date()) {
  const days=d=>(now-new Date(d))/86400000;
  return players.map(p=>{const r=reviews[p.id]||{},age=days(r.reviewed_at);
    const fresh=Number.isFinite(age)&&age>=0&&age<=7;
    const safe=url=>{try{return new URL(url).protocol==='https:';}catch{return false;}};
    return {id:p.id,name:p.name,role:fresh&&safe(r.role_source)&&r.role_note?r.role_note:'Role review missing or older than 7 days',
      role_source:fresh&&safe(r.role_source)?r.role_source:null,
      market:fresh&&safe(r.market_source)&&r.market_note?r.market_note:'Current market-price review missing',
      market_source:fresh&&safe(r.market_source)?r.market_source:null,
      complete:!!(fresh&&safe(r.role_source)&&r.role_note&&safe(r.market_source)&&r.market_note)};
  });
}
function researchTable(players) {
  const rows=tradeResearchStatus(players,DATA.trade_reviews||{});
  return '<h4>Role and market checks</h4>'+table(['Player','Role evidence','Market evidence'],rows.map(r=>[r.name,r.role,r.market]))+
    rows.filter(r=>r.role_source||r.market_source).map(r=>'<p>'+esc(r.name)+': '+[['Role source',r.role_source],['Market source',r.market_source]].filter(x=>x[1]).map(([label,url])=>'<a target="_blank" rel="noopener noreferrer" href="'+esc(url)+'">'+label+'</a>').join(' · ')+'</p>').join('')+
    '<p class="warning">'+(rows.every(r=>r.complete)?'Sources reviewed; prices remain opinions.':'Research incomplete: exploratory candidate, not a verified fair offer.')+' Individual model points are not a market price. No acceptance probability is calculated.</p>';
}
