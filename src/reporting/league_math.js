/* Two-sided wrapper around LINEUP_JS. Python parity: test_league_integration.py. */
function healthRisk(p){return !!p.health_risk || !['','healthy','available','active'].includes((p.injury_status||'').trim().toLowerCase());}
function tradeHealthNotice(result){
  const risks={...result.team_a.injury_risks,...result.team_b.injury_risks};
  if(!Object.keys(risks).length)return '<p class="muted">No injury flag in the stored snapshot is not verified medical clearance.</p>';
  return '<p class="warning">Injury uncertainty: '+esc(Object.entries(risks).map(([id,status])=>name(id)+' ('+status+')').join(', '))+'. Base values use historical availability, not a verified recovery timeline. Provisional gains if every flagged player is unavailable together: '+fmt(result.team_a.health_delta)+' / '+fmt(result.team_b.health_delta)+' FP/week (first / second team); also applying pessimistic projection stress: '+fmt(result.team_a.health_pessimistic)+' / '+fmt(result.team_b.health_pessimistic)+'. This is a sensitivity test, not a forecast that they miss the season. Snapshot: '+esc(DATA.ownership_observed_at)+'.</p>';
}
function trimLeagueRoster(roster, cfg) {
  let after = roster.slice(), cuts = [];
  while (after.length > cfg.rosterLimit) {
    const ranks = after.map(p => ({p, value:Math.round(rosterStrength(after.filter(q=>q.id!==p.id),cfg).expected*1e8)/1e8}));
    ranks.sort((a,b)=>b.value-a.value || (a.p.id<b.p.id?1:a.p.id>b.p.id?-1:0));
    cuts.push(ranks[0].p); after=after.filter(p=>p.id!==ranks[0].p.id);
  }
  return {after,cuts};
}
function bestLeagueAdd(roster,pool,cfg) {
  const before=rosterStrength(roster,cfg).expected;
  const candidates=pool.filter(p=>!healthRisk(p)&&!roster.some(q=>q.id===p.id)).map(p=>({p,gain:rosterStrength(roster.concat(p),cfg).expected-before}));
  candidates.sort((a,b)=>(Math.round(b.gain*1e8)-Math.round(a.gain*1e8)) || (a.p.id<b.p.id?1:a.p.id>b.p.id?-1:0));
  return candidates[0] || null;
}
function fillLeagueSlots(beforeCount,roster,pool,cfg) {
  const after=roster.slice(), adds=[];
  const n=Math.min(cfg.rosterLimit-after.length,Math.max(0,beforeCount-after.length));
  for(let i=0;i<n;i++) {
    const best=bestLeagueAdd(after,pool,cfg);
    if(!best || best.gain < -1e-8) break;
    adds.push(best.p); after.push(best.p);
  }
  return {after,adds};
}
function leagueSide(before,after,give,get,cuts,adds,cfg) {
  // Players on both rosters (e.g. a claim also in the free-move baseline) are not stressed.
  const kept=new Set(before.map(p=>p.id));
  const incoming=new Set(get.concat(adds).map(p=>p.id).filter(id=>!kept.has(id))),outgoing=new Set(give.concat(cuts).map(p=>p.id));
  const b=rosterStrength(before,cfg),a=rosterStrength(after,cfg);
  function stress(sign) {
    const br=before.map(p=>outgoing.has(p.id)?scalePlayer(p,1+sign*cfg.stress):p);
    const ar=after.map(p=>incoming.has(p.id)?scalePlayer(p,1-sign*cfg.stress):p);
    return rosterStrength(ar,cfg).expected-rosterStrength(br,cfg).expected;
  }
  const injury_risks=Object.fromEntries(before.concat(after).filter(healthRisk).map(p=>[p.id,p.injury_status||'Unresolved']));
  function healthDelta(sign=0){
    const transform=(p,ids,factor)=>({...scalePlayer(p,ids.has(p.id)?factor:1),availability:healthRisk(p)?0:p.availability});
    return jointExpectation(after.map(p=>transform(p,incoming,1-sign*cfg.stress)),cfg)-jointExpectation(before.map(p=>transform(p,outgoing,1+sign*cfg.stress)),cfg);
  }
  return {before:b.expected,after:a.expected,delta_weekly:a.expected-b.expected,
    injury_risks,health_delta:Object.keys(injury_risks).length?healthDelta():a.expected-b.expected,
    health_pessimistic:Object.keys(injury_risks).length?healthDelta(1):stress(1),
    traded_injury_risk:give.concat(get,cuts,adds).some(healthRisk),
    delta_starters:a.healthy-b.healthy,delta_depth:b.depthCost-a.depthCost,
    delta_season:(a.expected-b.expected)*cfg.weeksRemaining,pessimistic:stress(1),optimistic:stress(-1),
    drops:cuts.map(p=>p.id),pickups:adds.map(p=>p.id),after_roster:after};
}
function classifyLeague(a,b) {
  if(a.traded_injury_risk||b.traded_injury_risk)return 'INJURY REVIEW REQUIRED';
  function sign(r){return Math.min(r.pessimistic,r.health_pessimistic??r.pessimistic)>1e-8?1:r.optimistic < -1e-8?-1:0;}
  return {'1,1':'WIN-WIN','1,-1':'FAVOURS TEAM A','-1,1':'FAVOURS TEAM B','-1,-1':'LOSE-LOSE'}[[sign(a),sign(b)].join(',')] || 'TOO CLOSE TO CALL';
}
function evaluateLeagueTrade(a,b,giveA,giveB,pool,cfg,replacements=true,exact=true) {
  cfg={...cfg,exactAvailability:exact};
  if(!Number.isFinite(cfg.stress)||cfg.stress<0||cfg.stress>1||!Number.isFinite(cfg.weeksRemaining)||cfg.weeksRemaining<0) throw Error('Invalid stress or weeks');
  if(a.length>cfg.rosterLimit||b.length>cfg.rosterLimit) throw Error('Resolve IR/over-limit roster');
  optimalLineup(a,cfg);optimalLineup(b,cfg);optimalLineup(pool,cfg);
  const owned=new Set(a.concat(b).map(p=>p.id));
  if(owned.size!==a.length+b.length || pool.some(p=>owned.has(p.id))) throw Error('Duplicate ownership or owned free agent');
  if(new Set(giveA).size!==giveA.length||new Set(giveB).size!==giveB.length||giveA.some(id=>!a.some(p=>p.id===id))||giveB.some(id=>!b.some(p=>p.id===id))) throw Error('Invalid outgoing ownership');
  const outA=a.filter(p=>giveA.includes(p.id)),outB=b.filter(p=>giveB.includes(p.id));
  const ta=trimLeagueRoster(a.filter(p=>!giveA.includes(p.id)).concat(outB),cfg);
  const tb=trimLeagueRoster(b.filter(p=>!giveB.includes(p.id)).concat(outA),cfg);
  const fa=replacements?fillLeagueSlots(a.length,ta.after,pool.concat(tb.cuts),cfg):{after:ta.after,adds:[]};
  const fb=replacements?fillLeagueSlots(b.length,tb.after,pool.concat(ta.cuts).filter(p=>!fa.adds.some(q=>q.id===p.id)),cfg):{after:tb.after,adds:[]};
  // A claim is only trade value beyond the add/drop the side could make anyway,
  // so a side receiving claims is compared with its roster after as many free moves.
  const baseA=fa.adds.length?freeLeagueMoves(a,pool,fa.adds,cfg):{after:a,moves:[]};
  const baseB=fb.adds.length?freeLeagueMoves(b,pool,fb.adds,cfg):{after:b,moves:[]};
  const ra=leagueSide(baseA.after,fa.after,outA,outB,ta.cuts,fa.adds,cfg),rb=leagueSide(baseB.after,fb.after,outB,outA,tb.cuts,fb.adds,cfg);
  if(fa.adds.length)ra.baseline_free_moves=baseA.moves;
  if(fb.adds.length)rb.baseline_free_moves=baseB.moves;
  return {team_a:ra,team_b:rb,classification:classifyLeague(ra,rb),availability_integration:exact?'Exact joint independent weekly availability':'512-scenario screening approximation'};
}
const freeMoveCache=new WeakMap();
/** Up to claims.length positive standalone add/drops, searched over the top-valued
 * free agents plus the claimed players (a full-pool search is too slow per trade). */
function freeLeagueMoves(roster,pool,claims,cfg) {
  const key=[cfg.exactAvailability,claims.length,roster.map(p=>p.id).sort().join(','),claims.map(p=>p.id).sort().join(',')].join('|');
  const cache=freeMoveCache.get(pool)||new Map();freeMoveCache.set(pool,cache);
  if(cache.has(key))return cache.get(key);
  const top=pool.slice().sort((x,y)=>y.value-x.value||(x.id<y.id?-1:x.id>y.id?1:0)).slice(0,10);
  const candidates=top.concat(claims.filter(p=>pool.some(q=>q.id===p.id)&&!top.some(q=>q.id===p.id)));
  let after=roster;const moves=[];
  for(let i=0;i<claims.length;i++){
    const best=leagueAddDrops(after,candidates.filter(p=>!after.some(q=>q.id===p.id)),cfg,1)[0];
    if(!best||best.delta_weekly<=1e-8)break;
    const trial=trimLeagueRoster(after.concat(candidates.find(p=>p.id===best.add)),cfg);
    after=trial.after;moves.push({add:best.add,drop:best.drop,gain:best.delta_weekly});
  }
  const result={after,moves};cache.set(key,result);return result;
}
/** Evaluate one simultaneous, player-only trade among exactly three active rosters. */
function evaluateLeagueThreeWay(rosters,transfers,cfg,exact=true) {
  const settings={...cfg,exactAvailability:exact};
  if(rosters.length!==3||rosters.some(r=>!Array.isArray(r)||r.length>settings.rosterLimit))throw Error('Choose three valid active rosters within the roster limit.');
  if(!Number.isFinite(settings.stress)||settings.stress<0||settings.stress>1||!Number.isFinite(settings.weeksRemaining)||settings.weeksRemaining<0)throw Error('Invalid stress or weeks.');
  const all=rosters.flat(),owners=new Map();
  rosters.forEach((r,i)=>r.forEach(p=>{
    if(!p||!p.id||owners.has(p.id))throw Error('Duplicate or missing player ownership.');
    owners.set(p.id,i);
  }));
  rosters.forEach(r=>optimalLineup(r,settings));
  if(!Array.isArray(transfers)||transfers.length<3)throw Error('Route at least one player out of each team.');
  const sent=[[],[],[]],received=[[],[],[]],used=new Set();
  for(const route of transfers){
    const {from,to,playerId}=route;
    if(!Number.isInteger(from)||!Number.isInteger(to)||from<0||from>2||to<0||to>2||from===to||owners.get(playerId)!==from||used.has(playerId))throw Error('Every routed player must have one owner and a different destination.');
    used.add(playerId);
    const p=rosters[from].find(q=>q.id===playerId);
    sent[from].push(p);received[to].push(p);
  }
  if(sent.some(r=>!r.length)||received.some(r=>!r.length))throw Error('All three teams must send and receive at least one player.');
  return rosters.map((before,i)=>{
    const trial=before.filter(p=>!used.has(p.id)).concat(received[i]);
    const trimmed=trimLeagueRoster(trial,settings);
    return {...leagueSide(before,trimmed.after,sent[i],received[i],trimmed.cuts,[],settings),sent:sent[i].map(p=>p.id),received:received[i].map(p=>p.id)};
  });
}
function leagueAddDrops(roster,pool,cfg,top=20) {
  const baseline=rosterStrength(roster,cfg).expected;
  const rows=[];
  for(const incoming of pool) {
    if(healthRisk(incoming))continue;
    if(roster.some(p=>p.id===incoming.id))continue;
    const trial=trimLeagueRoster(roster.concat(incoming),cfg);
    if(!trial.after.some(p=>p.id===incoming.id))continue;
    rows.push({add:incoming.id,drop:trial.cuts.map(p=>p.id),delta_weekly:rosterStrength(trial.after,cfg).expected-baseline});
  }
  return rows.sort((a,b)=>b.delta_weekly-a.delta_weekly || (a.add<b.add?-1:a.add>b.add?1:0)).slice(0,top);
}
