/* Read-only offline views. All strings from data are escaped before HTML insertion. */
const DATA=JSON.parse(document.getElementById('league-data').textContent);
DATA.trade_reviews=TRADE_REVIEWS;
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>v==null?'—':Number(v).toFixed(2);
const name=id=>DATA.players[id]?.name||DATA.identities[id]?.name||id;
const names=ids=>ids.map(name).join(', ')||'None';
const table=(headers,rows)=>'<div class="scroll"><table><thead><tr>'+headers.map(x=>'<th scope="col">'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(x=>'<td>'+esc(x)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
const views=['My Team','League','Moves','Lock-In'];
const moveViews={'Automatic Trades':'Trade ideas','Trade Evaluator':'Check an offer','Three-Team Trade':'Check a 3-team trade','Free Agents':'Free agents'};
const mainView=()=>['Power Rankings','League Rosters','Team Strengths'].includes(activeView)?'League':Object.hasOwn(moveViews,activeView)||activeView==='Trade Partners'?'Moves':activeView==='Lock-In Advisor'?'Lock-In':'My Team';
let activeView='My Team',selected=DATA.teams.find(t=>t.manager===DATA.my_team)?.team_id||DATA.teams[0].team_id;
const selectedTeam=()=>DATA.teams.find(t=>t.team_id===selected);
const roster=(t,horizon='regular')=>t.roster.map(id=>(horizon==='regular'?DATA.players:DATA.playoff_players)[id]);
const pool=(horizon='regular')=>(horizon==='regular'?DATA.free_agents:DATA.playoff_free_agents).map(id=>(horizon==='regular'?DATA.players:DATA.playoff_players)[id]);
const blocked=t=>t.issues.length?'<p class="warning">Analytics unavailable: '+esc(t.issues.join('; '))+'. No missing player is assigned a zero projection.</p>':'';
const metric=(label,value)=>'<div class="card"><div class="muted">'+esc(label)+'</div><div class="number">'+fmt(value)+'</div></div>';
$('team').innerHTML=DATA.teams.map(t=>'<option value="'+esc(t.team_id)+'">'+esc(t.manager)+'</option>').join('');$('team').value=selected;
$('team').onchange=()=>{selected=$('team').value;render();};
$('status').innerHTML=(DATA.verified?'Ownership reconciled with Sleeper':'Provisional manual ownership')+' · '+esc(DATA.league_id)+'<br><small>Roster snapshot '+esc(DATA.ownership_observed_at?new Date(DATA.ownership_observed_at).toLocaleString():'unverified')+' · Forecast built '+esc(new Date(DATA.forecast.generated_at).toLocaleString())+' · Historical seasons '+esc(DATA.forecast.seasons.join(', '))+'</small>';
const gaps=DATA.teams.filter(t=>t.issues.length);
document.querySelector('.data-panel > summary').textContent='Data & refresh · rosters '+(DATA.ownership_observed_at?new Date(DATA.ownership_observed_at).toLocaleDateString():'unverified')+' · forecast '+new Date(DATA.forecast.generated_at).toLocaleDateString()+(gaps.length?' · Missing projections—review before using results':'');
// Rosters, free agents and injury flags change daily; say so outside the collapsed panel.
const rosterAgeHours=DATA.ownership_observed_at?(Date.now()-new Date(DATA.ownership_observed_at))/36e5:Infinity;
if(rosterAgeHours>24){const stale=document.createElement('p');stale.className='warning';stale.textContent=(Number.isFinite(rosterAgeHours)?'Rosters and injury flags are '+Math.floor(rosterAgeHours/24)+' day(s) old':'Ownership is unverified')+': adds, drops and injuries since then are not shown. Refresh from Sleeper before acting.';document.querySelector('.data-panel').before(stale);}
if(gaps.length)$('status').innerHTML+='<p class="warning">Incomplete projection coverage: '+esc(gaps.map(t=>t.manager+' — '+t.issues.join('; ')).join(' · '))+'. Full-league percentiles withheld.</p>';
if(DATA.forecast.warnings.some(x=>x.includes('schedule incomplete')))$('status').innerHTML+='<small class="warning">Schedule has unresolved fixtures. Calendar boundaries and player projections remain assumptions; see sources below.</small>';
$('sources').innerHTML='<p>Values are projected fantasy points per week, not observed results. Player values are conditional on at least one playable game; team expected points include modeled availability and a legal lineup. This dashboard does not import Sleeper projections, and Sleeper weekly scores are actual outcomes, not forecasts. Compare the same player/team measure and time window. NBA production rates use game data through '+esc(DATA.forecast.performance_history_through||'unknown')+'. '+(DATA.forecast.in_season_through?'Current-season games through '+DATA.forecast.in_season_through+' ('+DATA.forecast.in_season_games+' player-games, from league-archive) update minutes and per-minute production; early games get limited weight. ':'Current-season games are not incorporated; run league-archive, then league-build. ')+'Expected lineup averages joint starter and backup availability, solving a legal lineup in each scenario. Larger leagues use 512 fixed antithetic scenarios; small gains include numerical approximation error. Bench insurance is the improvement over using only the healthy starters. Single-outage losses are descriptive, not added together.</p><p>Weekly availability is an uncalibrated mixture of independent missed games and whole-week absences. Different players’ weekly absences are assumed independent. Stress ranges change projections; they are not confidence intervals.</p><p>Playoff estimates use the configured shared calendar and are shown separately. IR players are excluded. Recovery dates and future IR activation are not inferred. Injury flags remain provisional snapshot information: blank does not mean medically cleared. Automatic offers and suggested claims exclude flagged assets and test loss of remaining flagged cover; manual offers remain inspectable with warnings. Replacement pickups assume access after waiver clearance.</p><p>Partner compatibility is the smaller of the best positive relocation gains in each direction, in FP/week. It is a screening measure before roster cuts, not an acceptance probability or an actual proposal.</p><p>Percentiles compare all ten complete teams, or are withheld. Missing projections block affected-team analytics. Free agents require an unowned, active Sleeper record with a team and a usable projection; search covers that eligible pool.</p><ul>'+[...DATA.notes,...DATA.forecast.warnings].map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul><h3>Input files and checksums</h3>'+table(['Source','SHA-256'],DATA.forecast.sources.map(s=>[s.path,s.sha256]));
function render() {
  toolsRenderToken++;
  $('view').oninput=null;$('view').onchange=null;
  $('nav').innerHTML=views.map(v=>'<button type="button" data-view="'+v+'" aria-current="'+(v===mainView())+'">'+v+'</button>').join('');
  document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{activeView=({'League':'Power Rankings','Moves':'Automatic Trades','Lock-In':'Lock-In Advisor'})[b.dataset.view]||'My Team';render();});
  $('subnav').innerHTML=mainView()==='Moves'?Object.entries(moveViews).map(([key,label])=>'<button type="button" data-move="'+key+'" aria-current="'+(activeView===key)+'">'+label+'</button>').join(''):'';
  $('subnav').hidden=mainView()!=='Moves';
  document.querySelectorAll('[data-move]').forEach(b=>b.onclick=()=>{activeView=b.dataset.move;render();});
  $('page-purpose').textContent=({'My Team':'Your roster, priorities and depth in one place.','League':'Compare teams, then explore their rosters.','Moves':'Find an upgrade or check an offer you received.','Lock-In':'Decide whether to keep a completed score or wait.'})[mainView()];
  const t=selectedTeam();
  if(activeView==='My Team')renderTeam(t);
  if(activeView==='Power Rankings'){
    renderRosters();const allRosters=$('view').innerHTML;
    renderStrengths();const strengths=$('view').innerHTML;
    renderPowerRankings(t);
    $('view').insertAdjacentHTML('beforeend','<details><summary>Explore league rosters</summary>'+allRosters+'</details><details><summary>Compare depth and team strengths</summary>'+strengths+'</details>');
  }
  if(activeView==='League Rosters')renderRosters();
  if(activeView==='Team Strengths')renderStrengths();
  if(activeView==='Free Agents')renderFreeAgents(t);
  if(activeView==='Trade Evaluator')renderTrade(t);
  if(activeView==='Three-Team Trade')renderThreeWayTrade(t);
  if(activeView==='Trade Partners')renderPartners(t);
  if(activeView==='Automatic Trades'){
    renderPartners(t);const partners=$('view').innerHTML;
    renderAutoTrades(t);
    $('view').insertAdjacentHTML('beforeend','<details><summary>Explore potential trade partners</summary>'+partners+'</details>');
  }
  if(activeView==='Lock-In Advisor')renderLockAdvisor(t);
  if(activeView==='Positional Needs')renderNeeds(t);
  if(activeView==='My Team')simplifyTeamOverview(t);
}
function simplifyTeamOverview(t) {
  const view=$('view');
  const needs=needSummary(t,'regular');
  const top=needs?.rows.filter(r=>r.need_score>1e-8).slice(0,2)||[];
  const intro=document.createElement('section');intro.className='team-priority';
  intro.innerHTML='<h3>Where to focus</h3><p>'+esc(top.length?top.map(r=>r.position+': '+(r.cover_gap>r.production_gap?'build backup cover':'improve starting production')).join(' · '):'No clear positional gap in the saved projections.')+'</p><button id="team-find-moves">Explore improvements</button>';
  view.querySelector('.cards')?.after(intro);
  // Group secondary diagnostics without removing their tables or caveats.
  for(const title of ['Positions to strengthen','Positional redundancy','Best healthy starting lineup','Coverage & exposure','Separate playoff window']){
    const heading=[...view.children].find(el=>el.tagName==='H3'&&el.textContent===title);
    if(!heading)continue;
    const detail=document.createElement('details');const summary=document.createElement('summary');summary.textContent=title;detail.append(summary);
    heading.before(detail);let node=heading.nextElementSibling;heading.remove();
    while(node&&node.tagName!=='H3'&&node.tagName!=='DETAILS'){const next=node.nextElementSibling;detail.append(node);node=next;}
  }
  const diagnostics=document.createElement('details');diagnostics.innerHTML='<summary>Team analysis & projection details</summary>';
  [...view.children].filter(el=>el.tagName==='DETAILS').forEach(el=>diagnostics.append(el));
  view.append(diagnostics);
  if($('team-find-moves'))$('team-find-moves').onclick=()=>{activeView='Automatic Trades';render();};
}
function renderTeam(t) {
  let html='<h2>'+esc(t.manager)+'</h2>'+blocked(t);
  const p=t.profile;
  if(p)html+='<div class="cards">'+metric('Projected team points / week',p.expected_weekly)+metric('If everyone is available',p.healthy_weekly)+metric('Depth contribution / week',p.bench_insurance)+'</div><p class="muted">Based on the model’s optimized lineup, not your saved starter plan. Depth is already included in the team total.</p>';
  if(t.positional_needs)html+='<h3>Positions to strengthen</h3>'+needsTable(t)+needsDefinition();
  html+='<h3>Active roster</h3>'+table(['Player','Positions','Projected points / week','Recorded injury flag'],t.roster.map(id=>{const v=DATA.players[id];return [name(id),v?.positions.join(' / ')||DATA.identities[id]?.positions.join(' / '),v?fmt(v.value*v.availability):'Missing',v?.injury_status||'None recorded'];}))+'<p class="muted">Player estimates include modeled availability and are not additive team points. Injury flags are from the saved snapshot, not live medical clearance.</p>';
  html+='<p>IR: '+esc(names(t.reserve))+'</p>';
  if(p)html+='<h3>Positional redundancy</h3>'+table(['Position','Eligible assets','Lowest removal cost','Most replaceable asset'],Object.entries(p.positional_redundancy).map(([pos,r])=>[pos,r.players,fmt(r.lowest_removal_cost),r.most_replaceable_asset?name(r.most_replaceable_asset):'None']))+'<p class="muted">Low removal cost indicates existing cover, not a weak player. Free Agents measures actual replacement opportunities.</p>';
  if(p)html+='<h3>Best healthy starting lineup</h3>'+table(['Slot','Player'],p.lineup.map(([s,id])=>[s,name(id)]))+'<h3>Coverage & exposure</h3>'+table(['Slot','Eligible players','All eligible absent: healthy FP loss'],Object.keys(p.coverage).map(s=>[s,p.coverage[s],fmt(p.slot_absence_exposure[s])]))+'<p class="muted">Eligibility overlaps across slots; counts and absence exposures are not additive. HHI of starter value: '+fmt(p.concentration_hhi)+'.</p>';
  if(t.playoff_profile)html+='<h3>Separate playoff window</h3><p>'+fmt(t.playoff_profile.expected_weekly)+' expected FP/week across '+DATA.forecast.playoff_weeks+' loaded playoff weeks. This is not blended into the regular-season figures.</p>';
  html+='<details><summary>Player-level projection assumptions</summary>'+table(['Player','Historical games','Notes'],t.roster.map(id=>[name(id),DATA.players[id]?.historical_games??'Missing',DATA.players[id]?.notes||'No projection']))+table(['Player','Conditional points / week','Modeled weekly availability','Team loss if removed'],t.roster.map(id=>{const v=DATA.players[id];return [name(id),fmt(v?.value),v?fmt(v.availability*100)+'%':'Missing',fmt(p?.expected_marginal[id])];}))+'</details>';
  $('view').innerHTML=html;
}
function renderRosters(){ $('view').innerHTML='<h2>All ten rosters</h2><p>Confirmed ownership includes IR. Missing forecasts remain visible.</p>'+DATA.teams.map(t=>'<details><summary>'+esc(t.manager)+' · '+t.roster.length+' active · '+t.reserve.length+' IR</summary>'+blocked(t)+table(['Player','Status','Projection'],t.roster.concat(t.reserve).map(id=>[name(id),t.reserve.includes(id)?'IR':'Active roster',DATA.players[id]?fmt(DATA.players[id].value):'Missing']))+'</details>').join(''); }
function renderStrengths(){ $('view').innerHTML='<h2>Team strengths</h2><p>Regular-season lineup effects in FP/week. Full-league rankings are withheld when any team has missing projections.</p>'+table(['Team','Expected / week','Bench insurance','Absence cost','Concentration HHI','Expected percentile','Coverage status'],DATA.teams.map(t=>[t.manager,fmt(t.profile?.expected_weekly),fmt(t.profile?.bench_insurance),fmt(t.profile?.absence_cost),fmt(t.profile?.concentration_hhi),fmt(t.profile?.league_relative?.expected_weekly?.percentile),t.issues.join('; ')||'Complete active roster'])); }
function renderFreeAgents(t){
  $('view').innerHTML='<h2>Free agents · '+esc(t.manager)+'</h2>'+blocked(t)+'<p>'+DATA.free_agents.length+' eligible, projected free agents. Move value includes the best marginal-impact cut when needed.</p><div class="toolbar"><label for="fa-search">Player search</label><input id="fa-search" type="search"><button id="moves" '+(t.issues.length?'disabled':'')+'>Find best add/drop moves</button></div><div id="fa-results"></div><div id="move-results"></div>';
  function search(){const q=$('fa-search').value.toLowerCase();
    const unprojected=(DATA.unprojected_free_agents||[]).filter(p=>p.name.toLowerCase().includes(q));
    $('fa-results').innerHTML=table(['Player','Positions','Recorded injury flag','Conditional FP / week'],pool().filter(p=>p.name.toLowerCase().includes(q)).sort((a,b)=>b.value-a.value).slice(0,50).map(p=>[p.name,p.positions.join(' / '),p.injury_status||'None recorded',fmt(p.value)]))+
      (unprojected.length?'<details><summary>'+unprojected.length+' unowned NBA-rostered players without a projection</summary><p class="muted">No forecast row exists (often new rookies), so they are not valued or suggested as adds. Judge them yourself.</p>'+table(['Player','NBA team','Positions','Recorded injury flag'],unprojected.slice(0,100).map(p=>[p.name,p.team,p.positions.join(' / '),p.injury_status||'None recorded']))+'</details>':'');}
  $('fa-search').oninput=search;search();
  $('moves').onclick=()=>{$('moves').disabled=true;$('move-results').textContent='Calculating marginal cuts across the eligible pool…';setTimeout(()=>{try{const screened=new Set(leagueAddDrops(roster(t),pool(),DATA.cfg,20).map(r=>r.add));
      // Near-tie drops can flip under the 512-scenario screen, so the shortlist is re-scored exactly.
      const rows=leagueAddDrops(roster(t),pool().filter(p=>screened.has(p.id)),{...DATA.cfg,exactAvailability:true},20);$('move-results').innerHTML='<h3>Best moves for this roster</h3><p class="muted">Top 20 screened adds re-scored with exact weekly availability.</p>'+table(['Add','Drop','Change FP / week'],rows.map(r=>[name(r.add),names(r.drop),fmt(r.delta_weekly)]));}catch(e){$('move-results').textContent=e.message;}finally{$('moves').disabled=false;}},20);};
}
function renderPartners(t){$('view').innerHTML='<h2>Trade partners · '+esc(t.manager)+'</h2>'+blocked(t)+'<p>Screening by two-way relocation gains. Assets listed here are not trade proposals. Teams with missing projections are excluded.</p>'+t.partners.map(r=>{const other=DATA.teams.find(q=>q.team_id===r.team_id);return '<div class="card"><h3>'+esc(other.manager)+' · '+fmt(r.compatibility_fp_week)+' FP/week compatibility</h3>'+table(['Direction','Asset','Current removal cost','Other-roster addition gain'],r.useful_outgoing.map(p=>['You → '+other.manager,p.name,fmt(p.source_removal_cost),fmt(p.destination_addition_gain)]).concat(r.useful_incoming.map(p=>[other.manager+' → you',p.name,fmt(p.source_removal_cost),fmt(p.destination_addition_gain)])))+'</div>';}).join('');}
function renderTrade(t){
  $('view').innerHTML='<h2>Two-sided trade evaluator</h2>'+blocked(t)+'<div class="toolbar"><label for="opponent">Opponent</label><select id="opponent">'+DATA.teams.filter(q=>q.team_id!==t.team_id).map(q=>'<option value="'+q.team_id+'">'+esc(q.manager)+'</option>').join('')+'</select><label for="stress">Projection stress %</label><input id="stress" type="number" value="10" min="0" max="100"><label for="weeks">Regular weeks remaining</label><input id="weeks" type="number" value="'+DATA.cfg.weeksRemaining+'" min="0" max="52"></div><div class="columns"><div><h3>You send</h3><div id="give" class="picklist"></div></div><div><h3>You receive</h3><div id="get" class="picklist"></div></div></div><p><label><input id="replacements" type="checkbox"> Include hypothetical replacement claims (not a standalone offer)</label></p><button id="evaluate">Evaluate both teams</button><div id="trade-result"></div>';
  const picker=(team,key)=>team.roster.map(id=>'<label><input type="checkbox" name="'+key+'" value="'+id+'">'+esc(name(id))+'</label>').join('');
  $('give').innerHTML=picker(t,'give');const other=()=>DATA.teams.find(q=>q.team_id===$('opponent').value);
  function update(){ $('get').innerHTML=picker(other(),'get');$('trade-result').innerHTML=blocked(other());$('evaluate').disabled=!!(t.issues.length||other().issues.length); }$('opponent').onchange=update;update();
  $('evaluate').onclick=()=>{
    const a=Array.from(document.querySelectorAll('[name=give]:checked'),e=>e.value),b=Array.from(document.querySelectorAll('[name=get]:checked'),e=>e.value);
    const cfg={...DATA.cfg,stress:Number($('stress').value)/100,weeksRemaining:Number($('weeks').value)};
    $('evaluate').disabled=true;$('trade-result').textContent='Solving lineups, marginal cuts, replacements and stress scenarios…';
    setTimeout(()=>{try{
      if(!a.length&&!b.length)throw Error('Select at least one player');
      let html='';
      html+=researchTable(roster(t).filter(p=>a.includes(p.id)).concat(roster(other()).filter(p=>b.includes(p.id))));
      for(const horizon of ['regular','playoffs']) {
        const ar=roster(t,horizon),br=roster(other(),horizon);
        if(ar.some(p=>!p)||br.some(p=>!p)){html+='<p>Playoff forecast unavailable.</p>';continue;}
        const hc={...cfg,weeksRemaining:horizon==='playoffs'?DATA.forecast.playoff_weeks:cfg.weeksRemaining};
        const r=evaluateLeagueTrade(ar,br,a,b,pool(horizon),hc,$('replacements').checked);
        if(horizon==='regular'){
          const tradeWeek=currentTradeWeek(DATA.lock_advisor),ids=starterSnapshot(t,tradeWeek).ids;
          html+=weekTradeTable(ar,r.team_a.after_roster,br,r.team_b.after_roster,hc,ids,other(),tradeWeek);
          if(ids.length){const impact=plannedTradeImpact(ar,r.team_a.after_roster,hc,ids,positionMetrics(ar,hc).PG?.leader,true);
            html+='<h4>Your API Week 1 starter plan · season-average scenario</h4>'+table(['Expected before','Expected after','Expected gain','PG leader-out gain','Actual PG absence-penalty reduction'],[[fmt(impact.before),fmt(impact.after),fmt(impact.gain),fmt(impact.leader_out_gain),fmt(impact.insurance_gain)]])+'<p class="muted">Retains available planned starters and fills vacated slots. The unrestricted trade totals are shown separately below.</p>';
          }
        }
        html+=tradeHealthNotice(r);
        html+='<h3>'+ (horizon==='regular'?'Regular season':'Separate playoff window')+' · '+esc(r.classification.replace('TEAM A',t.manager).replace('TEAM B',other().manager))+'</h3>'+table(['Team','Before / week','After / week','Starter Δ','Depth Δ','Total Δ / week','Window Δ','Stress range / week','Cuts','Pickups'],[[t,r.team_a],[other(),r.team_b]].map(([team,s])=>[team.manager,fmt(s.before),fmt(s.after),fmt(s.delta_starters),fmt(s.delta_depth),fmt(s.delta_weekly),fmt(s.delta_season),fmt(s.pessimistic)+' to '+fmt(s.optimistic),names(s.drops),names(s.pickups)]))+
          [[t,r.team_a],[other(),r.team_b]].filter(([,s])=>s.baseline_free_moves).map(([team,s])=>'<p class="muted">'+esc(team.manager)+' “before” already includes the waiver move available without this trade: '+esc(s.baseline_free_moves.map(m=>'add '+name(m.add)+(m.drop.length?', drop '+names(m.drop):'')).join('; ')||'none worth making')+'. Claims count only beyond it.</p>').join('');
        for(const [team,before,side] of [[t,ar,r.team_a],[other(),br,r.team_b]]){
          const needs=needSummary(team,horizon);
          if(needs)html+=needChangesTable(team.manager,tradeNeeds(before,side.after_roster,hc,needs.references));
        }
      }
      $('trade-result').innerHTML=html+'<p class="muted">Exact joint-availability calculation under independent weekly absences; this does not make projections or medical assumptions certain. General team-profile screens use a faster approximation and may differ slightly. Fixed selected cuts and pickups under stress. Claims are hypothetical, not guaranteed. No transaction has been recorded.</p>';
    }catch(e){$('trade-result').textContent=e.message;}finally{$('evaluate').disabled=false;}},20);
  };
}
function renderThreeWayTrade(t){
  const others=DATA.teams.filter(team=>team.team_id!==t.team_id);
  const options=others.map(team=>'<option value="'+esc(team.team_id)+'">'+esc(team.manager)+'</option>').join('');
  $('view').innerHTML='<h2>Check a three-team trade</h2><p>Choose two other teams, then set where each traded player goes. Every team must send and receive at least one player. Active players only; picks and free agents are not included.</p>'+
    '<div class="toolbar"><label>Team 2 <select id="three-b">'+options+'</select></label><label>Team 3 <select id="three-c">'+options+'</select></label></div>'+
    '<div id="three-issues" role="status"></div><div id="three-players" class="three-rosters"></div>'+
    '<details><summary>Projection settings</summary><div class="toolbar"><label>Stress test % <input id="three-stress" type="number" min="0" max="100" value="10"></label><label>Regular weeks remaining <input id="three-weeks" type="number" min="0" max="52" value="'+DATA.cfg.weeksRemaining+'"></label></div></details>'+
    '<button id="three-evaluate">Check this trade</button><div id="three-result" role="status"></div>'+
    '<p class="muted">Scenario only. This page does not send an offer or make roster moves in Sleeper.</p>';
  $('three-c').value=others[1]?.team_id??'';
  const teams=()=>[t,DATA.teams.find(team=>team.team_id===$('three-b').value),DATA.teams.find(team=>team.team_id===$('three-c').value)];
  const renderPlayers=()=>{
    const chosen=teams();
    if(chosen[1]?.team_id===chosen[2]?.team_id){
      $('three-issues').textContent='Choose two different teams.';$('three-players').innerHTML='';$('three-evaluate').disabled=true;return;
    }
    $('three-issues').innerHTML=chosen.map(team=>blocked(team)).join('');
    $('three-evaluate').disabled=chosen.some(team=>!team||team.issues.length);
    $('three-players').innerHTML=chosen.map((team,from)=>'<section class="card"><h3>'+esc(team.manager)+'</h3><p class="muted">Choose a destination for each player leaving this team.</p><div class="picklist">'+roster(team).filter(Boolean).map(p=>'<label class="three-route-row"><span>'+esc(p.name)+' <small>'+esc(p.positions.join(' / '))+(healthRisk(p)?' · '+esc(p.injury_status||'Injury flag'):'')+'</small></span><select class="three-route" data-from="'+from+'" data-player="'+esc(p.id)+'" aria-label="Send '+esc(p.name)+' to"><option value="">Keep</option>'+chosen.map((dest,i)=>i===from?'':'<option value="'+i+'">'+esc(dest.manager)+'</option>').join('')+'</select></label>').join('')+'</div></section>').join('');
    $('three-result').innerHTML='';
    document.querySelectorAll('.three-route').forEach(control=>control.onchange=()=>{$('three-result').innerHTML='';});
  };
  $('three-b').onchange=()=>{if($('three-c').value===$('three-b').value)$('three-c').value=others.find(team=>team.team_id!==$('three-b').value)?.team_id??'';renderPlayers();};
  $('three-c').onchange=renderPlayers;
  renderPlayers();
  $('three-evaluate').onclick=()=>{
    const chosen=teams(),transfers=[...document.querySelectorAll('.three-route')].filter(control=>control.value!=='').map(control=>({from:Number(control.dataset.from),to:Number(control.value),playerId:control.dataset.player}));
    const stress=Number($('three-stress').value),weeks=Number($('three-weeks').value);
    $('three-evaluate').disabled=true;$('three-result').textContent='Calculating all three rosters…';
    setTimeout(()=>{try{
      if($('three-stress').value.trim()===''||$('three-weeks').value.trim()===''||!Number.isFinite(stress)||stress<0||stress>100||!Number.isInteger(weeks)||weeks<0||weeks>52)throw Error('Check the projection settings.');
      let html='<h3>Proposed player routes</h3>'+table(['From','Player','To'],transfers.map(route=>[chosen[route.from].manager,name(route.playerId),chosen[route.to].manager]));
      const cfg={...DATA.cfg,stress:stress/100,weeksRemaining:weeks};
      for(const horizon of ['regular','playoffs']){
        const before=chosen.map(team=>roster(team,horizon));
        if(before.some(team=>team.some(p=>!p))){html+='<p>Missing '+horizon+' projection for one of the three teams; this window is unavailable.</p>';continue;}
        const hc={...cfg,weeksRemaining:horizon==='playoffs'?DATA.forecast.playoff_weeks:weeks};
        const sides=evaluateLeagueThreeWay(before,transfers,hc);
        html+='<h3>'+ (horizon==='regular'?'Regular season':'Playoff window')+'</h3>'+
          table(['Team','Before / week','After / week','Change / week','Pessimistic change','Sends','Receives','Roster cut'],sides.map((side,i)=>[chosen[i].manager,fmt(side.before),fmt(side.after),fmt(side.delta_weekly),fmt(side.pessimistic),names(side.sent),names(side.received),names(side.drops)]))+
          '<p class="'+(sides.some(s=>s.delta_weekly<0||s.pessimistic<0)?'warning':'muted')+'">'+(sides.some(s=>s.delta_weekly<0)?'At least one team loses projected lineup points. ':'All three gain projected lineup points. ')+(sides.some(s=>s.pessimistic<0)?'The stress test also turns at least one result negative.':'All three remain nonnegative in the selected stress test.')+' These are model estimates, not acceptance odds.</p>';
        if(horizon==='regular'){
          html+='<details><summary>Week 1 and injury details</summary>';
          const weekRows=sides.map((side,i)=>{
            const tradeWeek=currentTradeWeek(DATA.lock_advisor),ids=starterSnapshot(chosen[i],tradeWeek).ids;
            const b=weekRosterScenario(before[i],DATA.lock_advisor,tradeWeek,hc,ids),a=weekRosterScenario(side.after_roster,DATA.lock_advisor,tradeWeek,hc,ids);
            return [chosen[i].manager,b.available?fmt(b.total):'Unavailable',a.available?fmt(a.total):'Unavailable',a.available&&b.available?fmt(a.total-b.total):a.reason||b.reason];
          });
          html+='<h4>Week 1 · pre-week scenario</h4>'+table(['Team','Before FP','After FP','Change FP'],weekRows);
          const risks=[...new Set(sides.flatMap(side=>Object.keys(side.injury_risks)))];
          html+='<p class="muted">Saved injury flags: '+esc(names(risks))+'. Flags and game schedules are snapshots, not live availability guarantees. No free-agent claims are assumed.</p>'+
            researchTable(transfers.map(route=>DATA.players[route.playerId]))+'</details>';
        }
      }
      $('three-result').innerHTML=html+'<p class="muted">All three roster changes are evaluated simultaneously with exact joint availability. Automatic cuts are shown above; empty roster spots remain empty. No transaction was recorded.</p>';
    }catch(e){$('three-result').textContent=e.message;}finally{$('three-evaluate').disabled=false;}},20);
  };
}
render();
