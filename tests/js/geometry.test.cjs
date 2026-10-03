const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const G = require('../../utils/seismic_view/geometry.js');
const R = 6371.0088;
const near = (a, b, tolerance = 1e-7) => assert.ok(Math.abs(a-b) < tolerance, `${a} != ${b}`);

test('projection preserves radial geodesic distances and round trips near dateline and poles', () => {
  near(G.project(1, 0, 0, 0)[0], R*Math.PI/180);
  assert.deepEqual(G.project(30, 60, 30, 60), [0, 0]);
  assert.deepEqual(G.inverseProject(0, 0, 30, 60), [30, 60]);
  for (const [lon,lat,cLon,cLat] of [[-179,30,179,30],[65,85,40,80],[110,-8,105,-5]]) {
    const p = G.project(lon,lat,cLon,cLat);
    const actual = G.inverseProject(...p,cLon,cLat);
    near(actual[0],lon); near(actual[1],lat);
  }
});

test('equatorial profile uses equally spaced distances and geographic half-width', () => {
  const p = G.profileGeometry([0,0],[2,0],25,3);
  near(p.lengthKm, R*Math.PI/90);
  near(p.points[1][0],1); near(p.points[1][1],0);
  near(p.distances[1],p.lengthKm/2);
  near(p.left[1][1],25/R*180/Math.PI);
  near(p.right[1][1],-25/R*180/Math.PI);
  assert.equal(G.profileGeometry([0,0],[1,0],10).points.length,201);
});

test('great circle crosses antimeridian by shortest path and curves poleward at high latitude', () => {
  const p = G.profileGeometry([179,10],[-179,10],10,3);
  near(Math.abs(p.points[1][0]),180);
  assert.ok(p.lengthKm < 220);
  const high = G.profileGeometry([-45,80],[45,80],10,3);
  assert.ok(high.points[1][1] > 82);
  const reversed = G.profileGeometry([45,80],[-45,80],10,3);
  for (let i=0;i<3;i++) {
    near(high.points[i][0], reversed.points[2-i][0]);
    near(high.left[i][1], reversed.right[2-i][1]);
  }
});

test('slice includes corridor boundaries and endpoints, excludes events beyond finite segment', () => {
  const edgeLat = 25/R*180/Math.PI;
  const events = [
    {id:'center',lon:1,lat:0,depth:40}, {id:'left',lon:1,lat:edgeLat,depth:80},
    {id:'right',lon:1,lat:-edgeLat,depth:90}, {id:'outside',lon:1,lat:edgeLat+.001,depth:60},
    {id:'before',lon:-.001,lat:0,depth:2}, {id:'after',lon:2.001,lat:0,depth:3},
    {id:'A',lon:0,lat:0,depth:4}, {id:'B',lon:2,lat:0,depth:5},
    {id:'invalid',lon:NaN,lat:0,depth:3}, {id:'missingDepth',lon:1,lat:0,depth:null}
  ];
  const snapshot = JSON.stringify(events);
  const result = G.sliceEvents(events,[0,0],[2,0],25);
  assert.deepEqual(result.map(e=>e.id),['center','left','right','A','B']);
  near(result[0].distanceKm,R*Math.PI/180);
  near(result[1].crossTrackKm,25);
  near(result[2].crossTrackKm,-25);
  assert.equal(JSON.stringify(events),snapshot);
  assert.notEqual(result[0],events[0]);
});

test('slice handles dateline and reversal consistently', () => {
  const events = [{id:'x',lon:180,lat:10.05,depth:50}];
  const f = G.sliceEvents(events,[179,10],[-179,10],25)[0];
  const r = G.sliceEvents(events,[-179,10],[179,10],25)[0];
  assert.ok(f); assert.ok(r);
  near(f.crossTrackKm,-r.crossTrackKm);
  near(f.distanceKm+r.distanceKm,G.profileGeometry([179,10],[-179,10],25).lengthKm);
});

test('bilinear depth reproduces planar surface and never extrapolates', () => {
  const grid = {lon:[100,101,102],lat:[-2,-1,0],depth:[[10,20,30],[30,40,50],[50,60,70]]};
  near(G.bilinearDepth(grid,100.25,-1.5),22.5);
  near(G.bilinearDepth(grid,102,0),70);
  assert.equal(G.bilinearDepth(grid,99.99,-1),null);
  assert.equal(G.bilinearDepth(grid,101,.01),null);
  const descending = {lon:[102,101,100],lat:[0,-1,-2],depth:[[70,60,50],[50,40,30],[30,20,10]]};
  near(G.bilinearDepth(descending,100.25,-1.5),22.5);
});

test('nodata never bridges a contributing corner but exact nodes and edges remain usable', () => {
  const grid = {lon:[0,1],lat:[0,1],depth:[[10,20],[30,null]]};
  assert.equal(G.bilinearDepth(grid,.5,.5),null);
  near(G.bilinearDepth(grid,0,0),10);
  near(G.bilinearDepth(grid,0,.5),20);
  near(G.bilinearDepth(grid,.5,0),15);
  assert.equal(G.bilinearDepth(grid,1,1),null);
});

test('grid dateline coordinates interpolate without crossing globe', () => {
  const wrapped = {lon:[179,-180,-179],lat:[0,1],depth:[[10,20,30],[20,30,40]]};
  near(G.bilinearDepth(wrapped,-179.5,.5),30);
  const positive = {lon:[179,180,181],lat:[0,1],depth:[[10,20,30],[20,30,40]]};
  near(G.bilinearDepth(positive,-179.5,.5),30);
});

test('Slab profile preserves null gaps and distance-coordinate identity', () => {
  const grid = {lon:[0,1,2,3],lat:[0,1],depth:[[10,null,30,40],[20,null,40,50]]};
  const p = G.profileGeometry([0,0],[3,0],10,7);
  const result = G.sampleSlabProfile(grid,p);
  assert.deepEqual(result.map(x=>x.depth),[10,null,null,null,30,35,40]);
  near(result[6].distanceKm,p.lengthKm);
  near(result[6].lon,3); near(result[6].lat,0);
});

test('invalid or ambiguous geometry is rejected at boundaries', () => {
  for (const args of [[[0,0],[0,0],25], [[0,0],[180,0],25], [[0,0],[100,0],25],
    [[0,91],[1,0],25],[[181,0],[1,0],25],[[0,0],[1,0],0],[[0,0],[1,0],501],
    [[0,0],[1,0],25,1], [[0,0],[1,0],25,2.5]]) assert.throws(()=>G.profileGeometry(...args));
  assert.throws(()=>G.project(NaN,0,0,0));
  assert.throws(()=>G.project(180,0,0,0));
  assert.throws(()=>G.inverseProject(Infinity,0,0,0));
  assert.throws(()=>G.inverseProject(Math.PI*R,0,0,0));
  assert.throws(()=>G.sliceEvents(null,[0,0],[1,0],25));
  assert.throws(()=>G.bilinearDepth({lon:[0,1,0],lat:[0,1],depth:[]},0,0));
  assert.throws(()=>G.bilinearDepth({lon:[0],lat:[0,1],depth:[[1],[2]]},0,0));
});

test('browser UMD export exposes the same functions without Node', () => {
  const context = {window:{}};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../utils/seismic_view/geometry.js'),'utf8'),context);
  assert.equal(typeof context.window.SeismicGeometry.profileGeometry,'function');
});
