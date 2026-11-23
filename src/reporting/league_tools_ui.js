/* New tools extend the existing offline desk; no network calls or transactions. */
let toolsRenderToken=0;
/* Keep detailed controls available without making them the main workflow. */
function simplifyTradeFinder(t) {
  const view=$('view'), advanced=document.createElement('details');
  advanced.id='auto-advanced';advanced.innerHTML='<summary>Advanced settings and lineup details</summary>';
  const children=[...view.children],run=$('auto-run');
  for(const child of children){
    if(child===run)break;
    if(child.tagName!=='H2')advanced.append(child);
  }
  view.insertBefore(advanced,run);
  const simple=document.createElement('section');
  simple.innerHTML=blocked(t)+'<p>Find possible offers worth discussing. Gains are estimates, not guarantees.</p><div class="toolbar"><label>What would help? <select id="auto-goal"><option value="team">Improve my team</option><option value="pg">Find PG depth</option><option value="bold">Explore bigger PG deals</option></select></label></div><p id="auto-team-brief"></p><p id="auto-default-note" class="muted"></p>';
  view.insertBefore(simple,advanced);
  simple.querySelector('.toolbar').append($('auto-partner').closest('label'));
  const needs=needSummary(t,'regular');
  const priorities=needs?.rows.filter(r=>r.need_score>1e-8).slice(0,2)||[];
  $('auto-team-brief').textContent=t.issues.length?'':priorities.length?'Team priorities: '+priorities.map(r=>r.position+' — '+(r.cover_gap>r.production_gap?'backup cover, rather than replacing your star':'starting production')).join('; ')+'.':'No clear positional priority in the saved projections.';
  const seed=starterSnapshot(t,currentTradeWeek(DATA.lock_advisor));
  const applyGoal=()=>{
    const goal=$('auto-goal').value;
    $('auto-mode').value='fit';$('auto-mode').onchange();
    $('auto-source').value='any';$('auto-focus').value=goal==='team'?'top':'PG';
    $('auto-robust').checked=false;$('auto-min').value='0.25';
    $('auto-protect').checked=goal!=='bold'&&seed.ids.length>0;
    $('auto-planned').checked=seed.ids.length>0;
    $('auto-insurance').checked=false;$('auto-research').checked=false;
    $('auto-waivers').checked=false;
    $('auto-source').onchange();
    $('auto-default-note').textContent=(seed.ids.length?'Uses your saved starter plan. '+(goal==='bold'?'Starter offers are allowed. ':'Your starters stay protected. '):'No valid starter plan is saved; uses the best projected lineup. Set a plan in Advanced if needed. ')+'Injury-flagged players stay out of offers. Stress tests warn about risk; they do not reject offers by default.';
  };
  $('auto-goal').onchange=applyGoal;applyGoal();
  advanced.addEventListener('change',()=>{$('auto-default-note').textContent='Custom settings are active. Choose a goal again to restore its defaults.';});
  run.textContent='Find trade ideas';
  // Model methodology remains available, but is not another wall of text.
  const foot=$('auto-results').nextElementSibling;
  if(foot)advanced.append(foot);
  advanced.querySelectorAll('details[open]').forEach(el=>el.open=false);
}
function renderAutoTrades(t) {
  const token=toolsRenderToken;
  $('view').innerHTML='<h2>Automatic trade finder · '+esc(t.manager)+'</h2>'+blocked(t)+
    '<p>Search other managers without choosing a target player. Injury-flagged assets, IR, picks and unprojected players are excluded from offers. Remaining active-roster 1-for-1s are evaluated; packages use a bounded shortlist. Injury flags are snapshot data, not live medical clearance.</p>'+
    '<div class="toolbar"><label>Horizon <select id="auto-horizon"><option value="regular">Regular season</option><option value="playoffs">Playoffs</option></select></label>'+
    '<label>Search approach <select id="auto-mode"><option value="surplus">Surplus for added depth</option><option value="fit">Pay for positional fit</option><option value="broad">Broader trade search</option></select></label>'+
    '<label>Offer from <select id="auto-source"><option value="wings">Surplus wings (SG / SF / PF, no C)</option><option value="any">Any surplus position</option></select></label>'+
    '<label>Trade focus <select id="auto-focus"><option value="top">Strengthen my top needs</option><option value="all">Largest overall gain</option>'+NEED_POSITIONS.map(p=>'<option value="'+p+'">Strengthen '+p+'</option>').join('')+'</select></label>'+
    '<label>Minimum your gain / week <input id="auto-min" type="number" min="0" step="0.1" value="1"></label>'+
    '<label>Minimum their gain / week <input id="auto-other" type="number" min="0" step="0.1" value="0.1"></label>'+
    '<label>Projection stress % <input id="auto-stress" type="number" min="0" max="100" value="10"></label></div>'+
    '<div class="toolbar"><button id="auto-bold">Bolder: offer frontcourt for a PG</button><label>Opponent <select id="auto-partner"><option value="all">Entire league</option>'+DATA.teams.filter(q=>q.team_id!==t.team_id).map(q=>'<option value="'+esc(q.team_id)+'">'+esc(q.manager)+'</option>').join('')+'</select></label><label>Week comparison <input id="auto-week" type="number" min="1" max="25" value="'+currentTradeWeek(DATA.lock_advisor)+'"></label></div>'+
    planControls(t)+'<p><label><input id="auto-planned" type="checkbox" checked> Evaluate and rank against my planned starters</label><br><label><input id="auto-protect" type="checkbox" checked> Do not offer my planned starters</label><br><label><input id="auto-insurance" type="checkbox"> Require a real reduction in the chosen leader’s absence penalty</label><br><label><input id="auto-research" type="checkbox"> Only show offers with recent sourced role AND market reviews</label></p>'+
    '<div id="auto-fit-controls" class="toolbar" hidden><label>Maximum individual-value overpay % <input id="auto-overpay" type="number" min="0" max="50" value="15"></label><label>Maximum their lineup loss FP / week <input id="auto-loss" type="number" min="0" max="3" step="0.1" value="1"></label><p class="muted">Exploratory offers: you must gain expected lineup points and target-position cover. If their lineup loses points, they must receive at least as much summed individual model value and their loss must stay within your limit. Offers that improve both rosters also qualify; overpay is optional. This is not market value or an acceptance prediction. Keeps your most valuable player and existing target-position players; other positional leaders can be offered. No hypothetical waiver claims.</p></div>'+
    '<div id="auto-needs"></div><p><label><input id="auto-packages" type="checkbox" checked> Include shortlisted 2-for-2, 2-for-1 and 1-for-2 packages</label><br>'+
    '<label><input id="auto-waivers" type="checkbox"> Allow hypothetical free-agent claims (not a standalone offer)</label><br>'+
    '<label><input id="auto-robust" type="checkbox" checked> Require both sides to stay nonnegative under pessimistic stress (you strictly positive)</label></p>'+
    '<button id="auto-run" '+(t.issues.length?'disabled':'')+'>Find trades across the league</button> <button id="auto-cancel" disabled>Cancel</button>'+
    '<p id="auto-progress" role="status"></p><div id="auto-results"></div>'+
    '<p class="muted">Positive fit for both rosters is a reason to consider an offer, not a prediction of acceptance. Fast screening uses 512 scenarios; up to 60 finalists are rechecked with exact joint-availability calculations before display. Projections and independence assumptions remain uncertain. No market-price model, manager preferences, dynasty value or draft-pick values are included. Results maximize projected lineup points, not season championship odds.</p>';
  function updateNeeds(){
    const h=$('auto-horizon').value,needs=needSummary(t,h),top=needs?.rows.filter(r=>r.need_score>1e-8||!r.eligible_count).slice(0,2)||[];
    const fit=$('auto-mode').value==='fit',surplus=$('auto-mode').value!=='broad';
    $('auto-fit-controls').hidden=!fit;
    $('auto-other').disabled=fit;$('auto-waivers').disabled=fit;
    if(fit)$('auto-waivers').checked=false;
    $('auto-source').disabled=!surplus;
    $('auto-focus').querySelector('[value=all]').disabled=surplus;
    if(surplus&&$('auto-focus').value==='all')$('auto-focus').value='top';
    $('auto-needs').innerHTML='<p>Top needs: '+esc(top.map(r=>r.position+' ('+r.reason+')').join('; ')||'No measured gap / benchmarks unavailable')+'.</p><details><summary>Inspect positional rankings</summary>'+needsTable(t,h)+needsDefinition()+'</details><p class="muted">'+(surplus?'Added-depth search targets the highest priority (or your chosen position), keeps every current player eligible there and all positional leaders, and requires an additional directly traded player there plus a stronger lineup with the same leader absent. Outgoing assets must come from positions where you have more eligible players than the opponent.':'Broader search allows star packages. Positional focus uses comparable needs improvement, withholding positive cover credit when the leader changes.')+' Both teams must gain after roster cuts. Free-agent claims are OFF unless you explicitly enable them. Results that pass pessimistic stress rank first, then your projected gain—not need-score reduction.</p>';
    $('auto-progress').textContent='';$('auto-results').innerHTML='';
    if(fit)$('auto-needs').innerHTML='<p>Top needs: '+esc(top.map(r=>r.position+' ('+r.reason+')').join('; ')||'No measured gap')+'.</p><details><summary>Inspect positional rankings</summary>'+needsTable(t,h)+needsDefinition()+'</details><p class="warning">Fit mode relaxes the mutual-gain requirement and protection of non-target positional leaders. Your best asset and existing target-position players stay protected. Requires an extra target-eligible player and better cover with the same leader absent, plus positive expected team gain even in the recorded-injury stress. Their baseline and injury-stress lineup loss must stay within your chosen limit. Pessimistic projection stress is shown but is optional in this mode.</p>';
  }
  $('auto-horizon').onchange=updateNeeds;$('auto-mode').onchange=()=>{const fit=$('auto-mode').value==='fit';$('auto-robust').checked=!fit;$('auto-min').value=fit?'0.25':'1';updateNeeds();};$('auto-source').onchange=updateNeeds;$('auto-focus').onchange=()=>{$('auto-results').innerHTML='';$('auto-progress').textContent='';};updateNeeds();
  $('auto-bold').onclick=()=>{$('auto-mode').value='fit';$('auto-mode').onchange();$('auto-source').value='any';$('auto-focus').value='PG';$('auto-protect').checked=false;$('auto-insurance').checked=false;updateNeeds();};
  document.querySelectorAll('#view input, .planned-starter, #auto-partner').forEach(el=>el.oninput=()=>{$('auto-results').innerHTML='';$('auto-progress').textContent='';});
  $('auto-run').onclick=async()=>{
    let stopped=false;
    const controls=[...document.querySelectorAll('#view input, #view select, #auto-bold')];
    const cancelled=()=>stopped||toolsRenderToken!==token;
    $('auto-cancel').onclick=()=>{stopped=true;$('auto-progress').textContent='Cancelling…';};
    $('auto-run').disabled=true;$('auto-cancel').disabled=false;$('auto-results').innerHTML='';
    controls.forEach(el=>el.disabled=true);
    try {
      const horizon=$('auto-horizon').value;
      const cfg={...DATA.cfg,stress:Number($('auto-stress').value)/100,weeksRemaining:horizon==='regular'?DATA.cfg.weeksRemaining:DATA.forecast.playoff_weeks};
      if(!['auto-min','auto-other','auto-stress'].every(id=>$(id).value.trim()!==''))throw Error('Enter the gain and stress settings');
      const mine=roster(t,horizon);
      if(mine.some(p=>!p))throw Error('Selected team lacks projections for this horizon');
      const eligible=DATA.teams.filter(q=>q.team_id!==t.team_id&&($('auto-partner').value==='all'||q.team_id===$('auto-partner').value)&&!q.issues.length&&!roster(q,horizon).some(p=>!p));
        const omitted=DATA.teams.filter(q=>q.team_id!==t.team_id&&($('auto-partner').value==='all'||q.team_id===$('auto-partner').value)&&!eligible.includes(q)).map(q=>q.manager);
      const needs=needSummary(t,horizon),focus=$('auto-focus').value,mode=$('auto-mode').value;
      if(focus!=='all'&&!needs)throw Error('Complete league projections are needed for positional focus. Choose largest overall gain to search the available teams.');
      const needFocus=focus==='all'?[]:focus==='top'?needs.rows.filter(r=>r.need_score>1e-8||!r.eligible_count).slice(0,mode!=='broad'?1:2).map(r=>r.position):[focus];
      if(mode!=='broad'&&!needFocus.length)throw Error('No measured top need. Choose a specific position for added depth, or use broader search.');
      if(mode==='fit'&&(!$('auto-overpay').value.trim()||!$('auto-loss').value.trim()))throw Error('Enter both positional-fit limits');
      const plannedIds=$('auto-planned').checked||$('auto-protect').checked?readStarterPlan(t,cfg):[];
      const comparisonWeek=Number($('auto-week').value);
      if(!Number.isInteger(comparisonWeek)||comparisonWeek<1||comparisonWeek>25)throw Error('Choose a week from 1 to 25.');
      if($('auto-insurance').checked&&needFocus.length!==1)throw Error('Choose one position for the absence-penalty filter.');
      const report=await findLeagueTrades(mine,eligible.map(q=>({id:q.team_id,roster:roster(q,horizon),needReferences:needSummary(q,horizon)?.references})),pool(horizon),cfg,
        {plannedStarterIds:$('auto-planned').checked?plannedIds:[],protectedStarterIds:plannedIds,protectStarters:$('auto-protect').checked,requireInsurance:$('auto-insurance').checked,requireResearch:$('auto-research').checked,reviews:DATA.trade_reviews,minGain:Number($('auto-min').value),otherMin:Number($('auto-other').value),maxOverpay:Number($('auto-overpay').value)/100,maxOtherLoss:Number($('auto-loss').value),packages:$('auto-packages').checked,robust:$('auto-robust').checked,needFocus,needReferences:needs?.references,searchMode:mode,offerFrom:$('auto-source').value,replacements:$('auto-waivers').checked},
        status=>{if(!cancelled())$('auto-progress').textContent=status.singles+' single-player swaps checked · '+status.packagesScreened+' packages screened · '+status.packagesEvaluated+' packages fully evaluated';},cancelled);
      if(toolsRenderToken!==token)return;
      if(report.cancelled){$('auto-progress').textContent='Search cancelled. No trades were submitted.';return;}
      $('auto-progress').textContent=report.results.length+' ideas to review'+(needFocus.length?' for '+needFocus.join(' / '):'')+'. Showing up to three per opponent; package search is limited.'+(omitted.length?' Missing data: '+omitted.join(', ')+'.':'');
      $('auto-results').innerHTML=report.results.length?report.results.map((r,i)=>{
        const a=r.result.team_a,b=r.result.team_b,other=DATA.teams.find(q=>q.team_id===r.opponent);
        return '<article class="card"><h3>'+esc(t.manager)+' ↔ '+esc(other.manager)+'</h3><p>You send <strong>'+esc(names(r.give))+'</strong><br>You receive <strong>'+esc(names(r.get))+'</strong></p>'+
          '<p><strong>Your team:</strong> '+fmt(r.planned?.gain??a.delta_weekly)+' projected points / week. <strong>Their team:</strong> '+fmt(b.delta_weekly)+' with an optimized lineup.</p>'+
          '<p><strong>Why consider it:</strong> '+(r.depth_fit?'Adds '+esc(r.depth_fit.target)+' eligibility; you send '+esc(r.depth_fit.source_positions.join(' / '))+' depth they have less of. Eligibility counts alone do not prove their lineup improves.':'Improves your projected lineup under the selected assumptions.')+'</p>'+
          '<p class="warning"><strong>Main caution:</strong> '+((r.planned?.pessimistic??a.pessimistic)<=0||b.pessimistic<0?'The gain can disappear if player projections are wrong. ':'Passes the selected stress test, but remains uncertain. ')+(r.planned?.missing_starters.length?'You give up a planned starter. ':'')+(b.delta_weekly<0?'Their projected lineup loses points. ':'')+(a.pickups.length||b.pickups.length?'Depends on hypothetical free-agent claims. ':'')+'Current role, health outlook and market value still need checking; acceptance is unknown.</p>'+
          '<details><summary>Why this trade? Full analysis</summary>'+
          '<div class="cards">'+metric('Your optimized gain FP / week',a.delta_weekly)+metric('Their optimized gain FP / week',b.delta_weekly)+'</div>'+
          (r.planned?'<h4>Your planned lineup · season-average scenario</h4>'+table(['Expected before','Expected after','Expected gain','All available gain','Original leader out gain','Actual absence-penalty reduction','Pessimistic gain','Pessimistic + recorded injuries'],[[fmt(r.planned.before),fmt(r.planned.after),fmt(r.planned.gain),fmt(r.planned.healthy_gain),needFocus.length===1?fmt(r.planned.leader_out_gain):'Choose one position',needFocus.length===1?fmt(r.planned.insurance_gain):'Not applicable',fmt(r.planned.pessimistic),fmt(r.planned.health_pessimistic)]])+'<p>Planned starters traded away: '+esc(names(r.planned.missing_starters))+'. Remaining chosen starters are retained; vacated slots use the best legal replacement. Negative stress results mean this gain is sensitive to projection errors; recorded injuries are a separate stress scenario, not an October health prediction.</p>':'')+
          (r.depth_fit?'<p><strong>'+esc(r.depth_fit.target)+' scenario:</strong> Unrestricted healthy gain '+fmt(r.depth_fit.healthy_gain)+'; original leader out gain '+fmt(r.depth_fit.backup_gain)+'; actual absence-penalty reduction '+fmt(r.depth_fit.insurance_gain)+' FP/week. '+(r.depth_fit.insurance_gain>1e-8?'Reduces reliance on that leader.':'General depth/production gain, not a reduction in leader reliance.')+' These overlapping scenarios must not be added.</p>':'')+
          weekTradeTable(roster(t),a.after_roster.map(p=>DATA.players[p.id]),roster(other),b.after_roster.map(p=>DATA.players[p.id]),cfg,plannedIds,other,comparisonWeek)+
          researchTable(mine.filter(p=>r.give.includes(p.id)).concat(roster(other,horizon).filter(p=>r.get.includes(p.id))))+
          tradeHealthNotice(r.result)+
          (r.fit_price?'<p class="warning"><strong>Positional-fit offer — not necessarily mutually beneficial.</strong> You send '+fmt(r.fit_price.sent)+' and receive '+fmt(r.fit_price.received)+' in summed individual expected FP/week: '+fmt(r.fit_price.overpay*100)+'% overpay. Individual values ignore lineup competition; the team gains above include it. '+(r.fit_price.asset_upgrade_for_other?'Their individual model-value upgrade':'Their nonnegative baseline lineup result')+' is a discussion point, not proof they would accept.</p>':'')+
          (r.depth_fit?'<p><strong>Roster fit:</strong> You add '+esc(r.depth_fit.target)+' depth ('+r.depth_fit.count_before+' → '+r.depth_fit.count_after+' eligible players) while keeping '+esc(names(r.depth_fit.preserved))+'. '+esc(other.manager)+' has fewer '+esc(r.depth_fit.source_positions.join(' / '))+' options than you and receives '+esc(names(r.give))+'. '+(r.fit_price?'Both rosters satisfy the selected fit-mode limits; review any displayed losses.':'Both rosters still pass the selected projected-gain test.')+'</p>':'')+
          '<p class="'+(a.pickups.length||b.pickups.length?'warning':'muted')+'">'+(a.pickups.length||b.pickups.length?'Waiver-dependent scenario: gains below include hypothetical claims, net of the waiver move each side could make without the trade. Do not present it as a standalone offer.':'Standalone offer calculation: no free-agent pickup is needed for either displayed gain.')+'</p>'+
          table(['Side','Starter gain','Bench/availability gain','Pessimistic to optimistic','Cut','Claim'],[[t.manager,fmt(a.delta_starters),fmt(a.delta_depth),fmt(a.pessimistic)+' to '+fmt(a.optimistic),names(a.drops),names(a.pickups)],[other.manager,fmt(b.delta_starters),fmt(b.delta_depth),fmt(b.pessimistic)+' to '+fmt(b.optimistic),names(b.drops),names(b.pickups)]])+
          '<p class="muted">'+esc(r.result.classification)+' under stress. '+(a.pessimistic>0&&b.pessimistic>=0?'Both sides stay nonnegative in the pessimistic scenario.':'Speculative: at least one side loses in the pessimistic scenario.')+' Roster fit is a discussion point, not an acceptance prediction. Stress is not a confidence interval.</p>'+
          (r.needs_a?'<p>Comparable needs improved: '+esc(r.needs_a.filter(n=>n.search_improvement>1e-8).map(n=>n.position).join(', ')||'None')+'. Needs worsened: '+esc(r.needs_a.filter(n=>n.search_improvement<-1e-8).map(n=>n.position).join(', ')||'None')+'.</p><details><summary>How this trade changes both teams’ positional needs</summary>'+needChangesTable(t.manager,r.needs_a)+needChangesTable(other.manager,r.needs_b)+needsDefinition()+'</details>':'')+
          '</details><button data-proposal="'+i+'">Compare this offer</button></article>';
      }).join(''):'<p>No clear upgrade found under these settings—not proof that no worthwhile deal exists. Try “Explore bigger PG deals” to include starters, or review Advanced settings. Injury checks still apply. No offers were submitted.</p>';
      document.querySelectorAll('[data-proposal]').forEach(button=>button.onclick=()=>{
        const r=report.results[Number(button.dataset.proposal)];activeView='Trade Evaluator';render();
        $('opponent').value=r.opponent;$('opponent').onchange();$('stress').value=cfg.stress*100;$('replacements').checked=r.replacements;
        document.querySelectorAll('[name=give]').forEach(el=>el.checked=r.give.includes(el.value));
        document.querySelectorAll('[name=get]').forEach(el=>el.checked=r.get.includes(el.value));
        $('evaluate').click();
      });
    }catch(e){if(toolsRenderToken===token)$('auto-progress').textContent=e.message;}
    finally{if(toolsRenderToken===token){$('auto-run').disabled=false;$('auto-cancel').disabled=true;controls.forEach(el=>el.disabled=false);$('auto-source').disabled=$('auto-mode').value==='broad';$('auto-other').disabled=$('auto-mode').value==='fit';$('auto-waivers').disabled=$('auto-mode').value==='fit';}}
  };
  simplifyTradeFinder(t);
}

function renderLockAdvisor(t) {
  const data=DATA.lock_advisor;
  if(!data?.players||!Object.keys(data.players).length){$('view').innerHTML='<h2>Lock-In advisor</h2><p>Rebuild the league forecast to supply the scoring distributions.</p>';return;}
  const available=t.roster.filter(id=>data.players[id]).sort((a,b)=>DATA.players[b].value-DATA.players[a].value);
  const first=data.schedule.games[0]?.date;
  const monday=s=>{const d=new Date(s+'T00:00:00Z');d.setUTCDate(d.getUTCDate()-(d.getUTCDay()+6)%7);return d;};
  const anchor=first?monday(first):null;
  const gameWeek=g=>anchor?Math.floor((new Date(g.date+'T00:00:00Z')-anchor)/604800000)+1:1;
  const maxWeek=Math.max(1,...data.schedule.games.map(gameWeek));
  const today=new Date();const current=anchor?Math.max(1,Math.min(maxWeek,Math.floor((today-anchor)/604800000)+1)):1;
  $('view').innerHTML='<h2>Lock-In advisor · '+esc(t.manager)+'</h2><p>Choose a completed game: keep the score or wait? Nothing here locks a game in Sleeper. Head-to-head mode compares modeled chances of beating your weekly opponent; points mode maximizes expected fantasy points.</p>'+
    '<div class="toolbar"><label>Player <select id="lock-player">'+available.map(id=>'<option value="'+esc(id)+'">'+esc(name(id))+'</option>').join('')+'</select></label>'+
    '<label>Fantasy week <select id="lock-week">'+Array.from({length:maxWeek},(_,i)=>'<option value="'+(i+1)+'">Week '+(i+1)+'</option>').join('')+'</select></label>'+
    '<label>Objective <select id="lock-objective"><option value="matchup">Win head-to-head matchup</option><option value="points">Maximize expected points</option></select></label></div>'+
    '<div id="lock-matchup-source"></div><div id="lock-schedule"></div>'+
    '<div class="toolbar"><label>Which completed fixture? <select id="lock-fixture"><option value="">Manual game count (no context adjustment)</option></select></label><label><input id="lock-context" type="checkbox" checked> Use validated opponent / venue / back-to-back adjustment</label></div><p id="lock-context-note" class="muted"></p>'+
    '<div class="toolbar"><label>Completed game FP <input id="lock-score" type="number" step="0.25" placeholder="Enter score"></label>'+
    '<label>Team games still to come this week <input id="lock-remaining" type="number" min="0" max="8" step="1" placeholder="0–8"></label>'+
    '<label>Chance of playing EACH future game % <input id="lock-play" type="number" min="0" max="100" value="100"></label></div>'+
    '<p class="muted">Appearance chance is your scenario assumption, not a live injury forecast. 100% assumes he plays every remaining game. A DNP in the team’s final game can produce zero; Sleeper does not return to an earlier score.</p>'+
    '<div id="lock-h2h"><h3>This week’s matchup state</h3><div class="toolbar"><label>Opponent <select id="lock-opponent">'+DATA.teams.filter(q=>q.team_id!==t.team_id).map(q=>'<option value="'+esc(q.team_id)+'">'+esc(q.manager)+'</option>').join('')+'</select></label></div>'+
    '<p>Enter only already locked points below. Exclude this player’s candidate score from your total, so it is not counted twice. Remaining estimates also exclude this player on your side.</p>'+
    '<div class="columns"><label>Your locked points <input id="lock-my-score" type="number" step="0.25"></label><label>Their locked points <input id="lock-their-score" type="number" step="0.25"></label>'+
    '<label>Your OTHER players: expected points still to come <input id="lock-my-rest" type="number" step="0.1"></label><label>Their players: expected points still to come <input id="lock-their-rest" type="number" step="0.1"></label>'+
    '<label>Uncertainty of remaining point DIFFERENCE (SD, FP) <input id="lock-uncertainty" type="number" min="0" step="1" placeholder="0 if all other scores final"></label></div>'+
    '<button id="lock-preweek">Fill a pre-week starter scenario</button><p class="muted">Pre-week helper uses the selected week’s API starters where available, otherwise projected starters. Assumes none of their scores is locked yet. It is not suitable unchanged once games have started. Edit every field to match your live matchup.</p></div>'+
    '<h3>Confirm in Sleeper before acting</h3><p>'+[['completed','This game is completed'],['eligible_starter','He was on my team in a starting slot when that game began (not bench or IR)'],['latest_game','This is his team’s latest completed game, not an earlier score'],['deadline_open','The next game has NOT started and Sleeper still permits locking this score'],['already_locked','This player is already locked for the week']].map(([id,label])=>'<label><input id="lock-'+id+'" type="checkbox"> '+label+'</label><br>').join('')+'</p>'+
    '<button id="lock-calculate">Compare lock vs wait</button><div id="lock-result" role="status"></div>'+
    '<details><summary>Model limitations</summary><p>Game-context adjustments use historical opponent, venue and team back-to-back associations in fantasy production per minute. They are capped at ±10% and enabled only after improving a chronological holdout; otherwise factors stay neutral. This is not a separate pace, travel, role or live injury forecast. Appearance chance remains your assumption. Future scores are independent. Head-to-head treats other players’ net remaining points as an independent normal distribution with your entered mean and SD, and assumes no new information before later decisions. Win-equivalent outcomes count ties as half; these are scenario estimates, not calibrated win odds. Recompute as the matchup changes.</p><p>Verify week dates and missing fixtures against Sleeper. Dates are not verified tipoff times. Rookie variability is simulated. Rebuilt forecasts preserve negative fantasy scores; a final-game DNP remains zero, not an earlier score.</p></details>';
  if(!available.length){$('lock-calculate').disabled=true;return;}
  $('lock-week').value=current;
  const weekRows=()=>DATA.matchups?.[$('lock-week').value];
  const opponent=()=>DATA.teams.find(q=>q.team_id===$('lock-opponent').value);
  const fixturesFor=(player)=>data.schedule.games.filter(g=>gameWeek(g)===Number($('lock-week').value)&&(g.home===player.team||g.away===player.team));
  const contextFor=(g,player)=>gameContextFactor(g,player.team,data.schedule.games,data.game_context,$('lock-context').checked);
  function resetState(){
    ['score','remaining','my-score','their-score','my-rest','their-rest','uncertainty'].forEach(id=>$('lock-'+id).value='');
    ['completed','eligible_starter','latest_game','deadline_open','already_locked'].forEach(id=>$('lock-'+id).checked=false);
    $('lock-result').innerHTML='';
    $('lock-fixture').value='';
  }
  function showSchedule(){
    const player=data.players[$('lock-player').value],week=Number($('lock-week').value);
    const fixtures=data.schedule.games.filter(g=>gameWeek(g)===week&&(g.home===player.team||g.away===player.team));
    const factors=fixtures.map(g=>contextFor(g,player));
    $('lock-schedule').innerHTML='<h3>'+esc(name($('lock-player').value))+' · '+esc(player.team)+' · Week '+week+'</h3>'+table(['Date (not tipoff)','NBA opponent','Venue','Back-to-back','Context change','Expected FP if played'],fixtures.map((g,i)=>[g.date,g.home===player.team?g.away:g.home,g.home===player.team?'Home':'Away',factors[i].backToBack?'Yes':'No',fmt((factors[i].factor-1)*100)+'%',fmt(player.sample.reduce((s,x)=>s+x,0)/player.sample.length*factors[i].factor)]))+
      '<p class="muted">'+esc(player.basis)+' · '+player.historical_games+' historical NBA games. '+esc(DATA.players[$('lock-player').value]?.notes||'')+'</p>'+
      (!fixtures.length?'<p class="warning">No loaded fixture for this player/week. Confirm schedule coverage; absence here is not proof of no game.</p>':'')+
      ((loaded=>loaded<82?'<p class="warning">Only '+loaded+' of 82 '+esc(player.team)+' games are loaded (NBA Cup games with TBD dates are missing). In weeks around the Cup, check Sleeper for extra games before trusting “games still to come”.</p>':'')(data.schedule.games.filter(g=>g.home===player.team||g.away===player.team).length));
    $('lock-fixture').innerHTML='<option value="">Manual game count (no context adjustment)</option>'+fixtures.map((g,i)=>'<option value="'+i+'">'+esc(g.date+' vs '+factors[i].opponent)+'</option>').join('');
    $('lock-context-note').textContent=(data.game_context?.note||'No fitted context model in this forecast; rebuild first.')+' Select the actual completed fixture to compare the remaining games in order. Highest expected game score is not an instruction to skip every earlier score. '+(data.schedule.notes||[]).join('; ');
    const snap=weekRows();
    const pair=snap&&Object.values(snap.pairs).find(p=>p.includes(t.team_id));
    if(pair)$('lock-opponent').value=pair.find(id=>id!==t.team_id);
    $('lock-matchup-source').innerHTML=snap?'<p>Week '+week+' API pairing: '+esc(t.manager)+' vs '+esc(pair?DATA.teams.find(q=>q.team_id===pair.find(id=>id!==t.team_id)).manager:'bye / unavailable')+'. Retrieved '+esc(new Date(snap.retrieved_at).toLocaleString())+'. API scores are not treated as confirmed locked points.</p>':'<p class="warning">No API matchup snapshot for this week. Choose the opponent manually, or refresh this week with league-matchups and rebuild the dashboard.</p>';
  }
  $('lock-player').onchange=()=>{resetState();showSchedule();};$('lock-week').onchange=()=>{resetState();showSchedule();};
  $('lock-fixture').onchange=()=>{const selected=$('lock-fixture').value;if(selected!=='')$('lock-remaining').value=fixturesFor(data.players[$('lock-player').value]).length-Number(selected)-1;else $('lock-remaining').value='';['completed','eligible_starter','latest_game','deadline_open','already_locked'].forEach(id=>$('lock-'+id).checked=false);$('lock-score').value='';$('lock-result').innerHTML='';};
  $('lock-context').onchange=()=>{resetState();showSchedule();};
  $('lock-opponent').onchange=resetState;
  $('lock-objective').onchange=()=>{$('lock-h2h').hidden=$('lock-objective').value==='points';$('lock-result').innerHTML='';};
  $('lock-preweek').onclick=()=>{
    const snap=weekRows();
    function estimate(team,exclude){
      const ids=snap?.rows.find(r=>String(r.roster_id)===team.team_id)?.starters.map(id=>'sleeper:'+id)||team.profile?.lineup.map(row=>row[1])||[];
      if(!ids.length)throw Error('No starter list for '+team.manager+' this week (no matchup snapshot and no complete projections). Enter remaining estimates manually.');
      const players=ids.filter(id=>id!==exclude);
      if(players.some(id=>!DATA.players[id]||!data.players[id]))throw Error('Some starters lack a forecast. Enter remaining estimates manually.');
      return players.reduce((total,id)=>{
        const player=data.players[id];
        const fixtures=fixturesFor(player),games=fixtures.length;
        const m=weeklyScoreMoments(player.sample,games,player.play_probability??1,fixtures.map(g=>contextFor(g,player).factor));
        return {mean:total.mean+m.mean,variance:total.variance+m.variance};
      },{mean:0,variance:0});
    }
    try{const a=estimate(t,$('lock-player').value),b=estimate(opponent());
      $('lock-my-score').value=0;$('lock-their-score').value=0;$('lock-my-rest').value=a.mean.toFixed(2);$('lock-their-rest').value=b.mean.toFixed(2);$('lock-uncertainty').value=Math.sqrt(a.variance+b.variance).toFixed(2);
      $('lock-result').innerHTML='<p class="warning">Pre-week scenario filled—not live matchup state. Uses this week’s loaded game counts, forecast appearance probabilities and points-optimal stopping for the other starters. Games and players are assumed independent; missing fixtures understate remaining opportunity. Edit as needed.</p>';
    }catch(e){$('lock-result').textContent=e.message;}
  };
  $('lock-calculate').onclick=()=>{
    try{
      const number=id=>{const el=$('lock-'+id);if(!el.value.trim())throw Error('Fill the completed score, remaining games and required matchup fields.');const n=Number(el.value);if(!Number.isFinite(n))throw Error('Enter valid numbers');return n;};
      const sample=data.players[$('lock-player').value].sample,score=number('score'),games=number('remaining'),p=number('play')/100;
      const state=Object.fromEntries(['completed','eligible_starter','latest_game','deadline_open','already_locked'].map(id=>[id,$('lock-'+id).checked]));
      if($('lock-fixture').value!==''){
        const player=data.players[$('lock-player').value],future=fixturesFor(player).slice(Number($('lock-fixture').value)+1);
        if(future.length!==games)throw Error('Remaining count differs from selected schedule. Correct the fixture or switch to manual count.');
        state.factors=future.map(g=>contextFor(g,player).factor);
      }
      const points=lockAdvice(sample,score,games,p,state);
      const h2h=$('lock-objective').value==='matchup';
      const result=h2h?matchupLockAdvice(sample,score,games,number('my-score')-number('their-score')+number('my-rest')-number('their-rest'),number('uncertainty'),p,state):points;
      let html='<h3>'+esc(result.action.replaceAll('_',' '))+'</h3>';
      if(result.action==='VERIFY ELIGIBILITY')html+='<p class="warning">Scenario only: confirm all four eligibility checks before this becomes an actionable suggestion.</p>';
      if(result.action==='ALREADY LOCKED')html+='<p>This week’s decision is fixed; later scores cannot replace it.</p>';
      if(result.action==='KEEP FINAL GAME')html+='<p>No team games remain in this scenario. There is no future game to wait for. Confirm Sleeper’s final-game score and lineup state.</p>';
      html+='<div class="cards">'+metric('Completed score',score)+(games?metric('Expected points if you wait',points.threshold):'')+'</div>';
      if(h2h)html+='<h3>Head-to-head · vs '+esc(opponent().manager)+'</h3>'+table(['Choice','Modeled win-equivalent chance'],[['Keep score',fmt(result.lock_win_equivalent*100)+'%'],['Wait',games?fmt(result.wait_win_equivalent*100)+'%':'No future game']])+'<p class="muted">Ties count half. The two chances use your entered matchup state, not the API’s unverified lock status. Points-only comparison: '+esc(points.action)+'.</p>';
      html+='<h3>Unadjusted reference thresholds</h3><p class="muted">The table below assumes identical future games. The decision above uses the selected remaining fixtures when provided.</p>'+table(['Future games','Lock at or above FP'],Array.from({length:5},(_,i)=>[i+1,fmt(lockContinuation(sample,i+1,p))]));
      $('lock-result').innerHTML=html;
    }catch(e){$('lock-result').textContent=e.message;}
  };
  $('view').oninput=()=>{$('lock-result').innerHTML='';};
  $('view').onchange=()=>{$('lock-result').innerHTML='';};
  showSchedule();
}
