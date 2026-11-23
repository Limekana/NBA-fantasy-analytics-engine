/* Decision parity with src/league/lock_advisor.py. No live or remote actions. */
function lockContinuation(sample,games,p=1,factors=null) {
  if(!sample?.length||sample.some(x=>!Number.isFinite(x)))throw Error('A finite scoring distribution is required');
  if(!Number.isInteger(games)||games<0||games>8)throw Error('Remaining games must be an integer from 0 to 8');
  if(!Number.isFinite(p)||p<0||p>1)throw Error('Appearance probability must be between zero and one');
  factors=factors??Array(games).fill(1);
  if(factors.length!==games||factors.some(f=>!Number.isFinite(f)||f<.5||f>1.5))throw Error('One finite context factor (0.5–1.5) is required per future game');
  let value=0;
  for(let k=1;k<=games;k++){const factor=factors[games-k];value=p*sample.reduce((s,x)=>s+(k===1?x*factor:Math.max(x*factor,value)),0)/sample.length+(1-p)*value;}
  return value;
}
function lockAdvice(sample,score,games,p=1,state={}) {
  if(!Number.isFinite(score))throw Error('Enter a finite completed-game score');
  const threshold=lockContinuation(sample,games,p,state.factors);
  if(state.already_locked)return {action:'ALREADY LOCKED',threshold};
  if(!['completed','eligible_starter','latest_game','deadline_open'].every(k=>state[k]))return {action:'VERIFY ELIGIBILITY',threshold};
  return {action:games===0?'KEEP FINAL GAME':score>=threshold?'LOCK':'WAIT',threshold,lock_advantage:games===0?null:score-threshold};
}

function normalCdf(x) {
  const t=1/(1+0.2316419*Math.abs(x));
  const tail=0.3989422804014327*Math.exp(-x*x/2)*t*(0.319381530+t*(-0.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))));
  return x>=0?1-tail:tail;
}
function weeklyScoreMoments(sample,games,p=1,factors=null) {
  lockContinuation(sample,games,p,factors); // shared input validation
  factors=factors??Array(games).fill(1);
  let mean=0,second=0;
  for(let k=1;k<=games;k++){
    let accepted=0,sum=0,squares=0;
    for(const raw of sample){const x=raw*factors[games-k];if(k===1||x>=mean){accepted++;sum+=x;squares+=x*x;}}
    const pass=1-p*accepted/sample.length;
    mean=p*sum/sample.length+pass*mean;
    second=p*squares/sample.length+pass*second;
  }
  return {mean,variance:Math.max(0,second-mean*mean)};
}
function matchupLockAdvice(sample,score,games,margin,uncertainty,p=1,state={}) {
  const base=lockAdvice(sample,score,games,p,state);
  if(!Number.isFinite(margin)||!Number.isFinite(uncertainty)||uncertainty<0)throw Error('Enter a finite matchup margin and nonnegative uncertainty');
  const utility=x=>uncertainty===0?(margin+x>0?1:margin+x<0?0:0.5):normalCdf((margin+x)/uncertainty);
  let wait=utility(0);
  const factors=state.factors??Array(games).fill(1);
  for(let k=1;k<=games;k++)wait=p*sample.reduce((s,x)=>s+(k===1?utility(x*factors[games-k]):Math.max(utility(x*factors[games-k]),wait)),0)/sample.length+(1-p)*wait;
  const lock=utility(score);
  if(['LOCK','WAIT'].includes(base.action))base.action=lock>=wait?'LOCK':'WAIT';
  return {...base,lock_win_equivalent:lock,wait_win_equivalent:games===0?null:wait,objective:'head_to_head'};
}
function gameContextFactor(game,team,allGames,model,use=true) {
  const home=game.home===team,opponent=home?game.away:game.home;
  const previous=new Date(game.date+'T00:00:00Z');previous.setUTCDate(previous.getUTCDate()-1);
  const yesterday=previous.toISOString().slice(0,10);
  const backToBack=allGames.some(g=>g.date===yesterday&&(g.home===team||g.away===team));
  const known=model?.enabled&&Object.prototype.hasOwnProperty.call(model.opponents,opponent);
  const effect=use&&known?(model.opponents[opponent]+model.home*(home?.5:-.5)+model.back_to_back*Number(backToBack)):0;
  return {factor:1+Math.max(-.1,Math.min(.1,effect)),backToBack,opponent,active:!!(use&&known)};
}

/* Enumerate every active-roster 1:1; packages are an explicitly bounded search.
   Shortlisted unequal packages are re-evaluated with the entire eligible FA pool.
   Base-lineup screening is heuristic and can miss a package rescued by a pickup. */
async function findLeagueTrades(mine,opponents,freeAgents,cfg,options={},progress=()=>{},cancelled=()=>false) {
  const minGain=options.minGain??0.25,otherMin=options.otherMin??0.1;
  const focus=options.needFocus??[],references=options.needReferences;
  const payForFit=options.searchMode==='fit';
  const planned=options.plannedStarterIds||[];
  if(planned.length)validateStarterPlan(mine,planned,cfg);
  const leader=focus.length===1?positionMetrics(mine,cfg)[focus[0]]?.leader:null;
  const protectedIds=options.protectedStarterIds||planned;
  const contextPass=item=>(!options.protectStarters||!item.give.some(id=>protectedIds.includes(id)))&&
    (!item.planned||(item.planned.gain>1e-8&&item.planned.gain+1e-8>=minGain&&item.planned.health_gain>=-1e-8&&(!options.robust||(item.planned.pessimistic>1e-8&&item.planned.health_pessimistic>1e-8))))&&
    (!options.requireInsurance||(leader&&!item.give.includes(leader)&&(item.planned?.insurance_gain??item.depth_fit?.insurance_gain??0)>1e-8))&&
    (!options.requireResearch||tradeResearchStatus(mine.filter(p=>item.give.includes(p.id)).concat(item.result.team_a.after_roster.filter(p=>item.get.includes(p.id))),options.reviews||{}).every(r=>r.complete));
  const surplus=options.searchMode==='surplus'||payForFit;
  const maxOverpay=options.maxOverpay??.15,maxOtherLoss=options.maxOtherLoss??1;
  if(payForFit&&(!Number.isFinite(maxOverpay)||maxOverpay<0||maxOverpay>.5||!Number.isFinite(maxOtherLoss)||maxOtherLoss<0||maxOtherLoss>3))throw Error('Fit limits: overpay 0–50%; their lineup loss 0–3 FP/week');
  if(surplus&&focus.length!==1)throw Error('Select exactly one position for added-depth search');
  if(focus.length&&(!references||focus.some(p=>!references[p])))throw Error('Complete positional benchmarks are required for the selected focus');
  if(!Number.isFinite(minGain)||minGain<0||!Number.isFinite(otherMin)||otherMin<0)throw Error('Minimum gains must be nonnegative');
  const results=[];let singles=0,packagesScreened=0,packagesEvaluated=0;
  const excludedInjuries=[...new Set(mine.concat(...opponents.map(o=>o.roster)).filter(healthRisk).map(p=>p.id))];
  freeAgents=freeAgents.filter(p=>!healthRisk(p));
  const pause=()=>new Promise(resolve=>setTimeout(resolve,0));
  function passes(item){const r=item.result;return !r.team_a.traded_injury_risk&&!r.team_b.traded_injury_risk&&r.team_a.health_delta>1e-8&&r.team_b.health_delta>=-(payForFit?maxOtherLoss:0)-1e-8&&r.team_a.delta_weekly>1e-8&&r.team_a.delta_weekly+1e-8>=minGain&&(payForFit?(item.fit_price.within_limit&&(item.fit_price.asset_upgrade_for_other||r.team_b.delta_weekly>=(item.exact?0:-.25)-1e-8)&&r.team_b.delta_weekly>=-maxOtherLoss-1e-8):r.team_b.delta_weekly+1e-8>=otherMin)&&(!options.robust||(r.team_a.pessimistic>1e-8&&r.team_b.pessimistic>=-1e-8&&r.team_a.health_pessimistic>1e-8&&r.team_b.health_pessimistic>=-1e-8))&&(surplus?!!item.depth_fit:(!focus.length||(item.focus_gain>1e-8&&item.total_need_gain>=-1e-8)));}
  function merit(r){return Math.min(r.team_a.delta_weekly,r.team_b.delta_weekly)+0.15*r.team_a.delta_weekly;}
  const robust=r=>r.team_a.pessimistic>1e-8&&r.team_b.pessimistic>=-1e-8&&r.team_a.health_pessimistic>1e-8&&r.team_b.health_pessimistic>=-1e-8?1:0;
  const compare=(a,b)=>(surplus?Number(!!b.depth_fit)-Number(!!a.depth_fit):0)||robust(b.result)-robust(a.result)||(b.planned?.gain??b.result.team_a.delta_weekly)-(a.planned?.gain??a.result.team_a.delta_weekly)||b.rank-a.rank||(focus.length?b.focus_gain-a.focus_gain:0)||a.key.localeCompare(b.key);
  for(const other of opponents) {
    if(cancelled())return {cancelled:true};
    const seeds=[],shortlist=[];
    const evaluate=(give,get,replacements=false)=>{
      const result=evaluateLeagueTrade(mine,other.roster,give,get,replacements?freeAgents:[],cfg,replacements,false);
      const key=other.id+'|'+give.slice().sort().join(',')+'|'+get.slice().sort().join(',');
      const needs_a=references?tradeNeeds(mine,result.team_a.after_roster,cfg,references):null;
      const needs_b=other.needReferences?tradeNeeds(other.roster,result.team_b.after_roster,cfg,other.needReferences):null;
      return {opponent:other.id,give,get,result,key,rank:merit(result),needs_a,needs_b,
        planned:planned.length?plannedTradeImpact(mine,result.team_a.after_roster,cfg,planned,leader):null,
        fit_price:payForFit?fitPrice(mine,other.roster,give,get,maxOverpay):null,
        replacements,depth_fit:surplus?surplusDepthFit(mine,result.team_a.after_roster,other.roster,give,get,focus[0],cfg,options.offerFrom??'wings',payForFit):null,
        focus_gain:needs_a?.filter(r=>focus.includes(r.position)).reduce((s,r)=>s+r.search_improvement,0)||0,
        total_need_gain:needs_a?.reduce((s,r)=>s+r.search_improvement,0)||0};
    };
    for(const a of mine.filter(p=>!healthRisk(p)))for(const b of other.roster.filter(p=>!healthRisk(p))){
      const item=evaluate([a.id],[b.id]);singles++;seeds.push(item);
      if(passes(item)&&contextPass(item))results.push(item);
      if(singles%50===0){progress({singles,packagesScreened,packagesEvaluated});await pause();if(cancelled())return {cancelled:true};}
    }
    if(options.packages!==false) {
      seeds.sort(compare);const seen=new Set();
      const screen=(give,get)=>{
        give=[...new Set(give)].sort();get=[...new Set(get)].sort();
        if(give.length===1&&get.length===1)return;
        const key=give.join(',')+'|'+get.join(',');if(seen.has(key))return;seen.add(key);
        const item=evaluate(give,get);packagesScreened++;
        if(give.length===get.length){packagesEvaluated++;if(passes(item)&&contextPass(item))results.push(item);}
        else shortlist.push(item);
      };
      const beam=seeds.slice(0,12);
      for(let i=0;i<beam.length;i++)for(let j=i+1;j<beam.length;j++){
        const x=beam[i],y=beam[j];
        screen(x.give.concat(y.give),x.get.concat(y.get));
      }
      // Include both consolidation directions, even when the single-trade seeds
      // happen not to share an incoming/outgoing asset.
      const out=[...new Set(beam.flatMap(x=>x.give))].slice(0,6);
      const incoming=[...new Set(beam.flatMap(x=>x.get))].slice(0,6);
      for(let i=0;i<out.length;i++)for(let j=i+1;j<out.length;j++)for(const b of incoming)screen([out[i],out[j]],[b]);
      for(let i=0;i<incoming.length;i++)for(let j=i+1;j<incoming.length;j++)for(const a of out)screen([a],[incoming[i],incoming[j]]);
      shortlist.sort(compare);
      for(const item of shortlist.slice(0,8)){
        await pause();if(cancelled())return {cancelled:true};
        const full=evaluate(item.give,item.get,!payForFit&&options.replacements!==false);packagesEvaluated++;
        if(passes(full)&&contextPass(full))results.push(full);
        progress({singles,packagesScreened,packagesEvaluated});
      }
    }
    progress({singles,packagesScreened,packagesEvaluated});await pause();
  }
  // Show gain to this manager first, then mutual benefit; deterministic ties.
  results.sort(compare);
  // Fast screening must not turn integration noise into a recommendation.
  // Verify a bounded finalist shortlist exactly, including cuts/claims/stress.
  const finalists=results.slice(0,60),verified=[];
  for(const item of finalists){
    await pause();if(cancelled())return {cancelled:true};
    const other=opponents.find(o=>o.id===item.opponent);
    item.result=evaluateLeagueTrade(mine,other.roster,item.give,item.get,item.replacements?freeAgents:[],cfg,item.replacements,true);
    item.exact=true;
    item.planned=planned.length?plannedTradeImpact(mine,item.result.team_a.after_roster,cfg,planned,leader,true):null;
    item.needs_a=references?tradeNeeds(mine,item.result.team_a.after_roster,cfg,references):null;
    item.needs_b=other.needReferences?tradeNeeds(other.roster,item.result.team_b.after_roster,cfg,other.needReferences):null;
    item.depth_fit=surplus?surplusDepthFit(mine,item.result.team_a.after_roster,other.roster,item.give,item.get,focus[0],cfg,options.offerFrom??'wings',payForFit):null;
    item.focus_gain=item.needs_a?.filter(r=>focus.includes(r.position)).reduce((s,r)=>s+r.search_improvement,0)||0;
    item.total_need_gain=item.needs_a?.reduce((s,r)=>s+r.search_improvement,0)||0;
    item.rank=merit(item.result);
    if(passes(item)&&contextPass(item))verified.push(item);
  }
  verified.sort(compare);
  const unique=new Map(verified.map(r=>[r.key,r]));
  const counts={},diverse=[];
  for(const row of unique.values())if((counts[row.opponent]||0)<3){diverse.push(row);counts[row.opponent]=(counts[row.opponent]||0)+1;}
  return {results:diverse,singles,packagesScreened,packagesEvaluated,qualifying:unique.size,excludedInjuries,exactChecked:finalists.length};
}
