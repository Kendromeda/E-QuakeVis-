/* Pure Plotly figure builders. Depth is positive down; horizontal units are km. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.SeismicPlots = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DEPTH_COLORS = [[0, '#38bdf8'], [30/700, '#22c55e'], [70/700, '#facc15'], [150/700, '#fb923c'], [300/700, '#ef4444'], [1, '#a855f7']];
  const BASE = {paper_bgcolor: '#101d2a', plot_bgcolor: '#101d2a', font: {family: 'Arial, sans-serif', color: '#cbd9e5', size: 11}, showlegend: false};
  const CONFIG = {responsive: true, displaylogo: false, scrollZoom: true, modeBarButtonsToRemove: ['toImage', 'select2d', 'lasso2d']};
  const escape = value => String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const size = event => Math.max(3, Math.min(19, 2 + Math.pow(Math.max(0, Number(event.magnitude) || 0), 1.45)));
  const unwrap = row => row.event ? {...row.event, distanceKm: row.distanceKm} : row;
  const gridFor = (data, state) => (data.grids || []).find(g => g.id === state.gridId);
  const point = p => Array.isArray(p) ? [p[0], p[1]] : [p.lon, p.lat];
  function project(geometry, center, p) {
    const xy = geometry.project(...point(p), ...center);
    return Array.isArray(xy) ? xy : [xy.x, xy.y];
  }
  function depthLimit(data) {
    let max = 50;
    for (const event of data.events || []) if (Number.isFinite(event.depth)) max = Math.max(max, event.depth);
    for (const grid of data.grids || []) for (const row of grid.depth) for (const depth of row) {
      if (Number.isFinite(depth)) max = Math.max(max, depth);
    }
    return Math.ceil(max * 1.05 / 10) * 10;
  }
  function hover(event) {
    const mag = Number.isFinite(event.magnitude) ? event.magnitude.toFixed(1) : '—';
    return '<b>M ' + mag + '</b> · ' + event.depth.toFixed(1) + ' km depth<br>' +
      'Longitude ' + event.lon.toFixed(3) + '° · Latitude ' + event.lat.toFixed(3) + '°<br>' +
      escape(event.time) + '<br>' + escape(event.place || event.label || event.id);
  }
  function eventTrace(events, name, dimension, geometry, center, maxDepth, opacity) {
    const coords = dimension === 3 ? events.map(e => project(geometry, center, e)) : null;
    return {
      type: dimension === 3 ? 'scatter3d' : 'scattergl', name, mode: 'markers',
      x: events.map((e, i) => coords ? coords[i][0] : e.distanceKm),
      y: events.map((e, i) => coords ? coords[i][1] : e.depth),
      ...(coords ? {z: events.map(e => e.depth)} : {}),
      customdata: events.map(e => e.id), ids: events.map(e => String(e.id)),
      text: events.map(hover), hovertemplate: '%{text}<extra></extra>',
      marker: {size: events.map(size), color: events.map(e => e.depth), colorscale: DEPTH_COLORS,
        cmin: 0, cmax: 700, showscale: false, opacity, line: {width: .35, color: '#172637'}},
      showlegend: false
    };
  }
  function selectedTrace(events, state, dimension, geometry, center, maxDepth) {
    const selected = events.filter(e => e.id === state.selectedId);
    if (!selected.length) return [];
    const trace = eventTrace(selected, 'Selected earthquake', dimension, geometry, center, maxDepth, 1);
    return [{...trace, marker: {...trace.marker, size: selected.map(e => size(e) + 6),
      color: '#eaa21e', symbol: 'circle-open', line: {width: 3, color: '#eaa21e'}}}];
  }
  function nativeIndices(values, low, high, stride) {
    const all = values.map((v, i) => v >= low && v <= high ? i : -1).filter(i => i >= 0);
    if (!all.length) return [];
    const sampled = all.filter((_, i) => i % stride === 0);
    return sampled[sampled.length - 1] === all[all.length - 1] ? sampled : [...sampled, all[all.length - 1]];
  }
  // Null a coarse vertex if its adjacent native cells contain missing data.
  // This deliberately erodes edges instead of spanning an unobserved slab hole.
  function safeDepth(grid, row, column, stride) {
    const value = grid.depth[row][column];
    if (!Number.isFinite(value) || stride === 1) return Number.isFinite(value) ? value : null;
    for (let r = Math.max(0, row - stride); r <= Math.min(grid.lat.length - 1, row + stride); r++) {
      for (let c = Math.max(0, column - stride); c <= Math.min(grid.lon.length - 1, column + stride); c++) {
        if (!Number.isFinite(grid.depth[r][c])) return null;
      }
    }
    return value;
  }
  function slabSurface(grid, data, state, geometry) {
    const [west, south, east, north] = data.bbox;
    const rows = nativeIndices(grid.lat, south, north, 1).length;
    const cols = nativeIndices(grid.lon, west, east, 1).length;
    let stride = Math.max(1, Math.ceil(Math.sqrt(rows * cols / 24000)));
    while ((Math.ceil(rows / stride) + 1) * (Math.ceil(cols / stride) + 1) > 25000) stride++;
    const ri = nativeIndices(grid.lat, south, north, stride), ci = nativeIndices(grid.lon, west, east, stride);
    if (ri.length < 2 || ci.length < 2) return [];
    const xy = ri.map(r => ci.map(c => project(geometry, data.center, [grid.lon[c], grid.lat[r]])));
    return [{type: 'surface', name: 'USGS Slab2 — ' + escape(grid.name),
      x: xy.map(row => row.map(p => p[0])), y: xy.map(row => row.map(p => p[1])),
      z: ri.map(r => ci.map(c => safeDepth(grid, r, c, stride))),
      text: ri.map(r => ci.map(c => 'USGS Slab2 — ' + escape(grid.name) + '<br>Longitude ' +
        grid.lon[c].toFixed(3) + '° · Latitude ' + grid.lat[r].toFixed(3) + '°')),
      hovertemplate: '%{text}<br>Depth %{z:.1f} km<extra></extra>',
      colorscale: [[0, '#9de1d5'], [1, '#286e85']], showscale: false,
      opacity: Math.max(0, Math.min(1, Number(state.opacity))), connectgaps: false,
      contours: {z: {show: true, start: 0, end: 800, size: 100, color: '#588b94', width: 1}},
      lighting: {ambient: .8, diffuse: .7, roughness: .9, specular: .08}, showlegend: false}];
  }
  // Clip reference polylines only; slab nodes and earthquake coordinates stay native.
  function clipSegment(a, b, bbox) {
    if (!a || !b) return [];
    const [west, south, east, north] = bbox, dx = b[0]-a[0], dy = b[1]-a[1];
    const edges = [[-dx,a[0]-west],[dx,east-a[0]],[-dy,a[1]-south],[dy,north-a[1]]];
    let start = 0, end = 1;
    for (const [p, q] of edges) {
      if (p === 0) { if (q < 0) return []; }
      else if (p < 0) start = Math.max(start, q/p);
      else end = Math.min(end, q/p);
      if (start > end) return [];
    }
    return [start, end].map(t => [a[0]+t*dx, a[1]+t*dy, (a[2] || 0)+t*((b[2] || 0)-(a[2] || 0))]);
  }
  function referenceLines(data, geometry) {
    const layers = data.layers || {};
    const volcanoes = (layers.volcanoes || []).filter(v => v.lon >= data.bbox[0] && v.lon <= data.bbox[2] && v.lat >= data.bbox[1] && v.lat <= data.bbox[3]);
    return [['boundaries', '#63778a', 2], ['faults', '#d89975', 2], ['trenches', '#ffbd66', 4]].map(([key, color, width]) => {
      const paths = (layers[key] || []).flatMap(line => (line.path || []).slice(1).flatMap((p, i) => {
        const segment = clipSegment(line.path[i], p, data.bbox);
        return segment.length ? [...segment, null] : [];
      }));
      const xy = paths.map(p => p ? project(geometry, data.center, p) : null);
      return {type: 'scatter3d', name: key, mode: 'lines', x: xy.map(p => p ? p[0] : null),
        y: xy.map(p => p ? p[1] : null), z: paths.map(p => p ? (p[2] || 0) : null),
        line: {color, width}, hoverinfo: 'skip', connectgaps: false, showlegend: false};
    }).concat(volcanoes.length ? [{type: 'scatter3d', name: 'Volcanoes', mode: 'markers',
      x: volcanoes.map(v => project(geometry, data.center, v)[0]),
      y: volcanoes.map(v => project(geometry, data.center, v)[1]),
      z: volcanoes.map(() => 0), marker: {size: 4, color: '#dc916b', symbol: 'diamond'},
      text: volcanoes.map(v => escape(v.name)), hovertemplate: '%{text}<extra></extra>'}] : []);
  }
  function profileTraces(data, state, geometry, maxDepth) {
    if (!state.profile || !state.profile.points.length) return [];
    const p = state.profile, xy = p.points.map(v => project(geometry, data.center, v));
    const xs = xy.map(v => v[0]), ys = xy.map(v => v[1]), zero = xy.map(() => 0);
    const label='ABCD'[state.activeLine || 0] || 'A';
    const traces = [{type: 'scatter3d', name: label+'–'+label+'′', mode: 'lines+text', x: xs, y: ys, z: zero,
      text: xy.map((_, i) => i === 0 ? label : i === xy.length - 1 ? label+'′' : ''),
      textfont: {size: 16, color: '#ffffff'}, textposition: 'top center',
      line: {color: '#f7ffff', width: 6}, hoverinfo: 'skip'},
      {type: 'surface', name: 'Vertical section plane', x: [xs, xs], y: [ys, ys],
        z: [zero, xy.map(() => maxDepth)], colorscale: [[0, '#c4f9f0'], [1, '#c4f9f0']],
        opacity: .07, showscale: false, hoverinfo: 'skip', connectgaps: false}];
    if (p.left && p.right && p.left.length && p.right.length) {
      const edges = [p.left, p.right].map(edge => edge.map(v => project(geometry, data.center, v)));
      return [...traces, {type: 'surface', name: 'Slice corridor', x: edges.map(e => e.map(v => v[0])),
        y: edges.map(e => e.map(v => v[1])), z: edges.map(e => e.map(() => 0)),
        colorscale: [[0, '#9cecdf'], [1, '#9cecdf']], opacity: .2, showscale: false, hoverinfo: 'skip'}];
    }
    return traces;
  }
  function otherProfileTraces(data, state, geometry) {
    const colors=['#007f83','#b25d24','#6657a8','#a02f6f'];
    return (state.lines || []).flatMap((line,index)=>{
      if(!line || index===state.activeLine) return [];
      const a=project(geometry,data.center,line.a),b=project(geometry,data.center,line.b);
      const label='ABCD'[index];
      return [{type:'scatter3d',name:label+'–'+label+'′',mode:'lines+text',
        x:[a[0],b[0]],y:[a[1],b[1]],z:[0,0],text:[label,label+'′'],
        textposition:'top center',textfont:{size:13,color:colors[index]},
        line:{color:colors[index],width:4},hoverinfo:'skip',showlegend:false}];
    });
  }
  function scene(data, state, geometry, maxDepth) {
    const [w, s, e, n] = data.bbox;
    const xy = [[w,s], [w,n], [e,s], [e,n], [(w+e)/2,s], [(w+e)/2,n]].map(p => project(geometry, data.center, p));
    const xr = [Math.min(...xy.map(p => p[0])), Math.max(...xy.map(p => p[0]))];
    const yr = [Math.min(...xy.map(p => p[1])), Math.max(...xy.map(p => p[1]))];
    const scale = Math.max(xr[1] - xr[0], yr[1] - yr[0], 1);
    const axis = {gridcolor: '#304355', zerolinecolor: '#526577', showbackground: true, backgroundcolor: '#111f2d', ticksuffix: ' km'};
    return {xaxis: {...axis, title: {text: 'East (km)'}, range: xr},
      yaxis: {...axis, title: {text: 'North (km)'}, range: yr},
      zaxis: {...axis, title: {text: 'Depth (km)'}, range: [maxDepth, 0]},
      aspectmode: 'manual', aspectratio: {x: Math.max(.01, (xr[1]-xr[0])/scale),
        y: Math.max(.01, (yr[1]-yr[0])/scale), z: maxDepth / scale * (Number(state.exaggeration) || 1)},
      camera: {eye: {x: 1.45, y: -1.65, z: 1.05}, up: {x: 0, y: 0, z: 1}},
      dragmode: 'orbit', uirevision: 'seismic-3d'};
  }
  // This is an interaction plane at sea level, never a synthetic slab/topography.
  function pickingTraces(data, state, geometry, sceneLayout) {
    if (!state.drawing) return [];
    const axisSamples = range => {
      const count = Math.max(2, Math.min(201, Math.ceil((range[1] - range[0]) / 2) + 1));
      return Array.from({length: count}, (_, i) => range[0] + (range[1] - range[0]) * i / (count - 1));
    };
    const xs = axisSamples(sceneLayout.xaxis.range), ys = axisSamples(sceneLayout.yaxis.range);
    const label='ABCD'[state.activeLine || 0] || 'A';
    const target = state.pendingA ? label+'′' : label;
    const plane = {type: 'surface', name: 'Pilih penampang pada depth 0 km', meta: 'profile-pick',
      x: ys.map(() => [...xs]), y: ys.map(y => xs.map(() => y)), z: ys.map(() => xs.map(() => 0)),
      text: ys.map(y => xs.map(x => {
        const ll = geometry.inverseProject(x, y, ...data.center);
        return 'Klik untuk titik ' + target + '<br>Longitude ' + ll[0].toFixed(3) +
          '° · Latitude ' + ll[1].toFixed(3) + '°<br>Diproyeksikan ke depth 0 km';
      })), hovertemplate: '%{text}<extra></extra>',
      colorscale: [[0, '#73c8dc'], [1, '#73c8dc']], opacity: .16, showscale: false,
      showlegend: false, lighting: {ambient: 1, diffuse: 0, specular: 0}};
    if (!state.pendingA) return [plane];
    const a = project(geometry, data.center, state.pendingA);
    return [plane, {type: 'scatter3d', name: 'Titik '+label, meta: 'profile-pending', mode: 'markers+text',
      x: [a[0]], y: [a[1]], z: [0], text: [label], textposition: 'top center',
      textfont: {size: 18, color: '#ffffff'}, marker: {size: 8, color: '#f7ffff', symbol: 'diamond'},
      hovertemplate: 'Titik '+label+' · pilih '+label+'′ untuk menyelesaikan penampang<extra></extra>', showlegend: false}];
  }
  function build3D(data, state, geometry) {
    const maxDepth = depthLimit(data), events = data.events || [];
    const slice = (state.slice || []).map(unwrap), ids = new Set(slice.map(e => e.id));
    const context = state.profile ? events.filter(e => !ids.has(e.id)) : events;
    const sceneLayout = scene(data, state, geometry, maxDepth);
    // Every Slab2 model inside the scene is drawn together, so neighbouring
    // subduction zones (e.g. Sunda and Molucca Sea) appear side by side. The
    // model selector only drives the A–A′ section profile.
    const slabs = state.showSlab ? (data.grids || []).flatMap(grid => slabSurface(grid, data, state, geometry)) : [];
    return {data: [
      ...slabs,
      ...(state.showReferences ? referenceLines(data, geometry) : []),
      ...(state.showEvents ? [eventTrace(context, 'Earthquakes', 3, geometry, data.center, maxDepth, state.profile ? .12 : .85)] : []),
      ...(state.showEvents && state.profile ? [eventTrace(slice, 'Slice earthquakes', 3, geometry, data.center, maxDepth, 1)] : []),
      ...profileTraces(data, state, geometry, maxDepth),
      ...otherProfileTraces(data, state, geometry),
      ...(state.showEvents ? selectedTrace(events, state, 3, geometry, data.center, maxDepth) : []),
      ...pickingTraces(data, state, geometry, sceneLayout)
    ], layout: {...BASE, margin: {l: 0, r: 0, b: 0, t: 0}, uirevision: 'seismic-3d',
      scene: sceneLayout}, config: {...CONFIG}};
  }
  function sectionSlab(data, state) {
    const grid = gridFor(data, state), profile = state.slabProfile || [];
    if (!state.showSlab || !grid || !profile.length) return {traces: [], annotations: []};
    const name = 'USGS Slab2 — ' + escape(grid.name), finite = profile.filter(p => p && Number.isFinite(p.depth));
    const label = finite[Math.floor(finite.length * .6)];
    return {traces: [{type: 'scatter', name, mode: 'lines', x: profile.map(p => p ? p.distanceKm : null),
      y: profile.map(p => p && Number.isFinite(p.depth) ? p.depth : null), connectgaps: false,
      line: {color: '#0b7c83', width: 3}, hovertemplate: name + '<br>%{x:.1f} km along profile<br>Depth %{y:.1f} km<extra></extra>'}],
      annotations: label ? [{x: label.distanceKm, y: label.depth, text: name, showarrow: true,
        arrowhead: 0, arrowcolor: '#0b7c83', ax: 0, ay: -28, font: {color: '#0b7c83', size: 12}, bgcolor: '#ffffff', borderpad: 3}] : []};
  }
  function buildSection(data, state) {
    const maxDepth = depthLimit(data), events = (state.slice || []).map(unwrap), slab = sectionSlab(data, state);
    const length = state.profile ? state.profile.lengthKm : 100;
    const label='ABCD'[state.activeLine || 0] || 'A';
    const prompt = state.profile ? 'Tidak ada gempa di dalam koridor ini' :
      state.activeLine===-1 ? 'Tambahkan garis slicing untuk membuat penampang' :
      'Pilih dua titik '+label+' → '+label+'′ pada peta untuk membuat penampang';
    const empty = !state.profile || !events.length ? [{xref: 'paper', yref: 'paper', x: .5, y: .93,
      text: prompt, showarrow: false, font: {color: '#617583', size: 12}}] : [];
    return {data: [...slab.traces, ...(state.showEvents ? [eventTrace(events, 'Earthquakes', 2, null, null, maxDepth, .9),
      ...selectedTrace(events, state, 2, null, null, maxDepth)] : [])],
      layout: {...BASE, paper_bgcolor: '#ffffff', plot_bgcolor: '#ffffff', font: {...BASE.font, color: '#304958'},
        margin: {l: 62, r: 22, t: 35, b: 52}, uirevision: state.profile ? 'section-' +
        JSON.stringify([state.profile.points[0], state.profile.points[state.profile.points.length-1]]) : 'section-empty',
        xaxis: {title: {text: state.activeLine===-1?'Distance along profile (km)':
          'Distance along '+label+'–'+label+'′ (km)'}, range: [0, length], gridcolor: '#e5ecef', zerolinecolor: '#b6c7d0'},
        yaxis: {title: {text: 'Depth (km)'}, range: [maxDepth, 0], gridcolor: '#e5ecef', zerolinecolor: '#b6c7d0'},
        annotations: [...slab.annotations, ...empty, ...(state.activeLine===-1 ? [] :
          [{xref: 'paper', yref: 'paper', x: 0, y: 1.1, text: label, showarrow: false},
           {xref: 'paper', yref: 'paper', x: 1, y: 1.1, text: label+'′', showarrow: false}])], dragmode: 'pan'}, config: {...CONFIG}};
  }
  return {build3D, buildSection, depthLimit, magnitudeSize: size, depthColors: DEPTH_COLORS};
});
