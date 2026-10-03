const test = require('node:test');
const assert = require('node:assert/strict');
const plots = require('../../utils/seismic_view/plots.js');
const geometry = {
 project: (lon, lat, cx, cy) => [(lon-cx)*100, (lat-cy)*100],
 inverseProject: (x, y, cx, cy) => [x/100+cx, y/100+cy]
};
const event = {id:'e1',lon:1,lat:1,depth:820,magnitude:6,time:'2026-01-01',place:'<img src=x>',label:'test'};
const grid = {id:'sum',name:'Sumatra',lon:[0,1,2],lat:[0,1,2],depth:[[10,20,30],[20,null,40],[30,40,50]]};
const data = {events:[event],center:[1,1],bbox:[0,0,2,2],grids:[grid],layers:{boundaries:[],trenches:[],faults:[],volcanoes:[]}};
const state = {showEvents:true,showSlab:true,showReferences:true,opacity:.4,exaggeration:1,selectedId:null,gridId:'sum',profile:null,slice:[],slabProfile:[]};

test('3D keeps real slab depths, missing nodes, geographic hover and metric reversed depth', () => {
 const figure=plots.build3D(data,state,geometry);
 const slab=figure.data.find(t=>t.type==='surface');
 assert.deepEqual(slab.z,grid.depth);
 assert.equal(slab.connectgaps,false);
 assert.equal(slab.opacity,.4);
 assert.ok(slab.text[0][0].includes('0.000'));
 assert.ok(figure.layout.scene.zaxis.range[0]>=820);
 assert.equal(figure.layout.scene.zaxis.range[1],0);
 assert.equal(figure.layout.scene.xaxis.title.text,'East (km)');
 assert.ok(figure.layout.scene.aspectratio.z>figure.layout.scene.aspectratio.x);
 assert.equal(figure.layout.uirevision,'seismic-3d');
});
test('events have stable ids, magnitude size, depth encoding and escaped tooltips', () => {
 const trace=plots.build3D(data,state,geometry).data.find(t=>t.name==='Earthquakes');
 assert.deepEqual(trace.customdata,['e1']);
 assert.deepEqual(trace.marker.color,[820]);
 assert.equal(trace.marker.cmax,700);
 assert.deepEqual(trace.marker.colorscale,[[0,'#38bdf8'],[30/700,'#22c55e'],[70/700,'#facc15'],[150/700,'#fb923c'],[300/700,'#ef4444'],[1,'#a855f7']]);
 assert.ok(trace.marker.size[0]>0);
 assert.ok(trace.text[0].includes('&lt;img'));
 assert.ok(!trace.text[0].includes('<img'));
 assert.ok(trace.text[0].includes('2026-01-01'));
});
test('visibility controls remove events and slab without changing metric depth scale', () => {
 const fig=plots.build3D(data,{...state,showSlab:false,showEvents:false},geometry);
 assert.equal(fig.data.filter(t=>t.type==='surface'||t.name==='Earthquakes').length,0);
 assert.ok(fig.layout.scene.zaxis.range[0]>=820);
});
test('section keeps slab gaps and labels the real model even for empty earthquake slice', () => {
 const profile={lengthKm:200,points:[[0,0],[2,2]],distances:[0,200],left:[[0,.1],[2,2.1]],right:[[0,-.1],[2,1.9]]};
 const section=plots.buildSection(data,{...state,profile,slabProfile:[{distanceKm:0,depth:10},{distanceKm:100,depth:null},{distanceKm:200,depth:50}]});
 const slab=section.data.find(t=>t.name.startsWith('USGS Slab2'));
 assert.deepEqual(slab.y,[10,null,50]);
 assert.equal(slab.connectgaps,false);
 assert.ok(section.layout.annotations.some(a=>a.text==='USGS Slab2 — Sumatra'));
 assert.equal(section.layout.paper_bgcolor,'#ffffff');
 assert.equal(slab.line.color,'#0b7c83');
 assert.equal(section.layout.yaxis.range[1],0);
 assert.deepEqual(section.layout.xaxis.range,[0,200]);
});
test('linked slice and selection add highlighted events, curtain, and corridor', () => {
 const profile={lengthKm:200,points:[[0,0],[2,2]],distances:[0,200],left:[[0,.1],[2,2.1]],right:[[0,-.1],[2,1.9]]};
 const linked={...state,profile,slice:[{...event,distanceKm:50}],selectedId:'e1'};
 const figure=plots.build3D(data,linked,geometry);
 assert.ok(figure.data.some(t=>t.name==='Slice earthquakes'&&t.customdata[0]==='e1'));
 assert.ok(figure.data.some(t=>t.name==='Selected earthquake'&&t.customdata[0]==='e1'));
 assert.equal(figure.data.find(t=>t.name==='Selected earthquake').marker.line.color,'#eaa21e');
 assert.ok(figure.data.some(t=>t.name==='Slice corridor'));
 assert.ok(figure.data.some(t=>t.name==='Vertical section plane'));
 const section=plots.buildSection(data,linked);
 assert.deepEqual(section.data.find(t=>t.name==='Earthquakes').x,[50]);
 assert.ok(section.data.some(t=>t.name==='Selected earthquake'));
});
test('four slicing lines remain visible in 3D while the active section uses its own label', () => {
 const profile={lengthKm:100,points:[[0,0],[0,2]],distances:[0,100],left:[[0,0],[0,2]],right:[[0,0],[0,2]]};
 const lines=[0,.5,1,1.5].map(x=>({a:[x,0],b:[x,2]}));
 const active={...state,profile,lines,activeLine:1};
 const model=plots.build3D(data,active,geometry);
 for(const name of ['A–A′','B–B′','C–C′','D–D′']) assert.ok(model.data.some(trace=>trace.name===name));
 const section=plots.buildSection(data,active);
 assert.ok(section.layout.xaxis.title.text.includes('B–B′'));
 assert.ok(section.layout.annotations.some(annotation=>annotation.text==='B′'));
});
test('removed slicing lines disappear from 3D, including when no line remains', () => {
 const profile={lengthKm:100,points:[[1,0],[1,2]],distances:[0,100],left:[[1,0],[1,2]],right:[[1,0],[1,2]]};
 const withOne=plots.build3D(data,{...state,profile,lines:[null,{a:[1,0],b:[1,2]},null,null],activeLine:1},geometry);
 assert.ok(withOne.data.some(trace=>trace.name==='B–B′'));
 for(const label of ['A–A′','C–C′','D–D′']) assert.ok(!withOne.data.some(trace=>trace.name===label));
 const empty=plots.build3D(data,{...state,lines:[null,null,null,null],activeLine:-1},geometry);
 assert.ok(!empty.data.some(trace=>['A–A′','B–B′','C–C′','D–D′'].includes(trace.name)));
});
test('native grid crop avoids adding interpolated geometry and decimation does not fill holes', () => {
 const n=200, lon=Array.from({length:n},(_,i)=>i/100),lat=[...lon];
 const depth=lat.map(()=>lon.map(()=>20)); depth[1][1]=null;
 const dense={...data,grids:[{...grid,lon,lat,depth}]};
 const slab=plots.build3D(dense,state,geometry).data.find(t=>t.type==='surface');
 assert.ok(slab.z.length*slab.z[0].length<=25000);
 assert.ok(slab.z.some(row=>row.includes(null)));
 const cropped=plots.build3D({...data,bbox:[.5,.5,2,2]},state,geometry).data.find(t=>t.type==='surface');
 assert.deepEqual(cropped.z,[[null,40],[40,50]]);
 const outside=plots.build3D({...data,bbox:[10,10,11,11]},state,geometry);
 assert.equal(outside.data.some(t=>t.type==='surface'),false);
});
test('reference layers preserve geographic gaps and escape volcano labels', () => {
 const references={...data,layers:{boundaries:[{path:[[0,0,0],[1,1,0]]}],trenches:[],faults:[],volcanoes:[{lon:1,lat:1,name:'<script>'}]}};
 const fig=plots.build3D(references,state,geometry);
 assert.deepEqual(fig.data.find(t=>t.name==='boundaries').x,[-100,0,null]);
 assert.deepEqual(fig.data.find(t=>t.name==='Volcanoes').text,['&lt;script&gt;']);
});
test('reference lines clip crossing segments and omit fully outside segments before projection', () => {
 const references={...data,layers:{boundaries:[{path:[[-2,1,0],[4,1,0]]},{path:[[8,8,0],[9,9,0]]}],trenches:[],faults:[],volcanoes:[]}};
 const fig=plots.build3D(references,state,geometry);
 assert.deepEqual(fig.data.find(t=>t.name==='boundaries').x,[-100,100,null]);
 assert.deepEqual(fig.data.find(t=>t.name==='boundaries').y,[0,0,null]);
});
test('wrappers keep one click listener across rerenders and dispatch latest callback', async () => {
 const fs=require('node:fs'), vm=require('node:vm');
 for (const [file,key] of [['view3d.js','Seismic3D'],['crosssection.js','SeismicSection']]) {
   let listener, listeners=0, calls=0;
   const div={on:(name,callback)=>{assert.equal(name,'plotly_click');listeners++;listener=callback;}};
   const window={SeismicPlots:plots,SeismicGeometry:geometry,Plotly:{react:async()=>{calls++;}}};
   vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname,'../../utils/seismic_view',file),'utf8'),{window});
   const selected=[];
   await window[key].render(div,data,state,id=>selected.push('old:'+id));
   await window[key].render(div,data,state,id=>selected.push(id));
   listener({points:[{customdata:'e1'}]}); listener({points:[{}]}); listener({});
   assert.equal(calls,2); assert.equal(listeners,1); assert.deepEqual(selected,['e1']);
 }
});
test('build functions do not mutate catalog, slab, or state', () => {
 const before=JSON.stringify({data,state});
 plots.build3D(data,state,geometry); plots.buildSection(data,state);
 assert.equal(JSON.stringify({data,state}),before);
});

test('drawing exposes a bounded zero-depth geographic picking surface and pending A', () => {
 const fig=plots.build3D(data,{...state,drawing:true,pendingA:[.5,1.5]},geometry);
 const plane=fig.data.find(t=>t.meta==='profile-pick');
 assert.ok(plane);
 assert.equal(plane.type,'surface');
 assert.ok(plane.opacity>0 && plane.opacity<.5);
 assert.ok(plane.z.every(row=>row.every(z=>z===0)));
 assert.deepEqual([plane.x[0][0],plane.x[0].at(-1)],fig.layout.scene.xaxis.range);
 assert.deepEqual([plane.y[0][0],plane.y.at(-1)[0]],fig.layout.scene.yaxis.range);
 assert.ok(plane.x[0][1]-plane.x[0][0]<=2);
 assert.ok(plane.text[0][0].includes('A′'));
 assert.ok(plane.text[0][0].includes('0.000'));
 const pending=fig.data.find(t=>t.meta==='profile-pending');
 assert.deepEqual(pending.x,[-50]); assert.deepEqual(pending.y,[50]); assert.deepEqual(pending.z,[0]);
 assert.deepEqual(pending.text,['A']);
 const start=plots.build3D(data,{...state,drawing:true},geometry).data.find(t=>t.meta==='profile-pick');
 assert.ok(start.text[0][0].includes('titik A<'));
 const wide=plots.build3D({...data,bbox:[-10,-10,10,10]},{...state,drawing:true},geometry).data.find(t=>t.meta==='profile-pick');
 assert.ok(wide.z.length<=201 && wide.z[0].length<=201);
 assert.equal(plots.build3D(data,{...state,pendingA:[0,0]},geometry).data.some(t=>t.meta==='profile-pick'||t.meta==='profile-pending'),false);
});

test('3D clicks pick projected surface coordinates only during drawing and use latest bindings', async () => {
 const fs=require('node:fs'),vm=require('node:vm');
 let listener,listeners=0;
 const div={on:(name,callback)=>{listeners++;listener=callback;}};
 const window={SeismicPlots:plots,SeismicGeometry:geometry,Plotly:{react:async()=>{}}};
 vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname,'../../utils/seismic_view/view3d.js'),'utf8'),{window});
 const selected=[],picked=[];
 await window.Seismic3D.render(div,data,{...state,drawing:true},id=>selected.push(id),p=>picked.push(['old',...p]));
 await window.Seismic3D.render(div,{...data,center:[2,3]},{...state,drawing:true},id=>selected.push(id),p=>picked.push([...p]));
 listener({points:[{x:50,y:-50,z:200,customdata:'e1'}]});
 listener({points:[{x:NaN,y:0}]}); listener({points:[{x:0,y:Infinity}]}); listener({points:[{customdata:'e1'}]}); listener({});
 assert.deepEqual(picked,[[2.5,2.5]]); assert.deepEqual(selected,[]);
 await window.Seismic3D.render(div,data,state,id=>selected.push(id),p=>picked.push([...p]));
 listener({points:[{x:0,y:0,customdata:'e1'}]});
 assert.deepEqual(selected,['e1']); assert.equal(picked.length,1); assert.equal(listeners,1);
});

test('3D pointer clicks wait for release, reject orbit drags, and ignore duplicate GL hits', async () => {
 const fs=require('node:fs'),vm=require('node:vm'),handlers={};
 let click;
 const div={on:(name,callback)=>{click=callback;},addEventListener:(name,callback)=>{handlers[name]=callback;}};
 const window={SeismicPlots:plots,SeismicGeometry:geometry,Plotly:{react:async()=>{}}};
 vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname,'../../utils/seismic_view/view3d.js'),'utf8'),{window});
 const picked=[],selected=[];
 await window.Seismic3D.render(div,data,{...state,drawing:true},id=>selected.push(id),p=>picked.push([...p]));
 const pointer=(name,x=10,y=10)=>handlers[name]({clientX:x,clientY:y,pointerId:1,button:0});
 const hit=()=>click({points:[{x:0,y:0,customdata:'e1'}]});
 assert.equal(typeof handlers.pointerdown,'function');
 pointer('pointerdown'); hit(); hit(); assert.equal(picked.length,0);
 pointer('pointerup',13,13); assert.deepEqual(picked,[[1,1]]);
 hit(); assert.equal(picked.length,1);
 pointer('pointerdown'); hit(); pointer('pointermove',30,30); pointer('pointermove'); pointer('pointerup');
 hit(); assert.equal(picked.length,1);
 pointer('pointerdown'); hit(); pointer('pointercancel'); pointer('pointerup'); hit(); assert.equal(picked.length,1);
 pointer('pointerdown'); pointer('pointerup'); hit(); hit(); assert.equal(picked.length,2);
 pointer('pointerdown'); hit(); pointer('pointerup',100,100); assert.equal(picked.length,2);
 pointer('pointerdown'); hit();
 await window.Seismic3D.render(div,data,state,id=>selected.push(id),p=>picked.push([...p]));
 pointer('pointerup'); assert.equal(picked.length,2); assert.equal(selected.length,0);
 pointer('pointerdown'); hit(); pointer('pointermove',40,40); pointer('pointerup',40,40); assert.equal(selected.length,0);
 pointer('pointerdown'); hit(); pointer('pointerup'); hit(); assert.deepEqual(selected,['e1']);
});

test('3D rerender preserves the current camera without sharing Plotly camera objects', async () => {
 const fs=require('node:fs'),vm=require('node:vm');
 const camera={eye:{x:0,y:0,z:2},up:{x:0,y:1,z:0},center:{x:.1,y:0,z:0},projection:{type:'orthographic'}};
 const div={on:()=>{},_fullLayout:{scene:{camera}}};
 let layout;
 const window={SeismicPlots:plots,SeismicGeometry:geometry,Plotly:{react:async(d,traces,value)=>{layout=value;}}};
 vm.runInNewContext(fs.readFileSync(require('node:path').resolve(__dirname,'../../utils/seismic_view/view3d.js'),'utf8'),{window});
 await window.Seismic3D.render(div,data,state,()=>{});
 assert.equal(JSON.stringify(layout.scene.camera),JSON.stringify(camera));
 assert.notEqual(layout.scene.camera,camera); assert.notEqual(layout.scene.camera.eye,camera.eye);
 assert.notEqual(layout.scene.camera.projection,camera.projection);
});
test('3D draws every Slab2 model inside the scene, not only the section model', () => {
 const neighbour={id:'hal',name:'Halmahera',lon:[1,2],lat:[1,2],depth:[[60,80],[80,100]]};
 const far={id:'png',name:'New Guinea',lon:[30,31],lat:[30,31],depth:[[10,20],[20,30]]};
 const scene={...data,grids:[grid,neighbour,far]};
 const names=plots.build3D(scene,state,geometry).data.filter(t=>t.type==='surface').map(t=>t.name);
 assert.deepEqual(names,['USGS Slab2 — Sumatra','USGS Slab2 — Halmahera']);
 const hidden=plots.build3D(scene,{...state,showSlab:false},geometry);
 assert.equal(hidden.data.some(t=>t.type==='surface'),false);
});
