/* One immutable state snapshot drives map, 3D, section and selected event. */
(function(root) {
  'use strict';
  const $=id=>document.getElementById(id), G=root.SeismicGeometry;
  const defaultA=[DATA.center[0],DATA.bbox[1]],defaultB=[DATA.center[0],DATA.bbox[3]];
  const names=['A','B','C','D'];
  const initialLines=[DATA.center[0],...([.25,.65,.8].map(f=>DATA.bbox[0]+f*(DATA.bbox[2]-DATA.bbox[0])))]
    .map(lon=>({a:[lon,DATA.bbox[1]],b:[lon,DATA.bbox[3]],widthKm:25,modelChoice:'auto'}));
  let state={a:defaultA,b:defaultB,widthKm:25,gridId:DATA.grids[0]?.id || '',modelChoice:'auto',
    showEvents:true,showSlab:true,showReferences:true,opacity:.45,exaggeration:1,
    selectedId:null,profile:null,slice:[],slabProfile:[],drawing:false,pendingA:null,
    lines:initialLines,activeLine:0};
  let busy=false,requested=false;
  const persistenceKey='equakevis-slab2-v1';
  function message(text) { $('error').textContent=text;$('error').hidden=!text; }
  function save() {
    try {sessionStorage.setItem(persistenceKey,JSON.stringify({a:state.a,b:state.b,widthKm:state.widthKm,
      gridId:state.gridId,modelChoice:state.modelChoice,showEvents:state.showEvents,showSlab:state.showSlab,
      showReferences:state.showReferences,opacity:state.opacity,exaggeration:state.exaggeration,
      lines:state.lines,activeLine:state.activeLine}));}
    catch (_) { /* Storage can be disabled in an embedded offline browser. Interaction remains local. */ }
  }
  function restore() {
    try {
      const saved=JSON.parse(sessionStorage.getItem(persistenceKey));
      if(!saved) return;
      const lines=Array.isArray(saved.lines)&&saved.lines.length===4?saved.lines.map(line=>{
        if(line===null) return null;
        G.profileGeometry(line.a,line.b,line.widthKm,2);
        return {a:[...line.a],b:[...line.b],widthKm:line.widthKm,
          modelChoice:DATA.grids.some(g=>g.id===line.modelChoice)?line.modelChoice:'auto'};
      }):[{a:saved.a,b:saved.b,widthKm:saved.widthKm,modelChoice:saved.modelChoice},...initialLines.slice(1)];
      lines.filter(Boolean).forEach(line=>G.profileGeometry(line.a,line.b,line.widthKm,2));
      const activeLine=Number.isInteger(saved.activeLine)&&lines[saved.activeLine]?
        saved.activeLine:lines.findIndex(Boolean);
      const active=lines[activeLine] || null;
      state={...state,a:active?.a || null,b:active?.b || null,widthKm:active?.widthKm || 25,lines,activeLine,
        gridId:active&&DATA.grids.some(g=>g.id===saved.gridId)?saved.gridId:'',
        modelChoice:active?.modelChoice || 'auto',
        ...Object.fromEntries(['showEvents','showSlab','showReferences'].filter(k=>typeof saved[k]==='boolean').map(k=>[k,saved[k]])),
        opacity:Number.isFinite(saved.opacity)?Math.max(.05,Math.min(1,saved.opacity)):.45,
        exaggeration:[1,2,3,5].includes(saved.exaggeration)?saved.exaggeration:1};
    } catch (_) {message('Pengaturan profil tersimpan tidak dapat dipakai. Profil awal dipulihkan.');}
  }
  function sceneData() {
    const points=[...state.lines.filter(Boolean).flatMap(line=>[line.a,line.b]),
      ...(state.profile?[...state.profile.left,...state.profile.right]:[])];
    const bbox=points.reduce((b,p)=>[Math.min(b[0],p[0]),Math.min(b[1],p[1]),Math.max(b[2],p[0]),Math.max(b[3],p[1])],DATA.bbox);
    return {...DATA,bbox};
  }
  function updateNumbers() {
    const grid=DATA.grids.find(g=>g.id===state.gridId);
    $('eventCount').textContent=DATA.events.length.toLocaleString('id-ID')+' hiposenter';
    $('profileLength').textContent=state.profile?state.profile.lengthKm.toFixed(1)+' km':'— km';
    $('sliceCount').textContent=state.activeLine===-1?'— gempa dalam slice':
      state.slice.length.toLocaleString('id-ID')+' gempa dalam slice';
    $('corridorLabel').textContent=state.activeLine===-1?'—':'±'+state.widthKm+' km';
    $('scaleNote').textContent='Depth positif ke bawah · '+state.exaggeration+'×';
    $('opacityValue').textContent=Math.round(state.opacity*100)+'%';
    const valid=state.slabProfile.filter(p=>p.depth!==null).length;
    $('profileStatus').textContent=state.activeLine===-1?'Tambahkan garis untuk melihat penampang.':!grid?'Grid Slab2 belum tersedia.':valid?
      'USGS Slab2 · '+grid.name+' · '+Math.round(valid/state.slabProfile.length*100)+'% sampel profil tercakup':
      'Profil di luar cakupan grid '+grid.name+' / nodata. Ubah garis atau pilih model lain.';
    $('exportSlice').disabled=!state.slice.length;
    $('sectionTitle').textContent=state.activeLine===-1?'Penampang —':
      'Penampang '+names[state.activeLine]+'–'+names[state.activeLine]+'′';
  }
  function updateSource() {
    const grid=DATA.grids.find(g=>g.id===state.gridId),container=$('source');container.replaceChildren();
    container.append(document.createTextNode(DATA.grids.length?
      'Model 3D: USGS Slab2 — '+DATA.grids.map(g=>g.name).join(', ')+' · Penampang: '+(grid?grid.name:'—')+' · ':
      'Tidak ada grid Slab2 yang dimuat. '));
    const link=document.createElement('a');link.href='https://doi.org/10.5066/F7PV6JNV';link.target='_blank';link.rel='noopener noreferrer';link.textContent='Hayes dkk. (2018) · DOI dataset';container.append(link);
    if(grid) container.append(document.createTextNode(' · Grid asli '+Math.abs(grid.lon[1]-grid.lon[0]).toFixed(2)+'° · surface ditipiskan hanya untuk tampilan'));
  }
  function selection() {
    const event=DATA.events.find(e=>e.id===state.selectedId);
    $('selection').textContent=event?
      'EVENT DIPILIH · M '+(event.magnitude===null?'—':event.magnitude.toFixed(1))+' · Depth '+event.depth.toFixed(1)+' km · Longitude '+event.lon.toFixed(4)+'° · Latitude '+event.lat.toFixed(4)+'° · '+event.time+' · '+event.place+
      (state.slice.some(e=>e.id===event.id)?' · Di dalam slice':' · Di luar slice'):
      'Pilih titik gempa di model 3D atau penampang untuk melihat detail yang sama pada kedua panel.';
  }
  async function render() {
    requested=true;if(busy) return;busy=true;document.body.dataset.ready='false';
    try {
      while(requested) {
        requested=false;const snapshot=state,data=sceneData();
        updateNumbers();selection();SeismicMap.render($('profileMap'),data,snapshot,pick);
        await Promise.all([Seismic3D.render($('view3d'),data,snapshot,selectEvent,pick),SeismicSection.render($('section'),data,snapshot,selectEvent)]);
      }
    } catch(error) {message('Visualisasi tidak dapat digambar: '+error.message);}
    finally {busy=false;document.body.dataset.ready='true';}
  }
  function selectEvent(id) {state={...state,selectedId:id};render();}
  function syncInputs() {
    const active=state.activeLine!==-1,name=names[state.activeLine] || '—';
    $('startLabel').textContent=name;$('endLabel').textContent=name+'′';
    document.querySelectorAll('.line-tabs button').forEach((button,index)=>{
      button.classList.toggle('active',index===state.activeLine);
      button.classList.toggle('vacant',!state.lines[index]);
      button.textContent=(state.lines[index]?'':'+ ')+names[index]+'–'+names[index]+'′';
      button.setAttribute('aria-pressed',String(index===state.activeLine));
    });
    ['a','b'].forEach(k=>{ $(k+'Lon').value=active?state[k][0].toFixed(5):'';$(k+'Lat').value=active?state[k][1].toFixed(5):'';});
    ['aLon','aLat','bLon','bLat','width','widthPreset','reverseProfile','removeProfile','drawProfile','draw3d','coordinatesSubmit'].forEach(id=>{$(id).disabled=!active;});
    $('grid').disabled=!active || !DATA.grids.length;
    $('width').value=state.widthKm;
    $('widthPreset').value=[10,25,50].includes(state.widthKm)?String(state.widthKm):'custom';
    $('grid').value=state.modelChoice;
    ['showEvents','showSlab','showReferences'].forEach(k=>{$(k).checked=state[k];});
    $('opacity').value=state.opacity;$('exaggeration').value=state.exaggeration;
    $('drawProfile').classList.toggle('active',state.drawing);
    $('drawProfile').textContent=!active?'Pilih garis':state.drawing?'Batalkan gambar':'Gambar '+name+'–'+name+'′';
    $('draw3d').textContent=!active?'Pilih garis':state.drawing?'Batalkan gambar':'Gambar '+name+'–'+name+'′ di 3D';
    $('draw3d').classList.toggle('active',state.drawing);
    $('draw3dHint').hidden=!state.drawing;
    $('draw3dHint').textContent=state.pendingA?
      name+': '+state.pendingA.join(', ')+'°. Klik titik '+name+'′ pada permukaan 3D.':
      'Klik '+name+' lalu '+name+'′ pada bidang permukaan 3D. Klik fitur diproyeksikan vertikal ke depth 0 km. Seret tetap memutar model.';
  }
  function applyProfile(a,b,widthKm=state.widthKm,modelChoice=state.modelChoice) {
    try {
      const coarse=G.profileGeometry(a,b,widthKm,2);
      const profile=G.profileGeometry(a,b,widthKm,Math.max(201,Math.min(10001,Math.ceil(coarse.lengthKm/2)+1)));
      const slice=G.sliceEvents(DATA.events,a,b,widthKm);
      const candidates=DATA.grids.filter(g=>modelChoice==='auto'||g.id===modelChoice).map(grid=>{
        const values=G.sampleSlabProfile(grid,profile);
        return {grid,values,coverage:values.filter(p=>p.depth!==null).length};
      });
      const chosen=candidates.reduce((best,item)=>!best||item.coverage>best.coverage?item:best,null);
      const gridId=chosen?.grid.id||'',slabProfile=chosen?.values||[];
      const lines=state.lines.map((line,index)=>index===state.activeLine?
        {a:[...a],b:[...b],widthKm,modelChoice}:line);
      state={...state,a:[...a],b:[...b],widthKm,gridId,modelChoice,profile,slice,slabProfile,drawing:false,pendingA:null,lines};
      message('');syncInputs();updateSource();save();render();
      const name=names[state.activeLine];
      $('drawHint').textContent='Seret '+name+' atau '+name+'′ pada peta untuk mengubah profil, atau isi koordinat.';
    } catch(error) {message('Profil tidak valid: '+error.message);syncInputs();}
  }
  function removeActiveLine() {
    if(state.activeLine===-1) return;
    const removed=state.activeLine;
    const lines=state.lines.map((line,index)=>index===removed?null:line);
    const activeLine=lines.findIndex(Boolean);
    state={...state,lines,activeLine,drawing:false,pendingA:null};
    if(activeLine!==-1) {
      const line=lines[activeLine];
      applyProfile(line.a,line.b,line.widthKm,line.modelChoice);
      $('drawHint').textContent=names[removed]+'–'+names[removed]+'′ dihapus. Pilih + untuk menambahkannya kembali.';
      return;
    }
    state={...state,a:null,b:null,profile:null,slice:[],slabProfile:[],gridId:'',selectedId:null};
    message('');syncInputs();updateSource();save();render();
    $('drawHint').textContent='Semua garis dihapus. Pilih + pada salah satu slot untuk menambah garis.';
  }
  function pick(p,index) {
    const point=p.map(v=>Math.round(v*1e5)/1e5);
    if(index===0 || index===1) {applyProfile(index===0?point:state.a,index===1?point:state.b);return;}
    if(!state.drawing) return;
    if(!state.pendingA) {state={...state,pendingA:point};syncInputs();render();$('drawHint').textContent=names[state.activeLine]+': '+point.join(', ')+'°. Sekarang pilih '+names[state.activeLine]+'′.';}
    else applyProfile(state.pendingA,point);
  }
  function exportCsv() {
    const columns=['id','time','magnitude','depth','lon','lat','distanceKm','crossTrackKm'];
    const cell=value=>'"'+String(value??'').replace(/^[=+@-]/,"'").replace(/"/g,'""')+'"';
    const csv=[columns.join(','),...state.slice.map(event=>columns.map(k=>typeof event[k]==='number'?event[k]:cell(event[k])).join(','))].join('\r\n');
    const url=URL.createObjectURL(new Blob(['\uFEFF'+csv],{type:'text/csv;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download='cross-section-'+names[state.activeLine].toLowerCase()+'-slab2.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function bind() {
    document.querySelectorAll('.line-tabs button').forEach((button,index)=>{
      button.onclick=()=>{
        if(index===state.activeLine) return;
        const line=state.lines[index] || initialLines[index];
        state={...state,activeLine:index,drawing:false,pendingA:null};
        applyProfile(line.a,line.b,line.widthKm,line.modelChoice);
      };
    });
    $('removeProfile').onclick=removeActiveLine;
    $('coordinates').addEventListener('submit',event=>{event.preventDefault();applyProfile([+$('aLon').value,+$('aLat').value],[+$('bLon').value,+$('bLat').value]);});
    const toggleDrawing=()=>{state={...state,drawing:!state.drawing,pendingA:null};syncInputs();const name=names[state.activeLine];$('drawHint').textContent=state.drawing?'Klik titik '+name+' pada peta atau 3D, kemudian '+name+'′.':'Gambar dibatalkan. Profil sebelumnya dipertahankan.';render();};
    $('drawProfile').onclick=toggleDrawing;
    $('draw3d').onclick=toggleDrawing;
    $('fullscreen3d').onclick=async()=>{
      try {
        if(document.fullscreenElement) await document.exitFullscreen();
        else await $('modelPanel').requestFullscreen();
      } catch(error) {message('Browser tidak mengizinkan layar penuh: '+error.message);}
    };
    $('fullscreenMap').onclick=async()=>{
      try {
        if(document.fullscreenElement===$('profilePanel')) await document.exitFullscreen();
        else await $('profilePanel').requestFullscreen();
      } catch(error) {message('Browser tidak mengizinkan layar penuh: '+error.message);}
    };
    document.addEventListener('fullscreenchange',()=>{
      const active=document.fullscreenElement===$('modelPanel');
      $('fullscreen3d').textContent=active?'Keluar layar penuh':'Layar penuh';
      $('fullscreen3d').setAttribute('aria-pressed',String(active));
      const mapActive=document.fullscreenElement===$('profilePanel');
      $('fullscreenMap').textContent=mapActive?'Keluar layar penuh':'Layar penuh';
      $('fullscreenMap').setAttribute('aria-pressed',String(mapActive));
      requestAnimationFrame(()=>SeismicMap.render($('profileMap'),sceneData(),state,pick));
      requestAnimationFrame(()=>Plotly.Plots.resize($('view3d')));
    });
    document.addEventListener('keydown',event=>{if(event.key==='Escape' && state.drawing) toggleDrawing();});
    $('reverseProfile').onclick=()=>applyProfile(state.b,state.a);
    $('widthPreset').onchange=()=>{if($('widthPreset').value==='custom') {$('width').focus();return;}applyProfile(state.a,state.b,+$('widthPreset').value);};
    $('width').onchange=()=>applyProfile(state.a,state.b,+$('width').value);
    $('grid').onchange=()=>applyProfile(state.a,state.b,state.widthKm,$('grid').value);
    ['showEvents','showSlab','showReferences'].forEach(k=>{$(k).onchange=()=>{state={...state,[k]:$(k).checked};save();render();};});
    $('opacity').oninput=()=>{state={...state,opacity:+$('opacity').value};save();render();};
    $('exaggeration').onchange=()=>{state={...state,exaggeration:+$('exaggeration').value};save();render();};
    $('resetView').onclick=()=>Plotly.relayout($('view3d'),{'scene.camera':{eye:{x:1.45,y:-1.65,z:1.05},up:{x:0,y:0,z:1},center:{x:0,y:0,z:0}}});
    $('view3d').onpointerdown=()=>$('view3d').focus({preventScroll:true});
    $('view3d').onkeydown=event=>{
      const delta={w:[0,.04],s:[0,-.04],a:[.04,0],d:[-.04,0]}[event.key.toLowerCase()];
      if(!delta) return;event.preventDefault();const camera=$('view3d')._fullLayout.scene.camera;
      Plotly.relayout($('view3d'),{'scene.camera.center':{...camera.center,x:camera.center.x+delta[0],y:camera.center.y+delta[1]}});
    };
    $('exportSlice').onclick=exportCsv;
    let timer;new ResizeObserver(()=>{clearTimeout(timer);timer=setTimeout(()=>{SeismicMap.render($('profileMap'),sceneData(),state,pick);},120);}).observe($('profileMap'));
  }
  $('grid').append(new Option('Otomatis · cakupan profil','auto'));
  for(const grid of DATA.grids) {const option=document.createElement('option');option.value=grid.id;option.textContent=grid.name;$('grid').append(option);}
  if(!DATA.grids.length) {$('grid').append(new Option('Grid tidak tersedia',''));$('grid').disabled=true;$('showSlab').disabled=true;}
  document.querySelectorAll('.mag-legend span').forEach((element,index)=>{const size=SeismicPlots.magnitudeSize({magnitude:[2,4,6][index]});element.querySelector('i').style.width=size+'px';element.querySelector('i').style.height=size+'px';});
  restore();bind();
  if(state.activeLine===-1) {syncInputs();updateSource();render();}
  else applyProfile(state.a,state.b);
})(window);
