const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('profile map uses a sharp backing canvas and draws four distinct slicing lines', () => {
  const strokes = [], labels = [];
  const context = {
    beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {}, fillRect() {},
    arc() {}, scale() {}, stroke() { strokes.push(this.strokeStyle); },
    fillText(value) { labels.push(value); },
  };
  const canvas = {clientWidth: 800, clientHeight: 400, dataset: {},
    getContext: () => context, addEventListener() {}};
  const window = {devicePixelRatio: 1};
  const source = fs.readFileSync(path.resolve(__dirname, '../../utils/seismic_view/map.js'), 'utf8');
  vm.runInNewContext(source, {window});
  const lines = [100, 101, 102, 103].map((lon, index) => ({
    a: [lon, -5], b: [lon, 5], label: String(index + 1),
  }));
  const data = {bbox: [99, -6, 104, 6], layers: {boundaries: [], faults: [], trenches: []}, events: []};
  const state = {a: lines[0].a, b: lines[0].b, lines, activeLine: 0,
    showEvents: false, showReferences: false, selectedId: null};
  window.SeismicMap.render(canvas, data, state, () => {});
  assert.equal(canvas.width, 1600);
  assert.equal(canvas.height, 800);
  assert.ok(new Set(strokes.filter(color => color.startsWith('#') && !['#dce8ed', '#9ab1b6'].includes(color))).size >= 4);
  for (const label of ['A', 'A′', 'B', 'B′', 'C', 'C′', 'D', 'D′']) assert.ok(labels.includes(label));
});

test('profile map omits removed slicing lines', () => {
  const labels=[];
  const context={beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){},fillRect(){},
    arc(){},scale(){},stroke(){},fillText(value){labels.push(value);}};
  const canvas={clientWidth:500,clientHeight:300,dataset:{},getContext:()=>context,addEventListener(){}};
  const window={devicePixelRatio:1};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../utils/seismic_view/map.js'),'utf8'),{window});
  const data={bbox:[99,-6,104,6],layers:{boundaries:[],faults:[],trenches:[]},events:[]};
  window.SeismicMap.render(canvas,data,{lines:[null,{a:[101,-5],b:[101,5]},null,null],
    activeLine:1,a:[101,-5],b:[101,5],showEvents:false,showReferences:false},()=>{});
  assert.ok(labels.includes('B') && labels.includes('B′'));
  for(const label of ['A','A′','C','C′','D','D′']) assert.ok(!labels.includes(label));
});
