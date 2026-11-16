(() => {
  const root=document.getElementById('orc-telemetry-viewer');
  const find=role=>root.querySelector(`[data-role="${role}"]`);
  const data=JSON.parse(document.getElementById('orc-viewer-data').textContent);
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const format=(v,d=1)=>finite(v)?v.toFixed(d):'--';
  const reference=data.laps.find(l=>l.lap_id===data.analysis.reference_lap_id);
  const comparisons=data.laps.filter(l=>l.valid&&l.signals&&l!==reference);
  let selected=comparisons[0]||reference,index=Math.floor((data.progress.length-1)/4),range=[0,1],mapPoints=[];
  const color={reference:'#fff2c7',comparison:'#88dbff',grid:'#3a3524',text:'#9fb28d',segment:'#eac16f'};
  const crtPresets=__CRT_PRESETS__;
  const crt={profile:'AMBER',raster:'SCANLINE',character:3};
  function applyCrt() {
    const level=crtPresets.levels[crt.character],profile=crtPresets.profiles[crt.profile],raster=crtPresets.rasters[crt.raster];
    for(const definition of [profile,raster])for(const [key,base] of Object.entries(definition)) {
      let value=base,scale=1;
      if(crtPresets.artifactKeys.includes(key))scale=level.artifactScale;
      if(crtPresets.bloomKeys.includes(key))scale=level.bloomScale;
      if(crtPresets.jitterKeys.includes(key))scale=level.jitterScale;
      if(typeof value==='number')value=Math.min(crtPresets.caps[key]??Infinity,value*scale);
      else if(crtPresets.jitterKeys.includes(key))value=Math.min(crtPresets.caps[key]??Infinity,parseFloat(value)*scale)+'px';
      root.style.setProperty('--'+key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase()),String(value));
    }
    root.dataset.displayProfile=crt.profile;root.dataset.rasterMode=crt.raster;root.dataset.characterLevel=String(crt.character);
    color.reference=profile.phosphorHot;color.comparison=profile.syntaxTrace;color.segment=profile.syntaxDomain;color.text=profile.syntaxLabel;
    render();
  }
  function nearest(p) { let best=0;data.progress.forEach((v,i)=>{if(Math.abs(v-p)<Math.abs(data.progress[best]-p))best=i;});return best; }
  function canvas(role) {
    const el=find(role),rect=el.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    el.width=Math.max(1,Math.round(rect.width*dpr));el.height=Math.max(1,Math.round(rect.height*dpr));
    const ctx=el.getContext('2d');ctx.scale(dpr,dpr);ctx.font=getComputedStyle(root).fontSize+' "Courier New",monospace';
    return {ctx,w:rect.width,h:rect.height};
  }
  function trace(key) {
    const {ctx,w,h}=canvas(key),x0=plotLeft,x1=w-plotRight,y0=9,y1=h-12;
    const a=reference.signals[key],b=selected.signals[key],mul=key==='speed'?1:100;
    const max=key==='speed'?Math.max(20,Math.ceil(Math.max(...a.filter(finite),...b.filter(finite))/10)*10):100;
    const x=p=>x0+(p-range[0])/(range[1]-range[0])*(x1-x0),y=v=>y1-v*mul/max*(y1-y0);
    ctx.strokeStyle=color.grid;ctx.lineWidth=1;
    for(let i=0;i<=4;i++) { const px=x0+i/4*(x1-x0),py=y0+i/4*(y1-y0);ctx.beginPath();ctx.moveTo(px,y0);ctx.lineTo(px,y1);ctx.moveTo(x0,py);ctx.lineTo(x1,py);ctx.stroke();if(i%2===0){ctx.fillStyle=color.text;ctx.textAlign='right';ctx.fillText(String(Math.round(max*(1-i/4))),x0-7,py+4);} }
    ctx.save();ctx.beginPath();ctx.rect(x0,y0,x1-x0,y1-y0);ctx.clip();
    for(const s of data.segments||[]){ctx.fillStyle='#e8c57c0a';ctx.fillRect(x(s.start),y0,x(s.end)-x(s.start),y1-y0);}
    for(const [values,c,dash] of [[a,color.reference,[4,4]],[b,color.comparison,[]]]){
      ctx.beginPath();ctx.strokeStyle=c;ctx.lineWidth=1.7;ctx.setLineDash(dash);let pen=false;
      values.forEach((v,i)=>{if(!finite(v)){pen=false;return;}const px=x(data.progress[i]),py=y(v);if(pen)ctx.lineTo(px,py);else ctx.moveTo(px,py);pen=true;});ctx.stroke();
    }
    ctx.setLineDash([2,3]);ctx.strokeStyle='#b8c8bb88';ctx.beginPath();ctx.moveTo(x(data.progress[index]),y0);ctx.lineTo(x(data.progress[index]),y1);ctx.stroke();ctx.setLineDash([]);
    for(const [v,c] of [[a[index],color.reference],[b[index],color.comparison]])if(finite(v)){ctx.fillStyle=c;ctx.beginPath();ctx.arc(x(data.progress[index]),y(v),2.6,0,Math.PI*2);ctx.fill();}
    drawZoomSelection(ctx,x,y0,y1);
    ctx.restore();
    const readout=find(key+'-value');readout.replaceChildren();
    for(const [label,v,cls] of [['REF',a[index],'orc-ref'],['LAP',b[index],'orc-lap']]){const span=document.createElement('span');span.className=cls;span.textContent=label+' '+format(finite(v)?v*mul:null,key==='speed'?1:0);readout.append(span);}
  }
  function path() {
    const {ctx,w,h}=canvas('path'),xs=data.path.x,ys=data.path.y;mapPoints=[];
    const valid=xs.map((x,i)=>({x,y:ys[i],i})).filter(p=>finite(p.x)&&finite(p.y));
    if(!valid.length){ctx.fillStyle=color.text;ctx.fillText('No position data',10,h/2);return;}
    const xmin=Math.min(...valid.map(p=>p.x)),xmax=Math.max(...valid.map(p=>p.x)),ymin=Math.min(...valid.map(p=>p.y)),ymax=Math.max(...valid.map(p=>p.y));
    const scale=Math.min((w-16)/Math.max(1,xmax-xmin),(h-16)/Math.max(1,ymax-ymin));
    const xy=i=>[w/2+(xs[i]-(xmin+xmax)/2)*scale,h/2-(ys[i]-(ymin+ymax)/2)*scale];
    function line(lo,hi,c,width){ctx.beginPath();ctx.strokeStyle=c;ctx.lineWidth=width;ctx.lineJoin='round';let pen=false;xs.forEach((v,i)=>{if(!finite(v)||!finite(ys[i])||data.progress[i]<lo||data.progress[i]>hi){pen=false;return;}const [x,y]=xy(i);if(pen)ctx.lineTo(x,y);else ctx.moveTo(x,y);pen=true;});ctx.stroke();}
    line(0,1,color.reference,2);
    for(const s of data.segments||[])line(s.start,s.end,color.segment,2.5);
    if(range[0]!==0||range[1]!==1)line(range[0],range[1],color.comparison,3.5);
    valid.forEach(p=>{const [x,y]=xy(p.i);mapPoints.push({x,y,i:p.i});});
    if(finite(xs[index])&&finite(ys[index])){const [x,y]=xy(index);ctx.fillStyle=color.comparison;ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();}
    const p=valid[0],start=xy(p.i);ctx.fillStyle=color.text;ctx.textAlign='left';ctx.fillText('LAP START',Math.max(2,Math.min(w-68,start[0]+8)),Math.max(13,start[1]-9));
  }
  const plotLeft=64,plotRight=10;
  const dragThreshold=6;
  let zoomDrag=null;
  const zoomReset=find('zoom-reset');

  function syncZoomControls() {
    const progress=find('progress');
    progress.min=nearest(range[0]);progress.max=nearest(range[1]);
    zoomReset.disabled=range[0]===0&&range[1]===1;
  }

  function drawZoomSelection(ctx,x,top,bottom) {
    if(!zoomDrag)return;
    const left=x(Math.min(zoomDrag.anchor,zoomDrag.end));
    const width=x(Math.max(zoomDrag.anchor,zoomDrag.end))-left;
    ctx.save();ctx.fillStyle=color.reference;ctx.globalAlpha=.18;
    ctx.fillRect(left,top,width,bottom-top);ctx.restore();
  }

  function bindTraceZoom() {
    function resetZoom() {
      zoomDrag=null;range=[0,1];find('segment').value='all';move(data.progress[index]);
    }
    zoomReset.addEventListener('click',resetZoom);
    for(const role of ['speed','brake','throttle','delta']) {
      const plot=find(role);
      let clickReleases=0;
      function fraction(clientX) {
        const rect=plot.getBoundingClientRect();
        return (clientX-rect.left-plotLeft)/Math.max(1,rect.width-plotLeft-plotRight);
      }
      function at(f) { return range[0]+Math.max(0,Math.min(1,f))*(range[1]-range[0]); }
      function ownsDrag(event) { return zoomDrag?.plot===plot&&zoomDrag.id===event.pointerId; }
      function moved(event) { return Math.hypot(event.clientX-zoomDrag.startX,event.clientY-zoomDrag.startY)>=dragThreshold; }
      plot.addEventListener('pointerdown',event=>{
        if(root.querySelector('.screen').dataset.view!=='main'||zoomDrag||!event.isPrimary||event.button!==0)return;
        const anchor=at(fraction(event.clientX));
        plot.setPointerCapture(event.pointerId);
        zoomDrag={plot,id:event.pointerId,startX:event.clientX,startY:event.clientY,anchor,end:anchor,moved:false};
        move(anchor);
      });
      plot.addEventListener('pointermove',event=>{
        const p=at(fraction(event.clientX));
        if(zoomDrag){if(!ownsDrag(event))return;zoomDrag.end=p;zoomDrag.moved||=moved(event);}
        move(p);
      });
      plot.addEventListener('pointerup',event=>{
        if(!ownsDrag(event))return;
        const {anchor,startX}=zoomDrag,end=at(fraction(event.clientX));
        // Browsers can emit a click (or double-click) after a captured drag.
        clickReleases=zoomDrag.moved||moved(event)?0:Math.min(2,clickReleases+1);
        zoomDrag=null;
        const first=nearest(Math.min(anchor,end)),last=nearest(Math.max(anchor,end));
        if(Math.abs(event.clientX-startX)>=dragThreshold&&last>first){
          range=[data.progress[first],data.progress[last]];
          find('segment').value=first===0&&last===data.progress.length-1?'all':'custom';
          move((range[0]+range[1])/2);
        }else move(end);
      });
      function cancel(event) { if(ownsDrag(event)){clickReleases=0;zoomDrag=null;render();} }
      plot.addEventListener('pointercancel',cancel);
      plot.addEventListener('lostpointercapture',cancel);
      plot.addEventListener('dblclick',()=>{
        if(clickReleases===2&&root.querySelector('.screen').dataset.view==='main')resetZoom();
        clickReleases=0;
      });
    }
  }

  const timingData=JSON.parse(document.getElementById("orc-viewer-timing").textContent);
  function deltaValues(timing,referenceId,comparisonId,progress) {
    const a=timing?.laps.find(l=>l.lap_id===referenceId)?.elapsed_s;
    const b=timing?.laps.find(l=>l.lap_id===comparisonId)?.elapsed_s;
    return progress.map((_,i)=>Number.isFinite(a?.[i])&&Number.isFinite(b?.[i])?b[i]-a[i]:null);
  }
  function timeDelta() {
    const values=deltaValues(timingData,reference.lap_id,selected.lap_id,data.progress);
    const value=values[index],readout=find('delta-value');
    const visible=values.filter((v,i)=>finite(v)&&data.progress[i]>=range[0]&&data.progress[i]<=range[1]);
    find('delta').closest('figure').hidden=!visible.length;
    root.dataset.timeDelta=String(visible.length>0);
    readout.textContent=visible.length?(finite(value)?(value>0?'+':'')+value.toFixed(3)+' s':'GAP'):'';
    if(!visible.length)return;
    const {ctx,w,h}=canvas('delta');
    const bound=Math.max(.1,Math.ceil(Math.max(...visible.map(Math.abs))*10)/10);
    const x0=plotLeft,x1=w-plotRight,y0=10,y1=h-12;
    const x=p=>x0+(p-range[0])/(range[1]-range[0])*(x1-x0);
    const y=v=>y0+(bound-v)/(2*bound)*(y1-y0);
    ctx.strokeStyle=color.grid;ctx.lineWidth=1;
    for(const v of [-bound,0,bound]){
      ctx.beginPath();ctx.moveTo(x0,y(v));ctx.lineTo(x1,y(v));ctx.stroke();
      ctx.fillStyle=color.text;ctx.textAlign='right';ctx.fillText((v>0?'+':'')+v.toFixed(1),x0-7,y(v)+4);
    }
    ctx.save();ctx.beginPath();ctx.rect(x0,y0,x1-x0,y1-y0);ctx.clip();
    ctx.setLineDash([4,4]);ctx.strokeStyle=color.reference;ctx.beginPath();ctx.moveTo(x0,y(0));ctx.lineTo(x1,y(0));ctx.stroke();ctx.setLineDash([]);
    ctx.beginPath();ctx.strokeStyle=color.comparison;ctx.lineWidth=1.7;let pen=false;
    values.forEach((v,i)=>{if(!finite(v)){pen=false;return;}if(pen)ctx.lineTo(x(data.progress[i]),y(v));else ctx.moveTo(x(data.progress[i]),y(v));pen=true;});ctx.stroke();
    ctx.setLineDash([2,3]);ctx.strokeStyle=color.text;ctx.beginPath();ctx.moveTo(x(data.progress[index]),y0);ctx.lineTo(x(data.progress[index]),y1);ctx.stroke();
    if(finite(value)){ctx.fillStyle=color.comparison;ctx.beginPath();ctx.arc(x(data.progress[index]),y(value),2.6,0,Math.PI*2);ctx.fill();}
    drawZoomSelection(ctx,x,y0,y1);ctx.restore();
  }

  function render() {
    syncZoomControls();timeDelta();
    ['speed','brake','throttle'].forEach(trace);path();
    find('lap').value=selected.lap_id;find('progress').value=index;find('progressValue').textContent=format(data.progress[index]*100)+'%';
    find('axis').replaceChildren(...Array.from({length:5},(_,i)=>{const span=document.createElement('span');span.textContent=format((range[0]+i/4*(range[1]-range[0]))*100,range[0]===0&&range[1]===1?0:1)+'%';return span;}));
    root.querySelectorAll('[data-lap-id]').forEach(row=>row.classList.toggle('orc-row-selected',row.dataset.lapId===selected.lap_id));
  }
  function changeLap(id) { selected=comparisons.find(l=>l.lap_id===id)||selected;render();find('selection').textContent=`Comparison Lap ${selected.lap}: ${format(selected.lap_time_s,3)} s.`; }
  function move(p) { index=nearest(Math.max(range[0],Math.min(range[1],p)));render(); }
  find('track').textContent=data.track;find('car').textContent=data.car;find('refLegend').textContent='Reference / Lap '+reference.lap;
  find('lap').replaceChildren(...comparisons.map(l=>{const o=document.createElement('option');o.value=l.lap_id;o.textContent=`Lap ${l.lap} / ${format(l.lap_time_s,3)} s`;return o;}));
  for(const s of data.segments||[]){const o=document.createElement('option');o.value=s.id;o.textContent=s.id;find('segment').append(o);}
  for(const lap of data.laps){
    const row=document.createElement('tr');row.dataset.lapId=lap.lap_id;
    if(!lap.valid)row.className='orc-invalid';
    const n=document.createElement('td'),time=document.createElement('td'),status=document.createElement('td');
    if(lap.valid&&lap!==reference&&lap.signals){const b=document.createElement('button');b.className='cursor-interaction';b.type='button';b.textContent=String(lap.lap).padStart(2,'0');b.setAttribute('aria-label',`Compare lap ${lap.lap}`);b.addEventListener('click',()=>changeLap(lap.lap_id));n.append(b);}else n.textContent=String(lap.lap).padStart(2,'0');
    time.textContent=format(lap.lap_time_s,3);
    status.textContent=lap===reference?'REF':lap.valid?'VALID':'EXCL';status.setAttribute('aria-label',lap.invalid_reason||status.textContent);
    const delta=document.createElement('td'),rounded=Math.round((lap.lap_time_s-reference.lap_time_s)*1000)/1000;
    delta.textContent=lap.valid?(rounded>0?'+':'')+rounded.toFixed(3):'--';delta.className='orc-lap-delta';
    delta.setAttribute('aria-label',lap.valid?'Time delta from Reference Lap: '+delta.textContent+' seconds':'Not comparable: excluded record');
    row.append(n,time,delta,status);find('laps').append(row);
  }
  const timingLabel=reference.lap_time_source==='derived_from_ticks'?'Tick-derived':'Simulator';
  find('timing').textContent=timingLabel+' timing; EXCL durations not comparable.';
  find('result').textContent=data.analysis.status==='consistent'?'CONSISTENT / No data-supported Coaching Instruction.':String(data.analysis.status).toUpperCase();
  find('reason').textContent=data.analysis.status==='reportable'&&data.analysis.coaching_instruction
    ? data.analysis.coaching_instruction
    : data.analysis.reason||'Pinned Analysis Run; no new instruction calculated.';
  find('source').textContent=JSON.stringify({recorded_session_id:data.recorded_session_id,analysis:data.analysis,scope:data.scope_note,display_points:data.progress.length,units:data.units,transformations:data.transformations,limitations:data.limitations,validation_provenance:data.validation_provenance,excluded_laps:data.laps.filter(l=>!l.valid).map(l=>({lap:l.lap,reason:l.invalid_reason}))},null,2);
  find('progress').max=data.progress.length-1;
  find('lap').addEventListener('change',e=>changeLap(e.target.value));
  find('segment').addEventListener('change',e=>{const s=data.segments.find(sg=>sg.id===e.target.value);range=s?[s.start,s.end]:[0,1];move((range[0]+range[1])/2);});
  find('progress').addEventListener('input',e=>move(data.progress[Number(e.target.value)]));
  bindTraceZoom();
  find('path').addEventListener('pointermove',e=>{if(!mapPoints.length)return;const r=e.currentTarget.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;const p=mapPoints.reduce((a,b)=>Math.hypot(a.x-x,a.y-y)<Math.hypot(b.x-x,b.y-y)?a:b);move(data.progress[p.i]);});
  new ResizeObserver(render).observe(root.querySelector('.orc-workspace'));
  const screen=root.querySelector('.screen'), scrollport=document.getElementById('terminalScrollport');
  const terminalForm=document.getElementById('terminalForm'), terminalInput=document.getElementById('terminalInput');
  const terminalOutput=document.getElementById('terminalOutput'), terminalStatus=document.getElementById('terminalStatus');
  const bezelToggle=document.getElementById('bezelToggle'),bezelKey='orc-private-preview-bezel';
  const history=[];let historyIndex=0,fontScale=Number(crtPresets.profiles[crt.profile].fontScale);
  const adjustments={artifact:null,bloom:null,jitter:null,text:null,frame:null};
  const views=['main','report','laps','trace','map','help','source'];
  const defaultView='main';
  const pageText=document.getElementById('terminalPageText'),pageCount=document.getElementById('pageCount');
  const previous=document.getElementById('pagePrevious'),next=document.getElementById('pageNext');
  let pageIndex=0,pageTotal=1;
  function paginate() {
    const view=screen.dataset.view;
    if(!['report','help','source'].includes(view))return;
    const text=view==='help'?root.querySelector('.terminal-help').textContent:view==='source'?find('source').textContent:
      ['PRIVATE RECORDED SESSION / '+data.sim.toUpperCase(),
        data.laps.filter(l=>l.valid).length+' VALID LAPS / '+comparisons.length+' COMPARISON LAPS',
        'REFERENCE LAP '+reference.lap+' / '+format(reference.lap_time_s,3)+' s',
        '',data.track+' / '+data.car,'','COACH REPORT',find('result').textContent,find('reason').textContent,
        '',find('timing').textContent,data.scope_note||'','SOURCE: recording scope and provenance.'].join('\n');
    const style=getComputedStyle(pageText),bounds=pageText.getBoundingClientRect();
    const font=parseFloat(style.fontSize)||17,line=parseFloat(style.lineHeight)||font*1.3;
    const measure=document.createElement('canvas').getContext('2d');
    measure.font=style.font||font+'px "Courier New",monospace';
    const columns=Math.max(8,Math.floor(bounds.width/measure.measureText('M').width)-1);
    const rows=Math.max(1,Math.floor(bounds.height/line)-1),lines=[];
    for(let line of text.split('\n')){
      while(line.length>columns){
        const space=line.lastIndexOf(' ',columns),cut=space>columns/2?space:columns;
        lines.push(line.slice(0,cut));line=line.slice(cut).trimStart();
      }
      lines.push(line);
    }
    pageTotal=Math.max(1,Math.ceil(lines.length/rows));pageIndex=Math.min(pageIndex,pageTotal-1);
    pageText.textContent=lines.slice(pageIndex*rows,(pageIndex+1)*rows).join('\n');
    pageCount.textContent=(pageIndex+1)+' / '+pageTotal;
    previous.disabled=pageIndex===0;next.disabled=pageIndex===pageTotal-1;
  }
  function turnPage(direction){pageIndex=Math.max(0,Math.min(pageTotal-1,pageIndex+direction));paginate();}
  previous.addEventListener('click',()=>turnPage(-1));next.addEventListener('click',()=>turnPage(1));
  const initialView=location.hash.slice(1).toLowerCase();
  screen.dataset.view=views.includes(initialView)?initialView:defaultView;
  function status() {
    terminalStatus.textContent=crt.profile+' / '+crt.raster+' / CHAR '+String(crt.character).padStart(2,'0')+' '+crtPresets.levels[crt.character].label+' / VIEW '+screen.dataset.view.toUpperCase()+' / PRIVATE';
    root.querySelectorAll('[data-view-link]').forEach(link=>link.dataset.active=String(link.dataset.viewLink===screen.dataset.view));
  }
  function metrics() {
    const bounds=root.querySelector('.crt-screen').getBoundingClientRect(),profile=crtPresets.profiles[crt.profile];
    const width=Math.max(4.5,bounds.width/profile.terminalColumns),height=Math.max(10,bounds.height/profile.terminalRows);
    root.style.setProperty('--terminal-cell-width',width.toFixed(3)+'px');root.style.setProperty('--terminal-cell-height',height.toFixed(3)+'px');
    root.style.setProperty('--pixel-cell-width',(width/2).toFixed(3)+'px');root.style.setProperty('--pixel-cell-height',(height/2).toFixed(3)+'px');
    root.style.setProperty('--terminal-content-width',profile.terminalColumns+'ch');
  }
  function terminalCrt() {
    applyCrt();
    for(const kind of ['artifact','bloom','jitter'])if(adjustments[kind]!==null){
      const scale=crtPresets.levels[adjustments[kind]][kind==='artifact'?'artifactScale':kind==='bloom'?'bloomScale':'jitterScale'];
      const keys=crtPresets[kind+'Keys'];
      for(const [key,value] of Object.entries({...crtPresets.profiles[crt.profile],...crtPresets.rasters[crt.raster]}))if(keys.includes(key)){
        const scaled=Math.min(crtPresets.caps[key]??Infinity,parseFloat(value)*scale);
        root.style.setProperty('--'+key.replace(/[A-Z]/g,c=>'-'+c.toLowerCase()),String(scaled)+(kind==='jitter'?'px':''));
      }
    }
    root.dataset.textWarpLevel=String(adjustments.text??crt.character);
    root.dataset.frameWarpLevel=String(adjustments.frame??crt.character);
    root.style.setProperty('--font-scale',fontScale);
    metrics();status();render();paginate();
  }
  function setBezel(enabled) {
    root.dataset.bezel=enabled?'on':'off';
    bezelToggle.setAttribute('aria-pressed',String(enabled));
  }
  function changeBezel(mode) {
    setBezel(mode==='on'||(mode==='toggle'&&root.dataset.bezel==='off'));
    try { window.localStorage.setItem(bezelKey,root.dataset.bezel); } catch {} // Some browsers block storage on local files.
    metrics();render();paginate();
  }
  function showView(view,goTop=true) {
    if(!views.includes(view))return false;
    zoomDrag=null;screen.dataset.view=view;pageIndex=0;status();render();paginate();
    if(goTop)scrollport.scrollTop=0;
    return true;
  }
  function say(message) { terminalOutput.textContent=message;requestAnimationFrame(()=>{render();paginate();}); }
  function setLevel(arg,current) {
    if(arg==='UP')return Math.min(5,current+1);
    if(arg==='DOWN')return Math.max(0,current-1);
    if(!/^[0-5]$/.test(arg||''))return null;
    return Number(arg);
  }
  function runCommand(raw) {
    const parts=raw.trim().split(/\s+/),verb=(parts.shift()||'').toLowerCase(),args=parts.map(v=>v.toUpperCase());
    if(!verb)return;
    if(verb==='next'||verb==='prev'){turnPage(verb==='next'?1:-1);return;}
    if(views.includes(verb)){showView(verb,false);say('');return;}
    if(['cls','clear'].includes(verb)){say('');return;}
    if(verb==='bezel'){
      const mode=(args[0]||'toggle').toLowerCase();
      if(!['on','off','toggle'].includes(mode)){say('BEZEL ON / OFF / TOGGLE');return;}
      say('');changeBezel(mode);return;
    }
    if(['lap','compare'].includes(verb)){
      const lap=comparisons.find(l=>String(l.lap)===args[0]);
      if(!lap){say('CHOOSE A COMPARISON LAP: '+comparisons.map(l=>l.lap).join(' '));return;}
      changeLap(lap.lap_id);showView(screen.dataset.view==='trace'?'trace':'main',false);say('COMPARISON LAP '+lap.lap+' / REFERENCE LAP '+reference.lap);return;
    }
    if(verb==='segment'){
      const s=(data.segments||[]).find(sg=>sg.id===args[0]);
      if(!s&&args[0]!=='ALL'){say('SEGMENT ALL / '+data.segments.map(sg=>sg.id).join(' '));return;}
      find('segment').value=s?s.id:'all';range=s?[s.start,s.end]:[0,1];move((range[0]+range[1])/2);say('SEGMENT '+(s?s.id:'ALL'));return;
    }
    if(verb==='profile'){
      if(!crtPresets.profiles[args[0]]){say('PROFILE AMBER / GREEN / VGA / LCD');return;}
      crt.profile=args[0];fontScale=Number(crtPresets.profiles[crt.profile].fontScale);terminalCrt();say('');return;
    }
    if(verb==='raster'){
      if(!crtPresets.rasters[args[0]]){say('RASTER GRID / SCANLINE / PIXEL / CLEAN');return;}
      crt.raster=args[0];terminalCrt();say('');return;
    }
    if(['character','char','crt'].includes(verb)){
      const arg=verb==='crt'&&args[0]==='CHARACTER'?args[1]:args[0],level=setLevel(arg,crt.character);
      if(level===null){say('CHARACTER 0-5 / UP / DOWN');return;}
      crt.character=level;terminalCrt();say('');return;
    }
    if(verb==='adjust'){
      const key=(args[0]||'').toLowerCase();
      if(key==='reset'){Object.keys(adjustments).forEach(k=>adjustments[k]=null);terminalCrt();say('');return;}
      if(!Object.hasOwn(adjustments,key)){say('ADJUST ARTIFACT / BLOOM / JITTER / TEXT / FRAME / RESET');return;}
      const level=setLevel(args[1],adjustments[key]??crt.character);
      if(level===null){say('ADJUST '+key.toUpperCase()+' 0-5 / UP / DOWN');return;}
      adjustments[key]=level;terminalCrt();say('');return;
    }
    if(verb==='status'){say('PRIVATE / OFFLINE / '+data.laps.filter(l=>l.valid).length+' VALID LAPS\nANALYSIS '+data.analysis.status.toUpperCase()+' / REF LAP '+reference.lap+'\nARTIFACTS READ ONLY / NO LIVE CONNECTION');return;}
    if(verb==='coach'){showView('report',false);say('');return;}
    if(verb==='history'){say(history.slice(-3).join('\n'));return;}
    say('UNKNOWN COMMAND. TYPE HELP.');
  }
  terminalForm.addEventListener('submit',event=>{
    event.preventDefault();const raw=terminalInput.value.trim();if(!raw)return;
    history.push(raw);historyIndex=history.length;terminalInput.value='';runCommand(raw);
    terminalInput.focus({preventScroll:true});
  });
  terminalInput.addEventListener('keydown',event=>{
    if(event.key==='ArrowUp'){event.preventDefault();historyIndex=Math.max(0,historyIndex-1);terminalInput.value=history[historyIndex]||'';}
    if(event.key==='ArrowDown'){event.preventDefault();historyIndex=Math.min(history.length,historyIndex+1);terminalInput.value=history[historyIndex]||'';}
  });
  root.querySelectorAll('[data-view-link]').forEach(link=>link.addEventListener('click',()=>{showView(link.dataset.viewLink);say('');}));
  window.addEventListener('hashchange',()=>showView(location.hash.slice(1)||defaultView));
  window.addEventListener('resize',()=>{metrics();render();paginate();});
  document.addEventListener('keydown',event=>{
    if(!(event.metaKey||event.ctrlKey)||!['+','=','-','0'].includes(event.key))return;
    event.preventDefault();
    fontScale=event.key==='0'?Number(crtPresets.profiles[crt.profile].fontScale):Math.min(1.14,Math.max(.74,fontScale+(event.key==='-'?-.04:.04)));
    root.style.setProperty('--font-scale',fontScale);metrics();render();paginate();
  });
  scrollport.addEventListener('click',event=>{
    if(event.target.closest('a,button,select,input,summary,canvas')||window.getSelection()?.toString())return;
    terminalInput.focus({preventScroll:true});
  });
  let savedBezel;
  try { savedBezel=window.localStorage.getItem(bezelKey); } catch {}
  setBezel(savedBezel==='on');
  bezelToggle.addEventListener('click',()=>changeBezel('toggle'));
  terminalCrt();
  window.orcPreview={runCommand,state:()=>({view:screen.dataset.view,page:pageIndex+1,pages:pageTotal,lap:selected.lap,reference:reference.lap,range:[...range],crt:{...crt},adjustments:{...adjustments}})};

})();
