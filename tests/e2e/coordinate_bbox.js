// Run against the local app: playwright-cli run-code --filename tests/e2e/coordinate_bbox.js
async page => {
  page.setDefaultTimeout(90000);
  const assert=(condition,message)=>{if(!condition) throw new Error(message);};
  await page.getByRole('textbox',{name:'North'}).fill('3.515');
  await page.getByRole('textbox',{name:'West'}).fill('108.61');
  await page.getByRole('textbox',{name:'East'}).fill('132.165');
  await page.getByRole('textbox',{name:'South'}).fill('-12.037');
  await page.getByRole('button',{name:'Terapkan kotak'}).click();
  await page.waitForFunction(()=>document.querySelector('[data-testid="stSidebar"]')?.textContent
    .includes('Seleksi kotak aktif: 108.61° BT'));
  const selected=await page.locator('[data-testid="stSidebar"]').innerText();
  assert(selected.includes('108.61° BT') && selected.includes('132.16'),'Manual longitude selection was not applied');
  assert(await page.getByRole('textbox',{name:'North'}).inputValue()==='3.515','North field changed unexpectedly');
  assert(await page.getByRole('textbox',{name:'East'}).inputValue()==='132.165','East field lost precision');

  await page.getByRole('textbox',{name:'North'}).fill('-20');
  await page.getByRole('button',{name:'Terapkan kotak'}).click();
  await page.getByText('North harus lebih besar daripada South.').waitFor();
  assert((await page.locator('[data-testid="stSidebar"]').innerText()).includes('108.61° BT'),
    'Invalid input replaced the active selection');

  await page.getByRole('button',{name:'Hapus seleksi kotak'}).click();
  await page.waitForFunction(()=>
    !document.querySelector('[data-testid="stSidebar"]')?.textContent.includes('Seleksi kotak aktif:') &&
    document.querySelector('input[aria-label="North"]')?.value==='');
  assert(await page.getByRole('textbox',{name:'North'}).inputValue()==='','Clearing selection did not reset fields');

  const iframe=page.locator('iframe[title="streamlit_folium.st_folium"]');
  await iframe.scrollIntoViewIfNeeded();
  const frame=await iframe.contentFrame();
  await frame.getByRole('link',{name:'Draw a rectangle'}).click();
  const box=await iframe.boundingBox();
  await page.mouse.move(box.x+box.width*.42,box.y+box.height*.36);
  await page.mouse.down();
  await page.mouse.move(box.x+box.width*.55,box.y+box.height*.52,{steps:8});
  await page.mouse.up();
  await page.waitForFunction(()=>document.querySelector('input[aria-label="North"]')?.value &&
    document.querySelector('input[aria-label="West"]')?.value &&
    document.querySelector('input[aria-label="East"]')?.value &&
    document.querySelector('input[aria-label="South"]')?.value);
  const bounds=Object.fromEntries(await Promise.all(['North','West','East','South'].map(async name=>
    [name,Number(await page.getByRole('textbox',{name}).inputValue())])));
  assert(bounds.North>bounds.South && bounds.East>bounds.West,'Drawn box did not update coordinate fields');
  await page.waitForFunction(()=>
    document.querySelector('iframe[title="streamlit_folium.st_folium"]')
      ?.contentWindow?.drawnItems?.toGeoJSON()?.features?.length===0);
  await frame.getByRole('link',{name:'Draw a rectangle'}).click();
  const nextBox=await iframe.boundingBox();
  await page.mouse.move(nextBox.x+nextBox.width*.59,nextBox.y+nextBox.height*.45);
  await page.mouse.down();
  await page.mouse.move(nextBox.x+nextBox.width*.69,nextBox.y+nextBox.height*.61,{steps:8});
  await page.mouse.up();
  await page.waitForFunction(previous=>{
    const west=Number(document.querySelector('input[aria-label="West"]')?.value);
    return Number.isFinite(west) && Math.abs(west-previous)>0.1;
  },bounds.West);
  assert(await frame.getByRole('link',{name:'Edit layers'}).count()===0,
    'Map shows edit controls for the replaced drawing');
  await page.getByRole('button',{name:'Hapus seleksi kotak'}).click();
  return {passed:['manual coordinate selection','invalid box rejected','selection cleared','drawn boxes replace and sync fields']};
}
