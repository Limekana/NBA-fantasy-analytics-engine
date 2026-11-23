/* Parity with src/league/needs.py; same definitions in profiles and trades. */
const NEED_POSITIONS=['PG','SG','SF','PF','C'];
function positionMetrics(roster,cfg) {
  const lineup=optimalLineup(roster,cfg),starters=new Set(lineup.assignments.filter(a=>a.player).map(a=>a.player.id)),rows={};
  for(const pos of NEED_POSITIONS){
    if(!cfg.elig[pos]?.some(slot=>cfg.slots.includes(slot)))continue;
    const players=roster.filter(p=>(p.positions?.length?p.positions:[p.position]).includes(pos)).sort((a,b)=>b.value*b.availability-a.value*a.availability||(a.id<b.id?-1:a.id>b.id?1:0));
    const leader=players[0];
    rows[pos]={position:pos,leader:leader?.id??null,quality:leader?leader.value*leader.availability:0,
      outage_loss:leader?Math.max(0,lineup.total-optimalLineup(roster.filter(p=>p.id!==leader.id),cfg).total):0,
      eligible_count:players.length,bench_cover:players.filter(p=>!starters.has(p.id)).map(p=>p.id)};
  }
  return rows;
}
function scoreNeeds(metrics,references) {
  const rows=Object.entries(metrics).map(([pos,item])=>{
    const peer=references[pos],production_gap=Math.max(0,peer.median_quality-item.quality),cover_gap=Math.max(0,item.outage_loss-peer.median_outage_loss);
    let reason=production_gap>1e-8&&cover_gap>1e-8?'Production + injury cover':production_gap>1e-8?'Starting production':cover_gap>1e-8?'Injury cover / star reliance':'No above-median need';
    if(!item.eligible_count)reason='No eligible player';
    return {...item,production_rank:1+peer.qualities.filter(v=>v>item.quality+1e-8).length,teams_compared:peer.qualities.length+1,
      median_quality:peer.median_quality,median_outage_loss:peer.median_outage_loss,production_gap,cover_gap,need_score:production_gap+cover_gap,reason};
  });
  rows.sort((a,b)=>(a.eligible_count>0)-(b.eligible_count>0)||(Math.round(b.need_score*1e8)-Math.round(a.need_score*1e8))||NEED_POSITIONS.indexOf(a.position)-NEED_POSITIONS.indexOf(b.position));
  return rows.map((r,i)=>({...r,priority:i+1}));
}
function tradeNeeds(before,after,cfg,references) {
  const b=scoreNeeds(positionMetrics(before,cfg),references),a=Object.fromEntries(scoreNeeds(positionMetrics(after,cfg),references).map(r=>[r.position,r]));
  return b.map(r=>({position:r.position,before:r.need_score,after:a[r.position].need_score,improvement:r.need_score-a[r.position].need_score,
    leader_changed:r.leader!==a[r.position].leader,
    leader_before:r.leader,leader_after:a[r.position].leader,
    eligible_before:r.eligible_count,eligible_after:a[r.position].eligible_count,
    healthy_lineup_gain:optimalLineup(after,cfg).total-optimalLineup(before,cfg).total,
    lineup_gain_without_original_leader:optimalLineup(after.filter(p=>p.id!==r.leader),cfg).total-optimalLineup(before.filter(p=>p.id!==r.leader),cfg).total,
    search_improvement:r.production_gap-a[r.position].production_gap+(r.leader!==a[r.position].leader?Math.min(0,r.cover_gap-a[r.position].cover_gap):r.cover_gap-a[r.position].cover_gap),
    production_change:a[r.position].quality-r.quality,outage_loss_change:a[r.position].outage_loss-r.outage_loss}));
}
function surplusDepthFit(before,after,otherBefore,give,get,target,cfg,source='wings',payForFit=false) {
  const eligible=(p,pos)=>(p.positions?.length?p.positions:[p.position]).includes(pos);
  // A C/PF is big-man cover, not a surplus wing merely because PF is listed.
  // The explicit any-position mode may consider these assets instead.
  const wing=p=>!eligible(p,'C')&&['SG','SF','PF'].some(pos=>eligible(p,pos));
  const existing=before.filter(p=>eligible(p,target));
  const incoming=after.filter(p=>get.includes(p.id)&&eligible(p,target));
  if(!incoming.length||existing.some(p=>!after.some(q=>q.id===p.id))||after.filter(p=>eligible(p,target)).length<=existing.length)return null;
  const metrics=positionMetrics(before,cfg),leaders=new Set(Object.values(metrics).map(r=>r.leader));
  const outgoing=before.filter(p=>give.includes(p.id));
  const best=before.slice().sort((a,b)=>b.value*b.availability-a.value*a.availability||a.id.localeCompare(b.id))[0];
  if(!outgoing.length||outgoing.some(p=>eligible(p,target)||(payForFit?p.id===best?.id:leaders.has(p.id))))return null;
  // Surplus is explicit relative eligibility, not a claim about market value.
  const supplied=outgoing.map(p=>source==='wings'?
    (wing(p)&&before.filter(wing).length>otherBefore.filter(wing).length?'wings':null):
    NEED_POSITIONS.find(pos=>pos!==target&&eligible(p,pos)&&before.filter(q=>eligible(q,pos)).length>otherBefore.filter(q=>eligible(q,pos)).length));
  if(supplied.some(pos=>!pos))return null;
  const leader=metrics[target]?.leader;
  const backupBefore=optimalLineup(before.filter(p=>p.id!==leader),cfg).total;
  const backupAfter=optimalLineup(after.filter(p=>p.id!==leader),cfg).total;
  if(backupAfter<=backupBefore+1e-8)return null;
  const healthyGain=optimalLineup(after,cfg).total-optimalLineup(before,cfg).total;
  return {target,source_positions:[...new Set(supplied)],count_before:existing.length,count_after:after.filter(p=>eligible(p,target)).length,
    backup_gain:backupAfter-backupBefore,healthy_gain:healthyGain,insurance_gain:backupAfter-backupBefore-healthyGain,
    preserved:existing.map(p=>p.id)};
}
function fitPrice(before,other,give,get,maxOverpay=0.15) {
  const sum=(rs,ids)=>rs.filter(p=>ids.includes(p.id)).reduce((s,p)=>s+p.value*p.availability,0);
  const sent=sum(before,give),received=sum(other,get);
  const overpay=received>0?Math.max(0,(sent-received)/received):Infinity;
  return {sent,received,overpay,within_limit:Number.isFinite(overpay)&&overpay<=maxOverpay+1e-8,
    asset_upgrade_for_other:sent>=received-1e-8};
}
function needSummary(t,horizon='regular') {
  return horizon==='regular'?t.positional_needs:t.playoff_positional_needs;
}
function needsTable(t,horizon='regular') {
  const needs=needSummary(t,horizon);
  if(!needs)return '<p class="warning">Positional league comparisons require complete projection coverage for every team in this horizon.</p>';
  return table(['Priority','Position','Need index ↓ (not FP)','What to strengthen','Leading-player rank','Leader','Leading quality FP/week','Healthy-lineup loss if leader out','Bench eligibility (not health clearance)'],needs.rows.map(r=>[
    r.need_score>1e-8||!r.eligible_count?r.priority:'—',r.position,fmt(r.need_score),r.reason,r.production_rank+' / '+r.teams_compared,name(r.leader)||'None',fmt(r.quality),fmt(r.outage_loss),names(r.bench_cover)]));
}
function needsDefinition() {
  return '<p class="muted">Need score = shortfall below other teams’ median leading-player quality + excess lineup loss above their median when that leader is absent. Quality = conditional weekly FP × usable-week probability. Higher score means more need; zero means neither gap. Leading-player rank is not a rank of all players at that position. Multi-position players overlap; these scores are priorities, not additive FP gains. Bench cover is descriptive, not a separate score.</p>';
}
function needChangesTable(label,changes) {
  if(!changes)return '';
  return '<h4>'+esc(label)+' · positional depth and lineup impact</h4>'+table(['Position','Eligible players before → after','Leading player before → after','Leading quality Δ FP/week','Whole-lineup gain with original leader out','Actual reduction in absence penalty'],changes.map(r=>[r.position,r.eligible_before+' → '+r.eligible_after,name(r.leader_before)+' → '+name(r.leader_after),fmt(r.production_change),fmt(r.lineup_gain_without_original_leader),r.leader_changed?'Not comparable':fmt(r.lineup_gain_without_original_leader-r.healthy_lineup_gain)]))+
    '<p class="muted">Healthy whole-lineup gain: '+fmt(changes[0]?.healthy_lineup_gain)+' FP/week. Leader-out gains also reassign the entire lineup, including flexible slots; they overlap across positions and must not be added. All remaining players are assumed available in these descriptive scenarios; use the injury-tested trade totals for decisions.</p>'+
    '<details><summary>Need-index changes (not fantasy-point gains)</summary>'+table(['Position','Before index','After index','Comparable index reduction','Reduction in leader reliance'],changes.map(r=>[r.position,fmt(r.before),fmt(r.after),fmt(r.search_improvement),r.leader_changed?'Different leader: not comparable':fmt(-r.outage_loss_change)]))+
    '<p class="muted">Reliance reduction is leader-out lineup gain minus healthy lineup gain for the same leader. It is not the incoming player’s positional contribution. Positive cover credit is withheld when the leader changes.</p></details>';
}
function renderNeeds(t) {
  $('view').innerHTML='<h2>Positions to strengthen · '+esc(t.manager)+'</h2>'+blocked(t)+
    '<div class="toolbar"><label>Horizon <select id="needs-horizon"><option value="regular">Regular season</option><option value="playoffs">Playoffs</option></select></label><button id="needs-trades">Find trades for my top needs</button></div><div id="needs-content"></div>'+needsDefinition();
  const update=()=>{
    const horizon=$('needs-horizon').value,needs=needSummary(t,horizon);
    const top=needs?.rows.filter(r=>r.need_score>1e-8||!r.eligible_count).slice(0,2)||[];
    $('needs-content').innerHTML='<p>'+(top.length?'Priority areas: '+esc(top.map(r=>r.position+' — '+r.reason).join('; '))+'.':'No above-median positional gap in this comparison, or comparison unavailable.')+'</p>'+needsTable(t,horizon);
    $('needs-trades').disabled=!needs||!top.length;
  };
  $('needs-horizon').onchange=update;
  $('needs-trades').onclick=()=>{const h=$('needs-horizon').value;activeView='Automatic Trades';render();$('auto-horizon').value=h;$('auto-horizon').onchange();$('auto-focus').value='top';$('auto-run').click();};
  update();
}
