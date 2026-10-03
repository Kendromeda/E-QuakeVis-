// Run against the local app: playwright-cli run-code --filename tests/e2e/seismic_workspace.js
async page => {
  await page.getByRole('tab', {name:'Hiposenter 3D',exact:true}).click();
  const iframe = await page.locator('iframe[title="st.iframe"]').elementHandle();
  const frame = await iframe.contentFrame();
  page.setDefaultTimeout(15000);
  await frame.waitForFunction(() => document.body.dataset.ready === 'true');
  const assert = (condition, text) => { if(!condition) throw new Error(text); };
  assert(await frame.locator('#error').isHidden(), 'Initial renderer error');
  await frame.locator('#widthPreset').selectOption('25');
  await frame.locator('#grid').selectOption('auto');
  await frame.locator('#aLon').fill('107');
  await frame.locator('#aLat').fill('-10');
  await frame.locator('#bLon').fill('107');
  await frame.locator('#bLat').fill('-5');
  await frame.getByRole('button',{name:'Terapkan koordinat'}).click();
  await frame.waitForFunction(()=>document.getElementById('profileLength').textContent==='556.0 km');
  await frame.waitForFunction(()=>document.getElementById('section').data.some(t=>t.name.startsWith('USGS Slab2')&&t.y.some(Number.isFinite)));
  const narrow = await frame.locator('#sliceCount').innerText();
  await frame.locator('#widthPreset').selectOption('50');
  await frame.waitForFunction(()=>document.getElementById('corridorLabel').textContent==='±50 km');
  const wide = await frame.locator('#sliceCount').innerText();
  assert(parseInt(wide)>=parseInt(narrow),'Wider corridor excluded previous events');
  await frame.locator('#showSlab').uncheck();
  await frame.waitForFunction(()=>!document.getElementById('section').data.some(t=>t.name.startsWith('USGS Slab2')));
  await frame.locator('#showSlab').check();
  await frame.waitForFunction(()=>document.getElementById('section').data.some(t=>t.name.startsWith('USGS Slab2')));
  // Click a plotted 2D event using the rendered axes, not a fabricated event dispatch.
  const hit=await frame.locator('#section').evaluate(div=>{
    const trace=div.data.find(t=>t.name==='Earthquakes'&&t.x.length);
    if(!trace) return null;
    const x=div._fullLayout.xaxis,y=div._fullLayout.yaxis,i=Math.floor(trace.x.length/2);
    return {x:x._offset+x.l2p(trace.x[i]),y:y._offset+y.l2p(trace.y[i])};
  });
  assert(hit,'Expected a real catalog event in Java slice');
  await frame.locator('#section').click({position:hit});
  await frame.waitForFunction(()=>document.getElementById('selection').textContent.startsWith('EVENT DIPILIH'));
  await frame.waitForFunction(()=>document.getElementById('view3d').data.some(t=>t.name==='Selected earthquake'));
  await frame.locator('#fullscreen3d').click();
  await frame.waitForFunction(()=>document.fullscreenElement?.id==='modelPanel');
  await frame.locator('#draw3d').click();
  await frame.waitForFunction(()=>document.getElementById('view3d').data.some(t=>t.meta==='profile-pick'));
  // Top camera exposes the picking plane; subsequent input is actual pointer clicks.
  await frame.evaluate(()=>Plotly.relayout(document.getElementById('view3d'),{'scene.camera':{eye:{x:0,y:0,z:2},up:{x:0,y:1,z:0},center:{x:0,y:0,z:0}}}));
  const bounds=await frame.locator('#view3d').boundingBox();
  // GL3D samples button state on animation frames; use a human-duration press.
  await page.mouse.click(bounds.x+bounds.width*.47,bounds.y+bounds.height*.4,{delay:120});
  await frame.waitForFunction(()=>document.getElementById('draw3dHint').textContent.startsWith('A:') && document.body.dataset.ready==='true');
  await page.mouse.click(bounds.x+bounds.width*.52,bounds.y+bounds.height*.6,{delay:120});
  await frame.waitForFunction(()=>document.getElementById('draw3dHint').hidden);
  assert(await frame.locator('#error').isHidden(),'3D picked profile failed');
  await frame.locator('#fullscreen3d').click();
  await frame.waitForFunction(()=>!document.fullscreenElement);
  // A Papua profile must choose actual New Guinea coverage, not the first grid.
  await frame.locator('#aLon').fill('138');await frame.locator('#aLat').fill('-5');
  await frame.locator('#bLon').fill('138');await frame.locator('#bLat').fill('0');
  await frame.getByRole('button',{name:'Terapkan koordinat'}).click();
  await frame.waitForFunction(()=>document.getElementById('source').textContent.includes('New Guinea'));
  await frame.locator('#aLon').fill('107');await frame.locator('#aLat').fill('-10');
  await frame.locator('#bLon').fill('107');await frame.locator('#bLat').fill('-5');
  await frame.getByRole('button',{name:'Terapkan koordinat'}).click();
  await frame.waitForFunction(()=>document.getElementById('profileLength').textContent==='556.0 km');
  await frame.locator('#section').screenshot({path:'output/playwright/slab2-section.png'});
  return {passed:['real Slab2 curve','corridor width','layer visibility','real point selection sync','native fullscreen','two 3D pointer picks','exit fullscreen','automatic Papua coverage'],narrow,wide};
}
