// Run against the local app: playwright-cli run-code --filename tests/e2e/profile_map.js
async page => {
  await page.reload();
  await page.getByRole('tab', {name:'Hiposenter 3D',exact:true}).click();
  const iframe=await page.locator('iframe[title="st.iframe"]').elementHandle();
  const frame=await iframe.contentFrame();
  page.setDefaultTimeout(30000);
  await frame.waitForFunction(()=>document.body.dataset.ready==='true');
  const assert=(ok,message)=>{if(!ok) throw new Error(message);};
  const initial=await frame.locator('#profileMap').evaluate(canvas=>({height:canvas.clientHeight,ratio:canvas.width/canvas.clientWidth}));
  assert(initial.height>=300 && initial.ratio>=2,'Map is too small or blurry');
  assert(await frame.locator('.line-tabs button').count()===4,'Expected four profile controls');
  for(const [index,label] of [[1,'B'],[2,'C'],[3,'D'],[0,'A']]) {
    await frame.locator(`.line-tabs button[data-line="${index}"]`).click();
    await frame.waitForFunction(expected=>document.getElementById('sectionTitle').textContent===`Penampang ${expected}–${expected}′` && document.body.dataset.ready==='true',label);
    assert(await frame.locator('#startLabel').innerText()===label,`Wrong active coordinates for ${label}`);
  }
  await frame.locator('.line-tabs button[data-line="1"]').click();
  await frame.waitForFunction(()=>document.body.dataset.ready==='true');
  await frame.locator('#aLon').fill('107');
  await frame.locator('#aLat').fill('-10');
  await frame.locator('#bLon').fill('107');
  await frame.locator('#bLat').fill('-5');
  await frame.getByRole('button',{name:'Terapkan koordinat'}).click();
  await frame.waitForFunction(()=>document.getElementById('profileLength').textContent==='556.0 km' && document.body.dataset.ready==='true');
  await frame.locator('.line-tabs button[data-line="0"]').click();
  await frame.waitForFunction(()=>document.body.dataset.ready==='true');
  await frame.locator('.line-tabs button[data-line="1"]').click();
  await frame.waitForFunction(()=>document.getElementById('profileLength').textContent==='556.0 km' && document.body.dataset.ready==='true');
  await frame.locator('#fullscreenMap').click();
  await frame.waitForFunction(()=>document.fullscreenElement?.id==='profilePanel');
  await frame.waitForFunction(()=>document.getElementById('profileMap').clientHeight>400);
  const expanded=await frame.locator('#profileMap').evaluate(canvas=>({height:canvas.clientHeight,ratio:canvas.width/canvas.clientWidth}));
  assert(expanded.ratio>=2,'Fullscreen map lost sharp resolution');
  await frame.locator('#fullscreenMap').click();
  await frame.waitForFunction(()=>!document.fullscreenElement);
  await frame.locator('#removeProfile').click();
  await frame.waitForFunction(()=>document.querySelector('.line-tabs button[data-line="1"]').textContent.startsWith('+') && document.body.dataset.ready==='true');
  assert(!(await frame.locator('#view3d').evaluate(div=>div.data.some(trace=>trace.name==='B–B′'))),'Removed B line still appears in 3D');
  await frame.locator('.line-tabs button[data-line="1"]').click();
  await frame.waitForFunction(()=>document.getElementById('sectionTitle').textContent==='Penampang B–B′' && document.body.dataset.ready==='true');
  for(const index of [0,1,2,3]) {
    await frame.locator(`.line-tabs button[data-line="${index}"]`).click();
    await frame.waitForFunction(()=>document.body.dataset.ready==='true');
    await frame.locator('#removeProfile').click();
    await frame.waitForFunction(i=>document.querySelector(`.line-tabs button[data-line="${i}"]`).textContent.startsWith('+') && document.body.dataset.ready==='true',index);
  }
  assert(await frame.locator('#sectionTitle').innerText()==='Penampang —','Section should be empty after removing all lines');
  assert(await frame.locator('#drawProfile').isDisabled(),'Drawing must wait for a new line slot');
  await frame.locator('.line-tabs button[data-line="3"]').click();
  await frame.waitForFunction(()=>document.getElementById('sectionTitle').textContent==='Penampang D–D′' && document.body.dataset.ready==='true');
  assert(await frame.locator('#error').isHidden(),'Map interaction produced an error');
  return {passed:['four selectable lines','independent B profile','high resolution canvas','map fullscreen','remove one line','remove all lines','add line again'],initial,expanded};
}
