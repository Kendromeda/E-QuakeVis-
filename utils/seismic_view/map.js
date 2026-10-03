/* Geographic plan view; distances are calculated by geometry.js, never pixels. */
(function(root) {
  'use strict';
  const bindings = new WeakMap();
  function transform(canvas, bbox) {
    const w = canvas.clientWidth, h = canvas.clientHeight, pad = 27;
    const [west,south,east,north] = bbox;
    const scale = Math.min((w-2*pad)/(east-west), (h-2*pad)/(north-south));
    const x0 = (w-scale*(east-west))/2, y0 = (h-scale*(north-south))/2;
    return {w,h,xy:p => [x0+(p[0]-west)*scale, h-y0-(p[1]-south)*scale],
      geo:(x,y) => [Math.max(west,Math.min(east,west+(x-x0)/scale)),
        Math.max(south,Math.min(north,south+(h-y0-y)/scale))]};
  }
  function line(ctx, coordinates, t, color, width=1, fill=null) {
    ctx.beginPath();
    coordinates.forEach((p,i) => {const [x,y]=t.xy(p); i ? ctx.lineTo(x,y) : ctx.moveTo(x,y);});
    if (fill) {ctx.closePath();ctx.fillStyle=fill;ctx.fill();}
    ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();
  }
  function render(canvas, data, state, onPick) {
    const t = transform(canvas,data.bbox), ratio = Math.min(3,Math.max(2,root.devicePixelRatio || 1));
    canvas.width=Math.round(t.w*ratio);canvas.height=Math.round(t.h*ratio);
    const ctx=canvas.getContext('2d');ctx.scale(canvas.width/t.w,canvas.height/t.h);
    ctx.fillStyle='#edf4f6';ctx.fillRect(0,0,t.w,t.h);
    ctx.font='9px Segoe UI';ctx.fillStyle='#8196a2';
    const [w,s,e,n]=data.bbox;
    for(let i=0;i<5;i++) {
      const lon=w+(e-w)*i/4,lat=s+(n-s)*i/4;
      line(ctx,[[lon,s],[lon,n]],t,'#dce8ed');
      line(ctx,[[w,lat],[e,lat]],t,'#dce8ed');
      const x=t.xy([lon,s])[0],y=t.xy([w,lat])[1];
      ctx.fillText(lon.toFixed(1)+'°',x-12,t.h-5);ctx.fillText(lat.toFixed(1)+'°',2,y-3);
    }
    for(const entry of data.layers.boundaries) line(ctx,entry.path,t,'#9ab1b6',.7,'#dae6e4');
    if(state.showReferences) {
      for(const entry of data.layers.faults) line(ctx,entry.path,t,'#c78e78',.8);
      for(const entry of data.layers.trenches) line(ctx,entry.path,t,'#c3893f',1.7);
    }
    if(state.showEvents) for(const event of data.events) {
      const [x,y]=t.xy([event.lon,event.lat]);ctx.fillStyle=event.color;
      ctx.globalAlpha=.55;ctx.beginPath();ctx.arc(x,y,1.4,0,2*Math.PI);ctx.fill();
    }
    ctx.globalAlpha=1;
    if(state.profile) line(ctx,[...state.profile.left,...state.profile.right.slice().reverse()],t,'#18a1a5',.7,'#0caaa32a');
    const colors=['#007f83','#b25d24','#6657a8','#a02f6f'];
    const lines=state.lines || [{a:state.a,b:state.b}];
    lines.forEach((entry,index)=>{
      if(!entry) return;
      const active=index===(state.activeLine || 0),color=colors[index%colors.length];
      line(ctx,[entry.a,entry.b],t,color,active?3:2);
      [entry.a,entry.b].forEach((p,end)=>{
        const [x,y]=t.xy(p);ctx.beginPath();ctx.arc(x,y,active?5:4,0,2*Math.PI);
        ctx.fillStyle=color;ctx.fill();ctx.strokeStyle='white';ctx.lineWidth=2;ctx.stroke();
        ctx.font='bold 12px Segoe UI';ctx.fillStyle=color;
        ctx.fillText('ABCD'[index]+(end?'′':''),x+7,y-6);
      });
    });
    const selected=data.events.find(event=>event.id===state.selectedId);
    if(selected) {const [x,y]=t.xy([selected.lon,selected.lat]);ctx.beginPath();ctx.arc(x,y,5,0,2*Math.PI);ctx.strokeStyle='#eaa21e';ctx.lineWidth=2;ctx.stroke();}
    bindings.set(canvas,{t,state,onPick});
    if(canvas.dataset.bound) return;
    canvas.dataset.bound='true';let drag=null;
    const position=event=>{const rect=canvas.getBoundingClientRect();return [event.clientX-rect.left,event.clientY-rect.top];};
    canvas.addEventListener('pointerdown',event=>{
      const binding=bindings.get(canvas),[x,y]=position(event);
      if(binding.state.drawing) {binding.onPick(binding.t.geo(x,y));return;}
      const points=[binding.state.a,binding.state.b];
      drag=points.findIndex(p=>p && Math.hypot(...binding.t.xy(p).map((v,i)=>v-[x,y][i]))<14);
      if(drag>=0) canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointerup',event=>{
      if(drag===null || drag<0) return;
      const binding=bindings.get(canvas),[x,y]=position(event);
      binding.onPick(binding.t.geo(x,y),drag);drag=null;
    });
    canvas.addEventListener('pointercancel',()=>{drag=null;});
  }
  root.SeismicMap={render};
})(window);
