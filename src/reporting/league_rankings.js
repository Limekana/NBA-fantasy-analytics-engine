/* Power = expected legal lineup output, not a weighted sum of arbitrary scores. */
function leaguePowerRankings(teams,players,cfg,mode='baseline') {
  if(!['baseline','injury'].includes(mode))throw Error('Unknown ranking scenario');
  if(new Set(teams.map(t=>t.team_id)).size!==teams.length)throw Error('Duplicate team ID');
  const missing=teams.filter(t=>!t.roster.length||t.roster.length>cfg.rosterLimit||t.roster.some(id=>!players[id]));
  if(missing.length||teams.length<2)return {rows:[],missing:missing.map(t=>t.manager),complete:false};
  const exactCfg={...cfg,exactAvailability:true};
  const owned=teams.flatMap(t=>t.roster);
  if(new Set(owned).size!==owned.length)throw Error('Duplicate ownership; refresh before ranking');
  const rows=teams.map(t=>{
    const roster=t.roster.map(id=>players[id]);
    const base=rosterStrength(roster,exactCfg);
    const starters=base.lineup.assignments.filter(a=>a.player).map(a=>a.player);
    const injuries=roster.filter(healthRisk);
    const stressed=injuries.length?jointExpectation(roster.map(p=>({...p,availability:healthRisk(p)?0:p.availability})),exactCfg):base.expected;
    return {team_id:t.team_id,manager:t.manager,expected:base.expected,healthy:base.healthy,
      bench:Math.max(0,base.expected-jointExpectation(starters,exactCfg)),
      injury_expected:stressed,injury_drop:Math.max(0,base.expected-stressed),
      injuries:injuries.map(p=>p.id),reserve:t.reserve||[],
      score:mode==='injury'?stressed:base.expected};
  });
  // Competition ranks at displayed precision: 1, 1, 3. Do not manufacture
  // precision with arbitrary tie-breaker rankings for virtually equal scores.
  const units=r=>Math.round(r.score*100);
  rows.sort((a,b)=>units(b)-units(a)||(a.team_id<b.team_id?-1:a.team_id>b.team_id?1:0));
  const best=Math.max(...rows.map(r=>r.score));
  rows.forEach((r,i)=>{r.rank=i&&units(r)===units(rows[i-1])?rows[i-1].rank:i+1;r.gap=Math.max(0,best-r.score);});
  return {rows,missing:[],complete:true};
}

function renderPowerRankings(t) {
  const token=toolsRenderToken;
  $('view').innerHTML='<h2>League power rankings</h2><p>Projected roster strength for your Lock-In format—not current standings or championship odds.</p>'+
    '<div class="toolbar"><label>Horizon <select id="power-horizon"><option value="regular">Regular season</option><option value="playoffs">Playoffs</option></select></label>'+
    '<label>Rank by <select id="power-mode"><option value="baseline">Baseline availability</option><option value="injury">Injury stress: flagged players unavailable</option></select></label></div><div id="power-results" role="status"></div>';
  let revision=0;
  function update(){
    const current=++revision,horizon=$('power-horizon').value,mode=$('power-mode').value;
    $('power-results').textContent='Calculating exact availability-adjusted lineups…';
    setTimeout(()=>{
      if(toolsRenderToken!==token||revision!==current)return;
      try{
        const players=horizon==='regular'?DATA.players:DATA.playoff_players;
        const result=leaguePowerRankings(DATA.teams,players,DATA.cfg,mode);
        if(!result.complete){$('power-results').innerHTML='<p class="warning">Full-league ranks are withheld until every active roster has projections for this horizon. Check: '+esc(result.missing.join(', ')||'league coverage')+'. No missing player receives an invented zero.</p>';return;}
        const mine=result.rows.find(r=>r.team_id===t.team_id),leaders=result.rows.filter(r=>r.rank===1);
        const card=(label,value)=>'<div class="card"><div class="muted">'+esc(label)+'</div><div class="number">'+esc(value)+'</div></div>';
        let html=(DATA.verified?'':'<p class="warning">Ownership is provisional. Confirm the Sleeper snapshot before relying on these ranks.</p>')+
          '<div class="cards">'+card(t.manager+' · rank',mine.rank+' / '+result.rows.length)+metric('Your projected FP / week',mine.score)+metric('Your gap to first · FP / week',mine.gap)+'</div>'+
          '<p>Leader'+(leaders.length>1?'s':'')+': '+esc(leaders.map(r=>r.manager).join(', '))+'. '+(mode==='injury'?'Injury stress assumes every active injury-flagged player is unavailable together. This is a conservative sensitivity test, not a season-long absence forecast.':'Baseline uses modeled weekly availability. Known injury flags remain unresolved recovery risks; compare the injury-stress column before treating a team as stronger.')+'</p>'+
          table(['Rank','Team','Ranked FP / week','Gap to first','Baseline FP / week','Flagged-out FP / week','Healthy lineup FP / week','Baseline bench cover FP / week'],result.rows.map(r=>[
            r.rank,r.manager+(r.team_id===t.team_id?' (selected)':''),fmt(r.score),fmt(r.gap),fmt(r.expected),fmt(r.injury_expected),fmt(r.healthy),fmt(r.bench)]))+
          '<h3>Availability and reserve cautions</h3>'+table(['Team','Active injury flags','IR excluded from rankings','FP/week lost in injury stress'],result.rows.map(r=>[
            r.manager,r.injuries.map(id=>name(id)+' ('+(players[id].injury_status||'unresolved')+')').join(', ')||'None recorded—not medical clearance',names(r.reserve),fmt(r.injury_drop)]))+
          '<details><summary>How these rankings work</summary><p>Rank = descending expected fantasy points from the best legal starting lineup across joint weekly availability of starters and backups. Exact enumeration uses the same model as the manual trade evaluator; independent absences are an assumption, not a medical prediction. Depth is already included in expected points and is not added again. Bench cover is the difference versus keeping only the healthy starting group.</p><p>The healthy lineup is a conditional ceiling for the stored weekly player values, not a best-game ceiling. Regular and playoff windows are separate schedule-based forecasts. IR players remain owned but their return timing is not modeled. Missing active projections block full-league ranks. Ties at two decimal places share a competition rank; small gaps should not be treated as decisive.</p><p>These rankings do not incorporate actual win–loss records, future opponent difficulty, dynasty value, manager skill or playoff-bracket odds. General team-profile screens use a faster approximation and may differ slightly.</p></details>'+
          '<p class="muted">Sources: verified roster snapshot '+esc(new Date(DATA.ownership_observed_at).toLocaleString())+'; player forecast '+esc(new Date(DATA.forecast.generated_at).toLocaleString())+'. Refresh from Sleeper updates ownership and these rankings, using existing performance projections.</p>';
        $('power-results').innerHTML=html;
      }catch(e){$('power-results').textContent='Rankings unavailable: '+e.message;}
    },20);
  }
  $('power-horizon').onchange=update;$('power-mode').onchange=update;update();
}
