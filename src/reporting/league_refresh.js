/* Only the loopback-hosted copy may contact the refresh service. */
(function setupSleeperRefresh(){
  const button=$('refresh-sleeper'),status=$('refresh-status');
  const performance=$('refresh-performance');
  if(!button)return;
  if($('performance-freshness'))$('performance-freshness').textContent='Performance history through '+(DATA.forecast.performance_history_through||'unknown')+(DATA.forecast.in_season_through?' · This season blended through '+DATA.forecast.in_season_through+' (run league-archive, then league-build, to add newer games)':' · This season not blended')+' · Forecast built '+new Date(DATA.forecast.generated_at).toLocaleString()+' · Last NBA performance download: '+(DATA.forecast.performance_refreshed_at?new Date(DATA.forecast.performance_refreshed_at).toLocaleString():'not recorded by this refresh feature')+'. Training seasons: '+(DATA.forecast.seasons||[]).join(', ')+'.';
  const local=location.protocol==='http:'&&location.hostname==='127.0.0.1';
  let token=null,busy=false;
  function showChanges(last){
    if(!last)return;
    $('refresh-details').hidden=false;
    $('refresh-changes').innerHTML=last.changes.length?
      table(['Manager','Added','Dropped','IR changed'],last.changes.map(c=>[c.manager,c.added.join(', ')||'None',c.dropped.join(', ')||'None',c.reserve_changed?'Yes':'No'])):
      '<p>No roster changes since the previous snapshot.</p>';
  }
  showChanges(DATA.last_refresh);
  if(!local){
    status.textContent='Offline copy. Open the local dashboard for refresh access; start Open League Dashboard.cmd if needed.';
    button.onclick=()=>{status.textContent='Open the dashboard launcher in the repository, then use the address it shows.';};
    if(performance)performance.onclick=button.onclick;
    return;
  }
  async function request(path,options={}){
    const response=await fetch(path,{cache:'no-store',...options});
    const result=await response.json();
    if(!response.ok)throw Error(result.error||'Local refresh service unavailable');
    return result;
  }
  function lock(){
    busy=true;button.disabled=true;if(performance)performance.disabled=true;toolsRenderToken++;
    $('nav').inert=true;$('subnav').inert=true;$('view').inert=true;$('team').disabled=true;
    // No old recommendation may remain actionable while ownership changes.
    $('view').innerHTML='<p>Refreshing source data and recalculating team needs. Previous trade suggestions have been cleared.</p>';
  }
  function unlock(){busy=false;button.disabled=false;if(performance)performance.disabled=false;$('nav').inert=false;$('subnav').inert=false;$('view').inert=false;$('team').disabled=false;render();}
  async function watch(){
    for(let i=0;i<300;i++){
      const state=await request('/api/status');
      if(state.status==='succeeded'){
        status.textContent='Refresh complete. Loading the updated dashboard…';
        location.reload();return;
      }
      if(state.status==='failed')throw Error(state.error);
      if(state.status!=='running')throw Error('Refresh status was lost. Reconnect to the local dashboard.');
      status.textContent=state.kind==='performance'?'Downloading NBA training seasons, validating history and rebuilding projections… This can take several minutes.':'Reading Sleeper and checking rosters… Keep this tab open.';
      await new Promise(resolve=>setTimeout(resolve,2000));
    }
    throw Error('Refresh is taking longer than expected. Reload to check its status; do not start another service.');
  }
  button.disabled=true;
  if(performance)performance.disabled=true;
  request('/api/session').then(async session=>{
    if(session.app!=='nba-fantasy-league-desk')throw Error('Unexpected local service');
    token=session.token;
    const state=await request('/api/status');
    if(state.status==='running'){lock();await watch();return;}
    button.disabled=false;
    if(performance)performance.disabled=false;
    status.textContent=state.status==='failed'?'Last refresh failed: '+state.error:
      'Ready. Last roster snapshot: '+new Date(DATA.ownership_observed_at).toLocaleString()+'.';
  }).catch(e=>{status.textContent='Refresh unavailable: '+e.message+'. Reopen Open League Dashboard.cmd.';if(busy)unlock();});
  async function start(kind){
    if(busy||!token)return;
    lock();status.textContent=kind==='performance'?'Starting NBA performance refresh…':'Starting Sleeper refresh…';
    try{
      await request(kind==='performance'?'/api/refresh-performance':'/api/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Refresh-Token':token},body:'{}'});
      await watch();
    }catch(e){status.textContent='Refresh failed: '+e.message+' The last displayed snapshot has not been refreshed.';unlock();}
  }
  button.onclick=()=>start('sleeper');
  if(performance)performance.onclick=()=>start('performance');
})();
